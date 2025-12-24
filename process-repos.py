#!/usr/bin/env python3
import os
import argparse
import subprocess
import concurrent.futures
import re
import sys
from tqdm import tqdm
import pygit2

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


def get_empty_tree(repo):
    builder = repo.TreeBuilder()
    return repo[builder.write()]

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
        repo = pygit2.Repository(repo_path)
        for remote in repo.remotes:
            if remote.url:
                meta["repo_source"] = remote.url
                break
    except (pygit2.GitError, KeyError) as e:
        print(f"  ⚠️  Could not get remotes for {repo_path}: {type(e).__name__}")

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

def get_commit_diff_and_files(repo, commit, max_diff_size):
    diff_text = []
    affected_files = set()

    if commit.parents:
        diff = repo.diff(commit.parents[0].tree, commit.tree)
    else:
        empty_tree = get_empty_tree(repo)
        diff = repo.diff(empty_tree, commit.tree)

    for patch in diff:
        if patch.delta.new_file:
            affected_files.add(patch.delta.new_file.path)
        if patch.delta.old_file:
            affected_files.add(patch.delta.old_file.path)

        diff_text.append(patch.text)

        if sum(len(d) for d in diff_text) > max_diff_size:
            break

    joined = "".join(diff_text)
    if len(joined) > max_diff_size:
        joined = joined[:max_diff_size] + "...TRUNCATED"

    return joined, sorted(affected_files)

def process_repo(repo_path, args):
    repo_path = os.path.abspath(repo_path)
    try:
        repo = pygit2.Repository(repo_path)
    except (pygit2.GitError, KeyError, OSError):
        return f"Skipped: {repo_path} (Not a valid git repo)"

    repo_name = os.path.basename(repo_path)
    output_file = os.path.join(args.output_dir, f"{repo_name}.jsonl")

    meta, contrib_content = get_repo_metadata(repo_path, args.include_license)
    if len(contrib_content) > args.max_contrib_size:
        contrib_content = contrib_content[: args.max_contrib_size] + "...TRUNCATED"

    walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
    walker.hide(repo.head.target)

    commits = []
    history_lines = []

    for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_TIME):
        if len(commit.parents) > 1:
            continue  # no merges

        msg = commit.message.split("\n", 1)[0]
        history_lines.append(f"{str(commit.id)[:7]} {msg}")

        commits.append(commit)
        if len(commits) >= args.max_commits + 5:
            break

    count = 0
    try:
        with open(output_file, "wb") as f:
            for i, commit in enumerate(commits[: args.max_commits]):
                msg = commit.message.strip()

                if (
                    REGEX_FILTER_MERGE.match(msg)
                    or REGEX_FILTER_REVERT.match(msg)
                    or msg.startswith(("squash!", "fixup!"))
                ):
                    continue

                author_name = commit.author.name or ""
                if args.skip_bot_commits and BOT_PATTERN.search(author_name):
                    continue

                recent_context = "\n".join(history_lines[i + 1 : i + 6])

                diff_text, affected_files = get_commit_diff_and_files(
                    repo, commit, args.max_diff_size
                )

                entry = {
                    "commit_msg": clean_message(msg),
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

    with concurrent.futures.ProcessPoolExecutor(max_workers=args.threads) as executor:
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
