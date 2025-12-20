#!/bin/bash

# Default: skip LFS
USE_LFS=0

# Parse arguments
if [ $# -lt 2 ] || [ $# -gt 3 ]; then
    echo "Usage: $0 <repository list file> <thread> [--lfs]"
    echo "  --lfs: Clone with Git LFS files (by default, LFS files are skipped)"
    exit 1
fi

REPO_FILE="$1"
thread="$2"

# Check if --lfs parameter is provided as third argument
if [ $# -eq 3 ] && [ "$3" = "--lfs" ]; then
    USE_LFS=1
fi

if [ ! -f "$REPO_FILE" ]; then
    echo "Error: File '$REPO_FILE' does not exist."
    exit 1
fi

mkdir -p repos
cd repos

# Preprocess: extract valid owner/repo lines
temp_file=$(mktemp)
while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    line=$(echo "$line" | xargs)
    [[ "$line" == */* ]] && echo "$line"
done <"../$REPO_FILE" >"$temp_file"

total=$(wc -l <"$temp_file")
if [ "$total" -eq 0 ]; then
    echo "No valid repositories found."
    rm -f "$temp_file"
    exit 0
fi

echo "Cloning $total repository(ies) in $thread threads..."
if [ $USE_LFS -eq 0 ]; then
    echo "Note: LFS files will be skipped (use --lfs parameter to download LFS content)"
fi

# Clone with or without LFS based on parameter
if [ $USE_LFS -eq 1 ]; then
    # Clone normally with LFS support
    parallel -j${thread} --bar -a "$temp_file" git clone https://github.com/{}.git
else
    # Skip LFS files by setting the environment variable
    parallel -j${thread} --bar -a "$temp_file" env GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/{}.git
fi

rm -f "$temp_file"
echo "Cloning complete."

cd ..
