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

# Create a helper script for cloning with duplicate handling
helper_script=$(mktemp)
cat > "$helper_script" << 'EOF'
#!/bin/bash

repo="$1"
use_lfs="$2"

# Extract owner and repo name
owner=$(echo "$repo" | cut -d'/' -f1)
repo_name=$(echo "$repo" | cut -d'/' -f2)

# Create owner directory if it doesn't exist
mkdir -p "$owner"

# Target directory
target_dir="${owner}/${repo_name}"

# Check if directory exists
if [ -d "$target_dir" ]; then
    # Check if it's a git repository of the same remote
    if [ -d "${target_dir}/.git" ]; then
        current_remote=$(cd "$target_dir" && git config --get remote.origin.url 2>/dev/null || echo "")
        expected_remote="https://github.com/${repo}.git"
        if [ "$current_remote" = "$expected_remote" ]; then
            echo "Repository '${repo}' already cloned at '${target_dir}'"
            exit 0
        fi
    fi
    
    # Find a new name with suffix
    counter=1
    new_dir="${owner}/${repo_name}_${counter}"
    while [ -d "$new_dir" ]; do
        counter=$((counter + 1))
        new_dir="${owner}/${repo_name}_${counter}"
    done
    echo "Directory '${target_dir}' already exists. Renaming existing directory to '${new_dir}'"
    mv "$target_dir" "$new_dir"
    target_dir="$new_dir"
fi

# Clone the repository
if [ "$use_lfs" -eq 1 ]; then
    git clone "https://github.com/${repo}.git" "$target_dir"
else
    GIT_LFS_SKIP_SMUDGE=1 git clone "https://github.com/${repo}.git" "$target_dir"
fi

status=$?
if [ $status -ne 0 ]; then
    # If clone failed and we renamed an existing directory, move it back
    if [ -n "$new_dir" ] && [ -d "$new_dir" ] && [ ! -d "$target_dir" ]; then
        mv "$new_dir" "$target_dir"
    fi
    exit $status
fi

exit 0
EOF

chmod +x "$helper_script"
export HELPER_SCRIPT="$helper_script"
export USE_LFS

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
    rm -f "$temp_file" "$helper_script"
    exit 0
fi

echo "Cloning $total repository(ies) in $thread threads..."
if [ $USE_LFS -eq 0 ]; then
    echo "Note: LFS files will be skipped (use --lfs parameter to download LFS content)"
fi

# Use parallel to clone repositories
parallel -j${thread} --bar "$HELPER_SCRIPT {} $USE_LFS" :::: "$temp_file"

rm -f "$temp_file" "$helper_script"
echo "Cloning complete."

cd ..