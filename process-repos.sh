#!/bin/bash
# Extract structured commit data from local Git repositories into JSONL format.
# Supports parallel processing via --threads.

set -euo pipefail

# Default values
DEFAULT_REPOS_DIR="repos"
DEFAULT_OUTPUT_DIR="commit_data"
DEFAULT_MAX_COMMITS=1000
DEFAULT_MAX_DIFF_SIZE=50000         # 50 KB
DEFAULT_MAX_CONTRIBUTING_SIZE=10000 # 10 KB
DEFAULT_THREADS=4
DEFAULT_SKIP_BOT_COMMITS=false
DEFAULT_MARK_SOURCE=false
DEFAULT_INCLUDE_LICENSE=false

show_help() {
	cat <<EOF
Usage: $0 [OPTIONS]

Extract commit history from local Git repositories into structured JSONL files.

OPTIONS:
  -r, --repos-dir DIR          Path to directory containing cloned repositories (default: '$DEFAULT_REPOS_DIR')
  -o, --output-dir DIR         Output directory for JSONL files (default: '$DEFAULT_OUTPUT_DIR')
  -m, --max-commits N          Maximum number of commits to extract per repository (default: $DEFAULT_MAX_COMMITS)
  -d, --max-diff-size BYTES    Truncate diffs larger than this (default: $DEFAULT_MAX_DIFF_SIZE bytes)
  -c, --max-contrib-size BYTES Truncate CONTRIBUTING.md larger than this (default: $DEFAULT_MAX_CONTRIBUTING_SIZE bytes)
  -t, --threads N              Number of parallel threads (default: $DEFAULT_THREADS)
  -b, --skip-bot-commits       Skip commits whose author name contains 'bot' (case-insensitive)
  -s, --mark-source            Mark commits with source repository URL
      --include-license        Include license information using 'licensee detect' (requires licensee command)
  -h, --help                   Show this help message

REQUIRES: jq, GNU parallel
OPTIONAL (when using --include-license): licensee

EXAMPLE:
  $0 -r ./my_repos -o ./dataset -m 500 -t 8 --skip-bot-commits --mark-source --include-license
EOF
}

# Parse command-line arguments
REPOS_DIR="$DEFAULT_REPOS_DIR"
OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
MAX_COMMITS="$DEFAULT_MAX_COMMITS"
MAX_DIFF_SIZE="$DEFAULT_MAX_DIFF_SIZE"
MAX_CONTRIBUTING_SIZE="$DEFAULT_MAX_CONTRIBUTING_SIZE"
THREADS="$DEFAULT_THREADS"
SKIP_BOT_COMMITS="$DEFAULT_SKIP_BOT_COMMITS"
MARK_SOURCE="$DEFAULT_MARK_SOURCE"
INCLUDE_LICENSE="$DEFAULT_INCLUDE_LICENSE"

while [[ $# -gt 0 ]]; do
	case $1 in
	-r | --repos-dir)
		REPOS_DIR="$2"
		shift 2
		;;
	-o | --output-dir)
		OUTPUT_DIR="$2"
		shift 2
		;;
	-m | --max-commits)
		MAX_COMMITS="$2"
		shift 2
		;;
	-d | --max-diff-size)
		MAX_DIFF_SIZE="$2"
		shift 2
		;;
	-c | --max-contrib-size)
		MAX_CONTRIBUTING_SIZE="$2"
		shift 2
		;;
	-t | --threads)
		THREADS="$2"
		shift 2
		;;
	-b | --skip-bot-commits)
		SKIP_BOT_COMMITS=true
		shift
		;;
	-s | --mark-source)
		MARK_SOURCE=true
		shift
		;;
	--include-license)
		INCLUDE_LICENSE=true
		shift
		;;
	-h | --help)
		show_help
		exit 0
		;;
	*)
		echo "Unknown option: $1" >&2
		show_help >&2
		exit 1
		;;
	esac
done

# Validate dependencies
if ! command -v jq &>/dev/null; then
	echo "Error: 'jq' is required but not installed." >&2
	exit 1
fi

if ! command -v parallel &>/dev/null; then
	echo "Error: 'parallel' (GNU Parallel) is required for parallel processing but not installed." >&2
	echo "Install it with: apt install parallel (Debian/Ubuntu) or brew install parallel (macOS)" >&2
	exit 1
fi

# Validate licensee if needed
if [[ "$INCLUDE_LICENSE" == true ]] && ! command -v licensee &>/dev/null; then
	echo "Error: 'licensee' is required for --include-license but not installed." >&2
	echo "Install it with: gem install licensee" >&2
	exit 1
fi

# Ensure output dir exists and get absolute path
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR_ABS=$(cd "$OUTPUT_DIR" && pwd)
readonly OUTPUT_DIR_ABS

