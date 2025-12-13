# Commit Message Generator (LLM Fine-Tuning)

This project provides an end-to-end pipeline for **automatically generating high-quality Git commit messages** by fine-tuning a Large Language Model (LLM) using real-world Git repository data.

It covers:

* Discovering and cloning popular GitHub repositories
* Extracting and normalizing commit-related data
* Converting commits into LLM-friendly prompt/target pairs
* Filtering and cleaning datasets
* Fine-tuning an LLM using **LoRA (Low-Rank Adaptation)** for efficient training

The final result is a lightweight LoRA adapter that can generate concise, imperative commit messages based on code diffs and repository context.

---

## Pipeline Overview

```text
GitHub Search
   ↓
Clone Repositories
   ↓
Extract Commit Data (JSONL)
   ↓
Normalize & Filter
   ↓
Convert to LLM Prompts
   ↓
LoRA Fine-Tuning
```

---

## Repository Structure

```text
.
├── clone-repos.sh           # Clone repositories in parallel
├── scraping-repo-list.sh   # Fetch top GitHub repositories by language
├── exclude-repos.sh        # Manually exclude problematic repositories
├── process-repos.sh        # Extract structured commit data from repos
├── normalize-charset.py    # Normalize Unicode and clean text
├── language-filter.py      # Filter commit messages by language
├── sequentize-for-llm.py   # Convert commit data to LLM prompt/target pairs
├── finetune-via-lora.py    # Fine-tune an LLM with LoRA
└── repos/                  # Cloned repositories (generated)
```

---

## Requirements

### System Tools

* `git`
* `jq`
* `curl`
* `GNU parallel`

### Python

* Python 3.9+
* `transformers`
* `datasets`
* `peft`
* `torch`
* `langdetect`
* `orjson` (optional, recommended)

---

## Step-by-Step Usage

### 1. Fetch Repository Lists from GitHub

Search for popular repositories by language and stars:

```bash
./scraping-repo-list.sh <max_pages> <lang1,lang2,...> --min-stars 100
```

Example:

```bash
./scraping-repo-list.sh 5 python,cpp --min-stars 500
```

This generates a text file containing `owner/repo` entries.

> Optionally exclude repositories by adding them to `exclude-repos.sh`.

---

### 2. Clone Repositories

Clone repositories in parallel:

```bash
./clone-repos.sh repo_list.txt 8
```

All repositories will be cloned into the `repos/` directory.

---

### 3. Extract Commit Data

Convert Git history into structured JSONL files:

```bash
./process-repos.sh -r repos -o commit_data -m 1000 -t 8
```

Each repository produces one `.jsonl` file containing:

* Cleaned commit message
* Code diff (truncated)
* Recent commit history
* Code style guidelines (if available)
* Affected files

---

### 4. Normalize Text Encoding

Normalize Unicode and clean invisible characters:

```bash
python normalize-charset.py commit_data repo_data_normalized
```

This step ensures consistent encoding across multilingual repositories.

---

### 5. Filter by Language

Keep only commits in specific languages (default: English):

```bash
python language-filter.py input.jsonl output.jsonl --target-lang en
```

---

### 6. Convert to LLM Training Format

Generate prompt/target pairs suitable for causal language modeling:

```bash
python sequentize-for-llm.py commit_data samples.jsonl
```

Each sample contains:

* A structured prompt (diff, affected files, history, style)
* A target commit message

---

### 7. Fine-Tune with LoRA

Train a LoRA adapter on top of a base LLM:

```bash
python finetune-via-lora.py \
  --model_name meta-llama/Llama-3-8b-hf \
  --data_path samples.jsonl \
  --fourbit \
  --bf16 \
  --output_dir ./output \
  --final_save_path ./commit-message-lora
```

Key features:

* 4-bit or 8-bit training support
* Configurable LoRA target modules
* Optional validation dataset
* Efficient training on limited GPU memory

---

## Prompt Design

Each prompt includes:

* Affected files
* Code diff
* Recent commit examples
* Code style guidelines

The model is instructed to generate a  **concise, imperative commit message** .

---

## Output

The final output is a LoRA adapter directory containing:

* LoRA weights
* Tokenizer configuration

This adapter can be merged with or loaded alongside the base model for inference.

---

## Notes & Best Practices

* Avoid merge, revert, squash, and fixup commits (filtered automatically)
* Large diffs and style files are truncated for efficiency
* Use diverse repositories for better generalization
* Always validate data quality before training

---

## License

This project is intended for research and educational purposes. Please ensure that your use of GitHub data complies with repository licenses and GitHub's Terms of Service.

---

## Acknowledgements

* Hugging Face Transformers & Datasets
* PEFT / LoRA
* GNU Parallel
* The open-source GitHub community
