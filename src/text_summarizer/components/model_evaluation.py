
import evaluate
import pandas as pd
import torch
from datasets import load_from_disk
from tqdm import tqdm
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from src.text_summarizer.entity import ModelEvaluationConfig


class ModelEvaluation:
    def __init__(self, config: ModelEvaluationConfig) -> None:
        self.config = config

    def generate_batch_sized_chunks(self, list_of_elements: list, batch_size: int) -> iter:
        """
        Split the dataset into smaller batches that we can process simultaneously
        Yield successive batch-sized chunks from list_of_elements.
        """
        for i in range(0, len(list_of_elements), batch_size):
            yield list_of_elements[i : i + batch_size]

    def calculate_metric_on_test_ds(
        self,
        dataset: dict,
        metric,
        model,
        tokenizer,
        batch_size: int = 16,
        device: str = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu",
        column_text: str = "article",
        column_summary: str = "highlights",
    ):
        article_batches = list(self.generate_batch_sized_chunks(dataset[column_text], batch_size))
        target_batches = list(self.generate_batch_sized_chunks(dataset[column_summary], batch_size))

        for article_batch, target_batch in tqdm(
            zip(article_batches, target_batches, strict=False), total=len(article_batches)):
            inputs = tokenizer(
                article_batch,
                max_length=1024,
                truncation=True,
                padding="max_length",
                return_tensors="pt",
            )

            inputs = {
                k: v.to(device) for k, v in inputs.items()
            }  # Move tensors to device

            summaries = model.generate(
                input_ids=inputs["input_ids"].to(device),
                attention_mask=inputs["attention_mask"].to(device),
                length_penalty=0.8,
                num_beams=4,
                max_length=96,
            )
            """ parameter for length penalty ensures that the model does not generate sequences that are too long. """

            # Finally, we decode the generated texts,
            # Replace the  token, and add the decoded texts with the references to the metric.
            decoded_summaries = [
                tokenizer.decode(
                    s, skip_special_tokens=True, clean_up_tokenization_spaces=True,
                )
                for s in summaries
            ]

            decoded_summaries = [d.replace("", " ") for d in decoded_summaries]

            metric.add_batch(predictions=decoded_summaries, references=target_batch)

        #  Finally compute and return the ROUGE scores.
        score = metric.compute()
        return score

    def evaluate(self) -> None:
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
        print(f"Using device: {device}")

        tokenizer = AutoTokenizer.from_pretrained(self.config.tokenizer_path)
        model_pegasus = AutoModelForSeq2SeqLM.from_pretrained(self.config.model_path).to(device)

        # Load preprocessed dataset
        dataset_samsum_pt = load_from_disk(self.config.data_path)

        rouge_names = ["rouge1", "rouge2", "rougeL", "rougeLsum"]
        rouge_metric = evaluate.load("rouge")

        score = self.calculate_metric_on_test_ds(
            dataset_samsum_pt["test"][0:10],
            rouge_metric,
            model_pegasus,
            tokenizer,
            batch_size=2,
            column_text="dialogue",
            column_summary="summary",
        )

        # Directly use the scores without accessing fmeasure or mid
        rouge_dict = {rn: score[rn] for rn in rouge_names}

        df = pd.DataFrame(rouge_dict, index=["pegasus"])
        df.to_csv(self.config.metric_file_name, index=False)
