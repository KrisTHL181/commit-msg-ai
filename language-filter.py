import argparse

try:
    import orjson as json
except ImportError:
    import json

import langdetect


def filter_language(input_file: str, output_file: str, min_length: int = 3, target_langs: list = None):
    if target_langs is None:
        target_langs = ["en"]
    target_langs = set(target_langs)

    with open(input_file, "r") as f_in, open(output_file, "w") as f_out:
        for line in f_in:
            sample = json.loads(line)
            commit_msg = sample.get("target", "").strip()
            if not commit_msg or (min_length > 0 and len(commit_msg) < min_length):
                continue
            try:
                lang = langdetect.detect(commit_msg)
            except langdetect.lang_detect_exception.LangDetectException:
                continue
            if lang in target_langs:
                f_out.write(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Filter JSONL commit data by language.")
    parser.add_argument("input_file", help="Input JSONL file")
    parser.add_argument("output_file", help="Output JSONL file")
    parser.add_argument(
        "--min-length", type=int, default=3, help="Minimum commit message length (inclusive), 0 = unlimited"
    )
    parser.add_argument(
        "--target-lang",
        type=str,
        nargs="+",
        default=["en"],
        help="One or more target languages to filter (default: en). " "Example: --target-lang en zh ja",
    )

    args = parser.parse_args()

    filter_language(args.input_file, args.output_file, args.min_length, args.target_lang)
