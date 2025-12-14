#!/bin/bash

# Root directory to scan (defaults to current directory if not provided as argument)
ROOT_DIR="${1:-.}"

# Convert to absolute path to avoid issues with relative paths
ROOT_DIR="$(cd "$ROOT_DIR" && pwd)"

# Check if the specified root directory exists
if [ ! -d "$ROOT_DIR" ]; then
  echo "Error: Directory '$ROOT_DIR' does not exist." >&2
  exit 1
fi

echo "Scanning for incomplete or corrupted Git repositories under: $ROOT_DIR"

# Iterate over all immediate subdirectories of ROOT_DIR
for dir in "$ROOT_DIR"/*/; do
  # Skip if not a directory (e.g., symbolic links that break, or glob mismatches)
  [ -d "$dir" ] || continue

  dir_name=$(basename "$dir")
  full_path="$ROOT_DIR/$dir_name"

  # Check if it's a Git repository (must contain .git directory)
  if [ ! -d "$full_path/.git" ]; then
    echo "🗑️  Not a Git repository (missing .git): $full_path → DELETING"
    rm -rf "$full_path"
    continue
  fi

  # Run integrity checks inside a subshell to avoid affecting current working directory
  (
    cd "$full_path" || exit 1

    # Verify that HEAD points to a valid commit
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
      echo "🗑️  Invalid or missing HEAD: $full_path → DELETING"
      exit 1
    fi

    # Check repository object integrity; suppress 'dangling' output (non-fatal)
    if ! git fsck --full --no-dangling >/dev/null 2>&1; then
      echo "🗑️  Git integrity check failed (corrupted/incomplete objects): $full_path → DELETING"
      exit 1
    fi

    # Ensure we can read at least one commit from history
    if ! git log -1 --oneline >/dev/null 2>&1; then
      echo "🗑️  Unable to read commit history: $full_path → DELETING"
      exit 1
    fi

    # All checks passed
    exit 0
  )

  # If any check failed (subshell exited non-zero), remove the directory
  if [ $? -ne 0 ]; then
    rm -rf "$full_path"
  else
    echo "✅ OK: $full_path"
  fi
done

echo "✅ Scan completed."
