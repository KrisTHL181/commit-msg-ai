#!/usr/bin/env python3
import json
import argparse
from datasets import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
import torch

def load_dataset(data_path, tokenizer):
    data = []
    with open(data_path, "r") as f:
        for line in f:
            sample = json.loads(line)
            data.append({
                "prompt": sample["prompt"],
                "target": sample["target"].strip() + (tokenizer.eos_token or "")
            })
    return Dataset.from_list(data)

def tokenize_function(examples, tokenizer, max_length):
    batch_input_ids = []
    batch_attention_mask = []
    batch_labels = []

    bos_token_id = tokenizer.bos_token_id
    eos_token_id = tokenizer.eos_token_id
    pad_token_id = tokenizer.pad_token_id

    for prompt, target in zip(examples["prompt"], examples["target"]):
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
        if eos_token_id is not None:
            target_ids.append(eos_token_id)

        total_len = len(prompt_ids) + len(target_ids)
        if bos_token_id is not None:
            total_len += 1

        if total_len > max_length:
            excess = total_len - max_length
            if excess >= len(prompt_ids):
                # Fallback: truncate target (should be rare)
                prompt_ids = []
                max_target_len = max_length - (1 if bos_token_id else 0)
                target_ids = target_ids[:max_target_len]
            else:
                # Truncate prompt from the LEFT
                prompt_ids = prompt_ids[excess:]

        input_ids = []
        if bos_token_id is not None:
            input_ids.append(bos_token_id)
        input_ids.extend(prompt_ids)
        input_ids.extend(target_ids)

        # Pad to max_length
        if len(input_ids) < max_length:
            attention_mask = [1] * len(input_ids) + [0] * (max_length - len(input_ids))
            input_ids = input_ids + [pad_token_id] * (max_length - len(input_ids))
        else:
            attention_mask = [1] * max_length
            input_ids = input_ids[:max_length]

        # Build labels
        labels = input_ids.copy()
        prompt_len = len(prompt_ids) + (1 if bos_token_id is not None else 0)
        for i in range(prompt_len):
            labels[i] = -100
        for i in range(len(input_ids)):
            if attention_mask[i] == 0:
                labels[i] = -100

        batch_input_ids.append(input_ids)
        batch_attention_mask.append(attention_mask)
        batch_labels.append(labels)

    return {
        "input_ids": batch_input_ids,
        "attention_mask": batch_attention_mask,
        "labels": batch_labels,
    }

def main():
    parser = argparse.ArgumentParser(description="Train LoRA for commit message generation with optional validation.")
    # Model & Tokenizer
    parser.add_argument("--model_name", type=str, default="meta-llama/Llama-3-8b-hf")
    parser.add_argument("--max_length", type=int, default=2048)

    # Data
    parser.add_argument("--data_path", type=str, required=True, help="Path to training JSONL file.")
    parser.add_argument("--eval_data_path", type=str, default=None, help="Optional path to validation JSONL file.")

    # LoRA
    parser.add_argument("--lora_r", type=int, default=8)
    parser.add_argument("--lora_alpha", type=int, default=32)
    parser.add_argument("--lora_dropout", type=float, default=0.05)
    parser.add_argument(
        "--lora_modules",
        nargs="+",
        default=["q_proj", "k_proj", "v_proj", "o_proj"],
        help="List of module names to apply LoRA to (e.g., q_proj k_proj gate_proj)."
    )

    # Training
    parser.add_argument("--output_dir", type=str, default="./commit-lora")
    parser.add_argument("--per_device_train_batch_size", type=int, default=4)
    parser.add_argument("--per_device_eval_batch_size", type=int, default=4)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=4)
    parser.add_argument("--learning_rate", type=float, default=2e-5)
    parser.add_argument("--num_train_epochs", type=int, default=3)
    parser.add_argument("--warmup_ratio", type=float, default=0.03)
    parser.add_argument("--lr_scheduler_type", type=str, default="cosine")
    parser.add_argument("--logging_steps", type=int, default=10)
    parser.add_argument("--save_strategy", type=str, default="epoch")
    parser.add_argument("--eval_strategy", type=str, default="epoch", help="Only used if --eval_data_path is provided.")
    parser.add_argument("--eval_steps", type=int, default=None, help="If None, defaults to logging_steps.")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--fourbit", action="store_true")
    parser.add_argument("--optim", type=str, default="paged_adamw_8bit")

    # Save
    parser.add_argument("--final_save_path", type=str, default="commit-message-lora")

    args = parser.parse_args()

    # ===== Tokenizer =====
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    if tokenizer.pad_token is None:
        tokenizer.add_special_tokens({'pad_token': '[PAD]'})

    # ===== Load datasets =====
    train_dataset = load_dataset(args.data_path, tokenizer)
    eval_dataset = None
    if args.eval_data_path:
        eval_dataset = load_dataset(args.eval_data_path, tokenizer)

    # ===== Tokenize =====
    def tokenize_fn(examples):
        return tokenize_function(examples, tokenizer, args.max_length)
    tokenized_train = train_dataset.map(tokenize_fn, batched=True, remove_columns=train_dataset.column_names)
    tokenized_eval = None
    if eval_dataset is not None:
        tokenized_eval = eval_dataset.map(tokenize_fn, batched=True, remove_columns=eval_dataset.column_names)

    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        load_in_4bit=args.fourbit,
        device_map="auto",
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
    )
    model = prepare_model_for_kbit_training(model)
    model.resize_token_embeddings(len(tokenizer))

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=args.lora_modules,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM"
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    # ===== Training Args =====
    eval_strategy = "no"
    if tokenized_eval is not None:
        eval_strategy = args.evaluation_strategy

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        num_train_epochs=args.num_train_epochs,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type=args.lr_scheduler_type,
        logging_steps=args.logging_steps,
        save_strategy=args.save_strategy,
        eval_strategy=eval_strategy,
        metric_for_best_model="eval_loss",
        eval_steps=args.eval_steps or args.logging_steps,
        fp16=args.fp16,
        bf16=args.bf16,
        optim=args.optim,
        report_to="none",
        load_best_model_at_end=True,
    )

    # ===== Trainer =====
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_train,
        eval_dataset=tokenized_eval,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )

    # ===== Train =====
    trainer.train()

    # ===== Save =====
    model.save_pretrained(args.final_save_path)
    tokenizer.save_pretrained(args.final_save_path)

if __name__ == "__main__":
    main()
