#!/usr/bin/env python3
import argparse
from pathlib import Path

try:
    import orjson as json  # way faster than standard json module

    json._dumps = json.dumps
    json.dumps = lambda content: json._dumps(content).decode("utf-8")
except ImportError:
    import json


task_instruction = """You are an AI assistant that writes concise, high-quality Git commit messages.
Task: Refer to the information provided, and then write a concise, imperative commit message describing the change.\n\n"""

def format_prompt(sample, min_length: int = 3, add_instruction_prompt: bool = True):
    parts = []

    # 1. Affected files
    affected = sample.get("affected_files", [])
    affected_str = ", ".join(affected) if affected else "(none)"
    parts.append(f"Affected files: {affected_str}")

    # 2. Change
    change = sample.get("change", "(none)").strip()
    parts.append(f"Diff (code changes): {change}")

    # 3. Recent commits message
    recent = sample.get("recent_commits_message", "(none)").strip()
    parts.append(f"Recent commit examples: {recent}")

    # 4. Code style
    code_style = sample.get("code_style", "").strip()
    if code_style:
        parts.append(f"Code style guidelines: {code_style}")
    else:
        parts.append("Code style guidelines: (not specified)")

    # 5. Commit message header
    parts.append("Commit message:")

    prompt = "\n".join(parts)
    target = sample.get("commit_msg", "").strip()

    if not target or len(target) < min_length:
        return None

    prompt = task_instruction + prompt if add_instruction_prompt else prompt
    return prompt, target


def main():
    parser = argparse.ArgumentParser(description="Convert JSONL commit data to LLM training format.")
    parser.add_argument("input_dir", help="Directory containing .jsonl files")
    parser.add_argument("output_file", type=str, default="samples.jsonl", help="Output text file for LLM training")
    parser.add_argument(
        "--min-length", type=int, default=3, help="Minimum commit message length (inclusive), 0 = unlimited"
    )
    args = parser.parse_args()

    if args.max_length == 0:
        args.max_length = float("inf")

    input_dir = Path(args.input_dir)
    output_file = Path(args.output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total_samples = 0
    written_samples = 0

    with open(output_file, "w", encoding="utf-8") as out_f:
        for jsonl_path in input_dir.glob("*.jsonl"):
            with open(jsonl_path, "r", encoding="utf-8") as in_f:
                for line in in_f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sample = json.loads(line)
                        total_samples += 1

                        formatted = format_prompt(sample, args.min_length)
                        if formatted is None:
                            continue

                        out_f.write(json.dumps({"prompt": formatted[0], "target": formatted[1]}))
                        out_f.write("\n")
                        written_samples += 1

                    except json.JSONDecodeError:
                        continue

    print(f"Processed {total_samples} samples, wrote {written_samples} to {output_file}")


if __name__ == "__main__":
    main()