# Sanitize filename (remove control chars and trim whitespace)
sanitize_name() {
	local input="$1"
	printf '%s' "$input" | tr -d '\0\r\n\t' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Function to process a single repo (called in parallel)
process_repo() {
	local repo_path="$1"
	local output_dir_abs="$2"
	local max_commits="$3"
	local max_diff_size="$4"
	local max_contrib_size="$5"
	local skip_bot_commits="$6"
	local mark_source="$7"
	local include_license="$8"

	if [[ ! -d "$repo_path" ]]; then
		return 0
	fi

	local raw_name=$(basename "$repo_path")
	local repo_name
	repo_name=$(sanitize_name "$raw_name")
	local output_file="$output_dir_abs/${repo_name}.jsonl"

	{
		echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Processing repository: $repo_name"
		cd "$repo_path" || {
			echo "  ⚠️ Failed to enter $repo_path" >&2
			return 1
		}

		# Skip empty repositories
		local commits
		if ! commits=$(git log --format=%H -n "$max_commits" 2>/dev/null); then
			echo "  ⚠️ Skipping empty repository: $repo_name"
			return 0
		fi

		: >"$output_file"

		# Get repository source URL
		local repo_source_json="{}"
		if [[ "$mark_source" == true ]]; then
			# Get repository source URL from remote -v output
			local fetch_url push_url
			fetch_url=$(git remote get-url origin 2>/dev/null || git remote -v 2>/dev/null | awk '/fetch/{print $2; exit}' || echo "")
			push_url=$(git remote get-url --push origin 2>/dev/null || git remote -v 2>/dev/null | awk '/push/{print $2; exit}' || echo "")
			if [[ -n "$fetch_url" || -n "$push_url" ]]; then
				repo_source_json=$(jq -n \
					--arg fetch "$fetch_url" \
					--arg push "$push_url" \
					'{
						fetch: ($fetch | select(length > 0)),
						push: ($push | select(length > 0))
					}' | jq 'with_entries(select(.value != null))')
			fi
		fi

		# Detect CONTRIBUTING.md
		local contributing_path=""
		if git ls-files --error-unmatch CONTRIBUTING.md >/dev/null 2>&1; then
			contributing_path="CONTRIBUTING.md"
		elif git ls-files --error-unmatch .github/CONTRIBUTING.md >/dev/null 2>&1; then
			contributing_path=".github/CONTRIBUTING.md"
		elif git ls-files --error-unmatch STYLEGUIDE.md >/dev/null 2>&1; then
			contributing_path="STYLEGUIDE.md"
		fi

		# Get license info if requested
		local license_info=""
		if [[ "$include_license" == true ]]; then
			# List files in root directory (tracked by git)
			local files_in_root
			files_in_root=$(git ls-files 2>/dev/null | grep -v /) || files_in_root=""

			# Try standard license filenames first
			local license_file=""
			for candidate in LICENSE LICENSE.txt LICENSE.md COPYING COPYING.txt COPYING.md; do
				if echo "$files_in_root" | grep -q "^$candidate$"; then
					license_file="$candidate"
					break
				fi
			done

			# If no standard file, search for files containing "license" or "copying" (case-insensitive)
			if [[ -z "$license_file" ]]; then
				license_file=$(echo "$files_in_root" | grep -iE '(license|copying)' | head -n1 | tr -d '\r\n')
			fi

			# Process license file if found
			if [[ -n "$license_file" ]]; then
				local license_output
				license_output=$(licensee detect --json "$license_file" 2>/dev/null || echo "{}")
				
				# Extract license using .licenses[0].key or .spdx_id, fallback to .license (old format)
				if jq -e '.licenses | length > 0' <<< "$license_output" &>/dev/null; then
					license_info=$(jq -r '
						.licenses[0].key // 
						.licenses[0].spdx_id // 
						"Unknown License"
					' <<< "$license_output")
				elif jq -e '.license' <<< "$license_output" &>/dev/null; then
					license_info=$(jq -r '
						.license.key // 
						.license.spdx_id // 
						.license.name // 
						"Unknown License"
					' <<< "$license_output")
				else
					license_info="Unknown License (file: $license_file)"
				fi

				# Normalize null/empty to "Unknown License"
				if [[ -z "$license_info" || "$license_info" == "null" ]]; then
					license_info="Unknown License"
				fi
			else
				license_info="No License"
			fi
		fi

		# Process each commit
		while IFS= read -r commit; do
			{
				# Check if commit author is a bot
				if [[ "$skip_bot_commits" == true ]]; then
					local author_name
					author_name=$(git log -1 --format=%an "$commit" 2>/dev/null)
					if echo "$author_name" | grep -qiE '\b(bot|robot)\b|\[bot\]'; then
						continue
					fi
				fi

				# 0. Commit message
				local commit_msg
				commit_msg=$(git log -1 --format=%B "$commit" 2>/dev/null | head -n1 |
					sed -E \
						-e 's/\b(fixes|closes|resolves|related|addresses?)\s*#[0-9]+\b//gi' \
						-e 's/\s*\(#[0-9]+\)//g' \
						-e 's/\b#[0-9]+\b//g' \
						-e 's/[[:space:]]+/ /g' \
						-e 's/^[[:space:]]*//; s/[[:space:]]*$//' \
						-e 's/[.,;:!?]+$//')
				if [[ $commit_msg =~ ^[Mm]erge[[:space:]] || $commit_msg =~ ^[Rr]evert[[:space:]] || $commit_msg =~ ^squash! || $commit_msg =~ ^fixup! ]]; then
					continue
				fi

				# 1. Diff
				local change
				change=$(GIT_PAGER=cat git show --no-color --format= "$commit" 2>/dev/null || echo "")
				if [[ ${#change} -gt $max_diff_size ]]; then
					change="${change:0:$max_diff_size}...TRUNCATED"
				fi

				# 2. Recent commits
				local recent_commits=""
				local parent
				if parent=$(git rev-parse "$commit"^ 2>/dev/null); then
					recent_commits=$(GIT_PAGER=cat git log --oneline -n 5 "$parent" 2>/dev/null || echo "")
				fi

				# 3. Code style
				local code_style=""
				if [[ -n "$contributing_path" ]]; then
					code_style=$(git show "$commit:$contributing_path" 2>/dev/null || echo "")
					if [[ ${#code_style} -gt $max_contrib_size ]]; then
						code_style="${code_style:0:$max_contrib_size}...TRUNCATED"
					fi
				fi

				# 4. Affected files
				local affected_files
				affected_files=$(git show --name-only --format= "$commit" 2>/dev/null | grep -v '^$' || echo "")

				# 5. Output JSON with conditional license and repo_source fields
				if [[ "$include_license" == true ]]; then
					jq -c -n \
						--arg commit_msg "$commit_msg" \
						--arg change "$change" \
						--arg recent_commits "$recent_commits" \
						--arg code_style "$code_style" \
						--arg affected_files "$affected_files" \
						--argjson repo_source "$repo_source_json" \
						--arg license_info "$license_info" \
						--arg mark_source_flag "$mark_source" \
						'{
							commit_msg: $commit_msg,
							change: ($change | gsub("\u0000"; "")),
							recent_commits_message: $recent_commits,
							code_style: $code_style,
							affected_files: ($affected_files | split("\n") | map(select(. != "")))
						}
						+ (if $mark_source_flag == "true" then {repo_source: $repo_source} else {} end)
						+ {license: $license_info}'
				else
					jq -c -n \
						--arg commit_msg "$commit_msg" \
						--arg change "$change" \
						--arg recent_commits "$recent_commits" \
						--arg code_style "$code_style" \
						--arg affected_files "$affected_files" \
						--argjson repo_source "$repo_source_json" \
						--arg mark_source_flag "$mark_source" \
						'{
							commit_msg: $commit_msg,
							change: ($change | gsub("\u0000"; "")),
							recent_commits_message: $recent_commits,
							code_style: $code_style,
							affected_files: ($affected_files | split("\n") | map(select(. != "")))
						}
						+ (if $mark_source_flag == "true" then {repo_source: $repo_source} else {} end)'
				fi
			} >>"$output_file" 2>/dev/null || echo "  ⚠️ Failed processing commit $commit in $repo_name" >&2
		done <<<"$commits"

		local commit_count
		commit_count=$(wc -l <"$output_file" 2>/dev/null || echo 0)
		echo "  ✅ Extracted $commit_count commits for $repo_name"
	} >&2
}

# Export function and variables for parallel
export -f process_repo
export -f sanitize_name

# Find all repo directories
mapfile -d '' repo_dirs < <(find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -type d -print0)

if [[ ${#repo_dirs[@]} -eq 0 ]]; then
	echo "No repositories found in: $REPOS_DIR" >&2
	exit 0
fi

# Run in parallel
printf '%s\0' "${repo_dirs[@]}" |
	parallel -0 -j "$THREADS" --line-buffer \
		process_repo {} "$OUTPUT_DIR_ABS" "$MAX_COMMITS" "$MAX_DIFF_SIZE" "$MAX_CONTRIBUTING_SIZE" "$SKIP_BOT_COMMITS" "$MARK_SOURCE" "$INCLUDE_LICENSE"

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Extraction complete. Data saved to: $OUTPUT_DIR_ABS"