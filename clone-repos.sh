#!/bin/bash

if [ $# -ne 2 ]; then
	echo "Usage: $0 <repository list file> <thread>"
	exit 1
fi

REPO_FILE="$1"
thread="$2"

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

parallel -j${thread} --bar -a "$temp_file" git clone https://github.com/{}.git

rm -f "$temp_file"
echo "Cloning complete."

cd ..
