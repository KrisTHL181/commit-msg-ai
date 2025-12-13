#!/bin/bash

# Display progress bar
print_progress() {
    local current=$1
    local total=$2
    [[ $total -eq 0 ]] && total=1

    local cols=$(tput cols 2>/dev/null || echo 80)
    local percent=$((current * 100 / total))
    local full_text=" ${percent}% (${current}/${total})"
    local simple_text=" ${percent}%"
    local text="$full_text"
    local text_len=${#text}
    local bar_width=$((cols - 2 - text_len)) # 2 = [ + ]

    if [[ $bar_width -lt 10 ]]; then
        text="$simple_text"
        text_len=${#text}
        bar_width=$((cols - 2 - text_len))
    fi

    if [[ $bar_width -lt 0 ]]; then
        printf "\r%s" "$text"
        return
    fi

    [[ $bar_width -gt $cols ]] && bar_width=$cols
    [[ $bar_width -lt 0 ]] && bar_width=0

    local filled=$((bar_width * current / total))
    local empty=$((bar_width - filled))

    local bar=$(printf "%${filled}s" | tr ' ' '#')
    local empty_bar=$(printf "%${empty}s" | tr ' ' '-')

    printf "\r[%s%s]%s" "$bar" "$empty_bar" "$text"
}

# Show help
show_help() {
    cat <<EOF
Usage: $0 <max_pages_per_lang> <lang1,lang2,...> [OPTIONS]

Fetch top GitHub repositories by language with star filtering.

OPTIONS:
  --min-stars N    Minimum number of stars (default: 100)
  -h, --help       Show this help

Example:
  $0 5 'python,javascript' --min-stars 500
EOF
}

# Default values
MAX_PAGES=""
LANG_LIST=""
MIN_STARS=100

# Parse positional args and flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --min-stars)
            MIN_STARS="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            if [[ -z "$MAX_PAGES" ]]; then
                MAX_PAGES="$1"
            elif [[ -z "$LANG_LIST" ]]; then
                LANG_LIST="$1"
            else
                echo "Error: Unexpected argument '$1'" >&2
                show_help >&2
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate required args
if [[ -z "$MAX_PAGES" ]] || [[ -z "$LANG_LIST" ]]; then
    echo "Error: Missing required arguments." >&2
    show_help >&2
    exit 1
fi

if ! [[ "$MAX_PAGES" =~ ^[0-9]+$ ]] || ! [[ "$MIN_STARS" =~ ^[0-9]+$ ]]; then
    echo "Error: max_pages and min-stars must be integers." >&2
    exit 1
fi

SAFE_LANGS=$(echo "$LANG_LIST" | tr ',' '_' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
OUTPUT_FILE="top_$((MAX_PAGES * 100))_${SAFE_LANGS}_min${MIN_STARS}_repos.txt"

>"$OUTPUT_FILE"

# Parse languages
IFS=',' read -ra LANGUAGES <<<"$LANG_LIST"

# Initialize rate limiting
declare -a REQUEST_TIMES=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    RATE_LIMIT=30
    echo "Using GitHub token: rate limit = 30 requests/minute"
else
    RATE_LIMIT=10
    echo "No GitHub token: rate limit = 10 requests/minute"
fi

# Wait for rate limit
wait_for_rate_limit() {
    local now=$(date +%s)
    while [[ ${#REQUEST_TIMES[@]} -gt 0 ]] && [[ ${REQUEST_TIMES[0]} -le $((now - 60)) ]]; do
        REQUEST_TIMES=("${REQUEST_TIMES[@]:1}")
    done
    if [[ ${#REQUEST_TIMES[@]} -ge $RATE_LIMIT ]]; then
        local earliest=${REQUEST_TIMES[0]}
        local must_wait_until=$((earliest + 60))
        local sleep_sec=$((must_wait_until - now))
        if [[ $sleep_sec -gt 0 ]]; then
            echo "Rate limit reached. Sleeping for $sleep_sec seconds..." >&2
            sleep "$sleep_sec"
            now=$((now + sleep_sec))
        fi
    fi
    REQUEST_TIMES+=("$now")
}

# Main loop
for lang in "${LANGUAGES[@]}"; do
    lang=$(echo "$lang" | xargs)
    [[ -z "$lang" ]] && continue

    echo "Fetching top $(($MAX_PAGES * 100)) repos for language: $lang (min stars: $MIN_STARS)"
    for ((i = 1; i <= MAX_PAGES; i++)); do
        print_progress $i $MAX_PAGES

        wait_for_rate_limit

        url="https://api.github.com/search/repositories?q=stars:>$MIN_STARS+language:${lang}&sort=stars&order=desc&per_page=100&page=$i"

        if [[ -n "${GITHUB_TOKEN:-}" ]]; then
            response=$(curl -H "Authorization: token $GITHUB_TOKEN" -s "$url")
        else
            response=$(curl -s "$url")
        fi

        echo "$response" | jq -r '.items[].full_name' >>"$OUTPUT_FILE" 2>/dev/null

        REQUEST_TIMES[-1]=$(date +%s)
    done
    echo ""
done

# Deduplicate and sort
sort -u "$OUTPUT_FILE" -o "$OUTPUT_FILE"

echo "Done! Results saved to $OUTPUT_FILE (total $(wc -l <"$OUTPUT_FILE") repos)"