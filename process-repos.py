#!/usr/bin/env python3
import os
import argparse
import subprocess
import concurrent.futures
import re
import sys
from tqdm import tqdm

try:
    import orjson

    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False

import json

DEFAULTS = {
    "repos_dir": "repos",
    "output_dir": "commit_data",
    "max_commits": 1000,
    "max_diff_size": 50000,
    "max_contrib_size": 10000,
    "threads": 4,
}

REGEX_FILTER_MERGE = re.compile(r"^[Mm]erge\s")
REGEX_FILTER_REVERT = re.compile(r"^[Rr]evert\s")
BOT_PATTERN = re.compile(r"\b(?:bot|robot)\b|\[bot\]", re.IGNORECASE)


def clamp(x, min_val, max_val):
    return max(min_val, min(x, max_val))


def serialize_json(data):
    """Serializes data using orjson or standard json fallback."""
    if HAS_ORJSON:
        return orjson.dumps(data) + b"\n"
    else:
        import json

        return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


def clean_message(msg):
    """Sanitizes commit subjects."""
    if not msg:
        return ""
    lines = msg.strip().split("\n")
    subject = lines[0].strip()
    subject = re.sub(r"(?i)\b(fixes|closes|resolves|related|addresses?)\s*#[0-9]+\b", "", subject)
    subject = re.sub(r"\s*\(#[0-9]+\)", "", subject)
    subject = re.sub(r"\b#[0-9]+\b", "", subject)
    subject = re.sub(r"\s+", " ", subject)
    return subject.strip().strip(".,;:!?")


