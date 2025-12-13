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

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <max_pages_per_lang> <lang1,lang2,lang3,...>"
    echo "Example: $0 5 'python,javascript,go'"
    exit 1
fi

MAX_PAGES="$1"
LANG_LIST="$2"
SAFE_LANGS=$(echo "$LANG_LIST" | tr ',' '_' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
OUTPUT_FILE="top_$((MAX_PAGES * 100))_${SAFE_LANGS}_repos.txt"

>"$OUTPUT_FILE"

# Parse languages
IFS=',' read -ra LANGUAGES <<<"$LANG_LIST"

# Initialize rate limiting
declare -a REQUEST_TIMES=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    RATE_LIMIT=30   # 30 requests per minute with token
    echo "Using GitHub token: rate limit = 30 requests/minute"
else
    RATE_LIMIT=10   # 10 requests per minute without token
    echo "No GitHub token: rate limit = 10 requests/minute"
fi

# Wait until we can make another request without exceeding rate limits
wait_for_rate_limit() {
    local now
    now=$(date +%s)
    
    # Remove timestamps older than 60 seconds
    while [[ ${#REQUEST_TIMES[@]} -gt 0 ]] && [[ ${REQUEST_TIMES[0]} -le $((now - 60)) ]]; do
        REQUEST_TIMES=("${REQUEST_TIMES[@]:1}")
    done
    
    # If we've reached the limit, calculate how long to wait
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
    
    # Record this request time (will be updated after actual request)
    REQUEST_TIMES+=("$now")
}

# Main scraping loop
for lang in "${LANGUAGES[@]}"; do
    lang=$(echo "$lang" | xargs)
    if [ -z "$lang" ]; then
        continue
    fi

    echo "Fetching top $(($MAX_PAGES * 100)) repos for language: $lang"
    for ((i = 1; i <= MAX_PAGES; i++)); do
        print_progress $i $MAX_PAGES
        
        # Wait if needed to respect rate limits
        wait_for_rate_limit
        
        url="https://api.github.com/search/repositories?q=stars:>1+language:${lang}&sort=stars&order=desc&per_page=100&page=$i"
        
        if [[ -n "${GITHUB_TOKEN:-}" ]]; then
            response=$(curl -H "Authorization: token $GITHUB_TOKEN" -s "$url")
        else
            response=$(curl -s "$url")
        fi
        
        # Extract repository names and append to output file
        echo "$response" | jq -r '.items[].full_name' >>"$OUTPUT_FILE" 2>/dev/null
        
        # Update the last request timestamp to actual time after request
        REQUEST_TIMES[-1]=$(date +%s)
    done
    echo "" # New line after progress bar
done

# Remove duplicates and sort
sort -u "$OUTPUT_FILE" -o "$OUTPUT_FILE"

echo "Done! Results saved to $OUTPUT_FILE (total $(wc -l <"$OUTPUT_FILE") repos)"
