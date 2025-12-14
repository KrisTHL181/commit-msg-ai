#!/bin/bash

# This script scans a user-specified directory and removes any subdirectory
# that is not a fully functional (non-corrupted) Git repository.

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <directory>"
  echo "  Scans <directory> for incomplete/corrupted Git repositories and deletes them."
  echo "  You MUST specify the directory explicitly to avoid accidental data loss."
  exit 1
fi

TARGET_DIR="$1"

# Resolve to absolute path
if ! TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"; then
  echo "Error: Unable to access directory '$1'." >&2
  exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: '$TARGET_DIR' is not a directory." >&2
  exit 1
fi

# Safety: ensure the path is not root or home
case "$TARGET_DIR" in
  "/"|"/home"|"/Users"|"/usr"|"/var"|"/tmp"|"/bin"|"/lib"|"/sbin")
    echo "Error: Refusing to operate on system directory: $TARGET_DIR" >&2
    exit 1
    ;;
esac

# Confirm with user
echo "⚠️  This script will DELETE incomplete or corrupted Git repos under:"
echo "    $TARGET_DIR"
read -p "Are you sure? Type 'yes' to continue: " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "🔍 Scanning subdirectories in: $TARGET_DIR"

# Process each immediate subdirectory
for subdir in "$TARGET_DIR"/*/; do
  [ -d "$subdir" ] || continue
  repo_path="$subdir"

  # Remove trailing slash for consistent basename
  repo_name=$(basename "$repo_path")
  full_path="$TARGET_DIR/$repo_name"

  # Skip if not a Git repo
  if [ ! -d "$full_path/.git" ]; then
    echo "🗑️  Not a Git repository (no .git): $full_path → DELETING"
    rm -rf "$full_path"
    continue
  fi

  # Perform integrity checks in a subshell
  (
    cd "$full_path" || exit 1

    # Check valid HEAD
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
      exit 1
    fi

    # Check object integrity (ignore dangling objects)
    if ! git fsck --full --no-dangling >/dev/null 2>&1; then
      exit 1
    fi

    # Ensure commit history is readable
    if ! git log -1 --oneline >/dev/null 2>&1; then
      exit 1
    fi

    exit 0
  )

  if [ $? -ne 0 ]; then
    echo "🗑️  Incomplete or corrupted repo: $full_path → DELETING"
    rm -rf "$full_path"
  else
    echo "✅ OK: $full_path"
  fi
done

echo "✅ Scan completed."