def get_repo_metadata(repo_path, include_license=False):
    """Extracts metadata; ignores external tool failures but reports them."""
    meta = {"license": "Unknown"}
    try:
        remote_out = subprocess.check_output(
            ["git", "remote", "-v"], cwd=repo_path, stderr=subprocess.DEVNULL, text=True
        )
        fetch = next((line.split()[1] for line in remote_out.splitlines() if "(fetch)" in line), None)
        if fetch:
            meta["repo_source"] = fetch
    except (subprocess.CalledProcessError, IndexError, FileNotFoundError) as e:
        print(f"  ⚠️  Could not get remotes for {repo_path}: {e}")

    if include_license:
        try:
            lic_out = subprocess.run(
                ["licensee", "detect", "--json", repo_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            lic_data = json.loads(lic_out.stdout)
            lic = lic_data.get("licenses", [])
            if not lic:
                meta["license"] = "No License"
            else:
                meta["license"] = lic[0].get("spdx_id") or lic[0].get("key", "Unknown")
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
            meta["license"] = f"Detection Failed: {type(e).__name__}"

    contrib_content = ""
    for name in ["CONTRIBUTING.md", ".github/CONTRIBUTING.md", "STYLEGUIDE.md"]:
        fpath = os.path.join(repo_path, name)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", errors="ignore") as f:
                    contrib_content = f.read()
                break
            except (OSError, UnicodeDecodeError) as e:
                print(f"  ⚠️  Error reading {fpath}: {e}")

    return meta, contrib_content


def process_repo(repo_path, args):
    """Processes a repo. Uses history slicing for context to avoid subshell overhead."""
    # Logic to handle path resolution
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return f"Skipped: {repo_path} (No .git folder)"

    repo_name = os.path.basename(repo_path)
    output_file = os.path.join(args.output_dir, f"{repo_name}.jsonl")

    meta, contrib_content = get_repo_metadata(repo_path, include_license=args.include_license)
    if len(contrib_content) > args.max_contrib_size:
        contrib_content = contrib_content[: args.max_contrib_size] + "...TRUNCATED"

    # Fetch Hash, Author, and raw Subject
    cmd = ["git", "log", f"-n{args.max_commits + 5}", "--format=%H%x00%an%x00%s%x00PRE_END_COMMIT", "--no-merges"]
    try:
        log_output = subprocess.check_output(cmd, cwd=repo_path, stderr=subprocess.DEVNULL, text=True, errors="replace")
    except subprocess.CalledProcessError as e:
        return f"Error running git log in {repo_name}: {e}"

    raw_entries = [e for e in log_output.split("PRE_END_COMMIT\n") if e.strip()]

    history_lines = []
    processed_commits = []
    for entry in raw_entries:
        parts = entry.split("\0")
        if len(parts) < 3:
            continue
        h, auth, msg = parts[0].strip(), parts[1].strip(), parts[2].strip()
        history_lines.append(f"{h[:7]} {msg}")
        processed_commits.append({"hash": h, "author": auth, "raw_msg": msg})

    count = 0
    try:
        with open(output_file, "wb") as f:
            for i in range(min(len(processed_commits), args.max_commits)):
                c_info = processed_commits[i]
                commit_msg = c_info["raw_msg"]

                # Skip Merge/Revert
                if (
                    REGEX_FILTER_MERGE.match(commit_msg)
                    or REGEX_FILTER_REVERT.match(commit_msg)
                    or commit_msg.startswith(("squash!", "fixup!"))
                ):
                    continue

                if args.skip_bot_commits and BOT_PATTERN.search(c_info["author"]):
                    continue

                recent_context = "\n".join(history_lines[i + 1 : i + 6])
                try:
                    diff_text = subprocess.check_output(
                        ["git", "show", "--format=", "--no-color", c_info["hash"]],
                        cwd=repo_path,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        errors="replace",
                    )
                    if len(diff_text) > args.max_diff_size:
                        diff_text = diff_text[: args.max_diff_size] + "...TRUNCATED"

                    files_out = subprocess.check_output(
                        ["git", "show", "--name-only", "--format=", c_info["hash"]],
                        cwd=repo_path,
                        text=True,
                        stderr=subprocess.DEVNULL,
                    )
                    affected_files = [line for line in files_out.splitlines() if line.strip()]

                    entry = {
                        "commit_msg": clean_message(commit_msg),
                        "change": diff_text,
                        "recent_commits_message": recent_context,
                        "license": meta["license"],
                        "code_style": contrib_content,
                        "affected_files": affected_files,
                    }
                    if args.mark_source and "repo_source" in meta:
                        entry["repo_source"] = meta["repo_source"]

                    f.write(serialize_json(entry))
                    count += 1
                except subprocess.CalledProcessError:
                    continue
    except OSError as e:
        return f"File Error for {repo_name}: {e}"

    return f"✅ Extracted {count} commits from {repo_name}"


def main():
    parser = argparse.ArgumentParser(description="Structured Git data extractor.")
    parser.add_argument(
        "--repos-dir",
        "-r",
        default=DEFAULTS["repos_dir"],
        help=f"Path to the directory containing cloned Git repositories (default: '{DEFAULTS['repos_dir']}')",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        default=DEFAULTS["output_dir"],
        help=f"Directory where the resulting .jsonl files will be saved (default: '{DEFAULTS['output_dir']}')",
    )

    parser.add_argument(
        "--max-commits",
        "-m",
        type=int,
        default=DEFAULTS["max_commits"],
        help=f"Maximum number of commits to extract from each repository (default: {DEFAULTS['max_commits']})",
    )

    parser.add_argument(
        "--max-diff-size",
        "-d",
        type=int,
        default=DEFAULTS["max_diff_size"],
        help=f"Threshold in bytes to truncate commit diffs. Prevents excessively large JSON objects (default: {DEFAULTS['max_diff_size']})",
    )

    parser.add_argument(
        "--max-contrib-size",
        "-c",
        type=int,
        default=DEFAULTS["max_contrib_size"],
        help=f"Threshold in bytes to truncate the CONTRIBUTING/Styleguide file content (default: {DEFAULTS['max_contrib_size']})",
    )

    parser.add_argument(
        "--threads",
        "-t",
        type=int,
        default=DEFAULTS["threads"],
        help=f"Number of repositories to process concurrently using a ThreadPool (default: {DEFAULTS['threads']})",
    )

    parser.add_argument(
        "--skip-bot-commits",
        "-b",
        action="store_true",
        help="If set, skips commits where the author name contains 'bot' or 'robot' (case-insensitive)",
    )

    parser.add_argument(
        "--mark-source",
        "-s",
        action="store_true",
        help="Include the repository's remote fetch URL in every JSONL entry for traceability",
    )

    parser.add_argument(
        "--include-license",
        action="store_true",
        help="Attempt to detect the project license using the 'licensee' command-line tool. Requires 'licensee' to be installed.",
    )

    args = parser.parse_args()

    args.max_commits = clamp(args.max_commits, 1, 2147483647 - 5)

    abs_base = os.path.abspath(args.repos_dir)
    print(f"Looking for repositories in: {abs_base}")

    if not os.path.exists(abs_base):
        print(f"❌ Error: {abs_base} does not exist.")
        sys.exit(1)

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    # Resolve subdirectories
    subdirs = [os.path.join(abs_base, d) for d in os.listdir(abs_base) if os.path.isdir(os.path.join(abs_base, d))]
    repos = [d for d in subdirs if os.path.isdir(os.path.join(d, ".git"))]

    print(f"Found {len(subdirs)} subdirectories, {len(repos)} valid Git repos.")

    if not repos:
        print("❌ No valid Git repositories found. Stopping.")
        sys.exit(1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {executor.submit(process_repo, r, args): r for r in repos}
        for future in tqdm(
            concurrent.futures.as_completed(futures),
            total=len(futures),
            desc="Processing repos",
            file=sys.stderr
        ):
            try:
                result = future.result()
                if result:
                    tqdm.write(result)
            except Exception as e:
                print(f"❌ Critical Thread Failure: {e}")


if __name__ == "__main__":
    main()
