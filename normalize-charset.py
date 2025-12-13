#!/usr/bin/env python3
"""
Normalize text encoding and characters in JSONL files for commit message preprocessing.

Supports:
- Unicode normalization (NFC, NFD, NFKC, NFKD)
- Full-width to half-width ASCII conversion (optional)
- Invisible character cleanup
- Line ending standardization
"""

import argparse
import re
import sys
import unicodedata

try:
    import orjson as json
except ImportError:
    import json


def clean_invisible_chars(text: str) -> str:
    """Remove problematic invisible characters while preserving whitespace and common CJK."""
    # Keep: spaces, basic punctuation, CJK, Arabic, Cyrillic, etc.
    # Remove: control chars, private use, formatting marks (except ZWJ/ZWNJ if needed)
    return re.sub(
        r"[^\u0009\u000A\u0020-\u007E\u00A0\u2000-\u200F\u2028-\u202F"
        r"\u3000-\u303F\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF"
        r"\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF"
        r"\u0600-\u06FF\u0400-\u04FF\u2060-\u206F]",
        "",
        text,
    )


def to_halfwidth_ascii(text: str) -> str:
    """Convert full-width ASCII characters to half-width."""
    return unicodedata.normalize("NFKC", text)


def normalize_text(
    text: str,
    unicode_norm: str = "NFC",
    halfwidth: bool = False,
    clean_invisible: bool = True,
) -> str:
    """Apply full text normalization pipeline."""
    if not isinstance(text, str) or not text.strip():
        return text if isinstance(text, str) else ""

    # Step 1: Unicode normalization
    if unicode_norm in ("NFC", "NFD", "NFKC", "NFKD"):
        text = unicodedata.normalize(unicode_norm, text)

    # Step 2: Full-width to half-width (only affects ASCII range)
    if halfwidth:
        text = to_halfwidth_ascii(text)

    # Step 3: Remove harmful invisible characters
    if clean_invisible:
        text = clean_invisible_chars(text)

    # Step 4: Standardize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Step 5: Collapse multiple spaces (preserving newlines)
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


def process_jsonl(
    input_path: str,
    output_path: str,
    text_fields: list,
    unicode_norm: str,
    halfwidth: bool,
    clean_invisible: bool,
):
    """Process JSONL file line by line."""
    with open(input_path, "r", encoding="utf-8") as fin, open(output_path, "w", encoding="utf-8") as fout:
        for line_num, line in enumerate(fin, 1):
            try:
                obj = json.loads(line)
                for field in text_fields:
                    if field in obj and isinstance(obj[field], str):
                        obj[field] = normalize_text(
                            obj[field],
                            unicode_norm=unicode_norm,
                            halfwidth=halfwidth,
                            clean_invisible=clean_invisible,
                        )
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            except Exception as e:
                print(
                    f"Warning: Skipping line {line_num} due to error: {e}",
                    file=sys.stderr,
                )


def main():
    parser = argparse.ArgumentParser(description="Normalize character encoding in JSONL files for commit messages.")
    parser.add_argument("input_file", help="Input JSONL file")
    parser.add_argument("output_file", help="Output JSONL file")
    parser.add_argument(
        "--fields",
        nargs="+",
        default=["commit_msg", "change", "recent_commits_message", "code_style"],
        help="Text fields to normalize (default: commit_msg change recent_commits_message code_style)",
    )
    parser.add_argument(
        "--unicode-norm",
        choices=["NFC", "NFD", "NFKC", "NFKD"],
        default="NFC",
        help="Unicode normalization form (default: NFC)",
    )
    parser.add_argument(
        "--halfwidth",
        action="store_true",
        help="Convert full-width ASCII to half-width (e.g., ＡＢＣ → ABC)",
    )
    parser.add_argument(
        "--no-clean-invisible",
        action="store_false",
        dest="clean_invisible",
        help="Skip cleaning invisible characters",
    )
    parser.add_argument("--debug", action="store_true", help="Print first few normalized samples")

    args = parser.parse_args()

    if args.debug:
        # Test normalization on sample strings
        samples = [
            "Ｈｅｌｌｏ, ｗｏｒｌｄ！\u200b",
            "café\u0301 vs café",
            "File:　ｈｅｌｌｏ.py　(全角スペース)",
        ]
        print("Debug: Normalization examples")
        for s in samples:
            norm = normalize_text(
                s,
                unicode_norm=args.unicode_norm,
                halfwidth=args.halfwidth,
                clean_invisible=args.clean_invisible,
            )
            print(f"IN : {repr(s)}")
            print(f"OUT: {repr(norm)}\n")

    process_jsonl(
        input_path=args.input_file,
        output_path=args.output_file,
        text_fields=args.fields,
        unicode_norm=args.unicode_norm,
        halfwidth=args.halfwidth,
        clean_invisible=args.clean_invisible,
    )
    print(f"Normalized {args.input_file} -> {args.output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
