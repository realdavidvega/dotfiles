#!/usr/bin/env bash
# yt-dlp common helpers for video, audio, and playlist downloads.
# Source this file: source "$DOTFILES_PATH/scripts/yt-dlp/yt-dlp-common.sh"

YT_DLP_OUTPUT_TEMPLATE="${YT_DLP_OUTPUT_TEMPLATE:-%(title)s [%(id)s].%(ext)s}"

_yt_dlp_cmd() {
    if command -v yt-dlp &>/dev/null; then
        echo "yt-dlp"
        return 0
    fi
    if command -v yt-dlp.exe &>/dev/null; then
        echo "yt-dlp.exe"
        return 0
    fi
    echo "yt-dlp: command not found. Install yt-dlp or yt-dlp.exe." >&2
    return 1
}

_yt_dlp_cookies() {
    local ytdlp="${1:-}"

    # 1. Explicit cookie file wins
    if [ -n "${YT_DLP_COOKIES:-}" ] && [ -f "${YT_DLP_COOKIES}" ]; then
        printf '%s\n' "--cookies"
        printf '%s\n' "${YT_DLP_COOKIES}"
        return 0
    fi

    # 2. WSL Windows path: try chrome profiles only when the selected downloader
    # is the Windows executable. Linux yt-dlp resolves "chrome:Profile 1" against
    # ~/.config/google-chrome/Profile 1, which fails on WSL machines without Linux Chrome.
    if grep -qi microsoft /proc/version 2>/dev/null && [[ "$ytdlp" == *.exe ]]; then
        local -a win_ytdlp_candidates=(
            "/mnt/c/Users/david/AppData/Local/Programs/Python/Python312/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/AppData/Roaming/Python/Python312/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/AppData/Local/Programs/Python/Python313/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/AppData/Roaming/Python/Python313/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/AppData/Local/Programs/Python/Python310/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/AppData/Roaming/Python/Python310/Scripts/yt-dlp.exe"
            "/mnt/c/Users/david/scoop/shims/yt-dlp.exe"
        )
        local -a chrome_profiles=("Profile 1" "Default")
        local win_ytdlp
        local profile
        local cookies_db

        if [ -n "${YT_DLP_CHROME_PROFILE:-}" ]; then
            chrome_profiles=("${YT_DLP_CHROME_PROFILE}" "${chrome_profiles[@]}")
        fi

        if command -v yt-dlp.exe >/dev/null 2>&1; then
            win_ytdlp="$(command -v yt-dlp.exe)"
        else
            for candidate in "${win_ytdlp_candidates[@]}"; do
                if [ -f "$candidate" ]; then
                    win_ytdlp="$candidate"
                    break
                fi
            done
        fi

        if [ -n "${win_ytdlp:-}" ]; then
            for profile in "${chrome_profiles[@]}"; do
                cookies_db="/mnt/c/Users/david/AppData/Local/Google/Chrome/User Data/${profile}/Network/Cookies"
                if [ -f "$cookies_db" ]; then
                    printf '%s\n' "--cookies-from-browser"
                    printf '%s\n' "chrome:${profile}"
                    return 0
                fi
            done
        fi
    fi

    # 3. Firefox on Linux/WSL
    if [ -d "$HOME/.mozilla/firefox" ]; then
        printf '%s\n' "--cookies-from-browser"
        printf '%s\n' "firefox"
        return 0
    fi

    # 4. No cookies available — yt-dlp will work for public videos
    return 0
}

_yt_dlp_append_args() {
    local line
    while IFS= read -r line; do
        [ -n "$line" ] && args+=("$line")
    done <<EOF
$1
EOF
}

_yt_dlp_video_args() {
    local -a args=(
        --embed-metadata
        --embed-thumbnail
        --embed-subs
        --sub-langs "all,-live_chat"
        --continue
        --no-overwrites
        --output "${YT_DLP_OUTPUT_TEMPLATE}"
    )
    printf '%s\n' "${args[@]}"
}

_yt_dlp_audio_args() {
    local -a args=(
        --extract-audio
        --embed-metadata
        --embed-thumbnail
        --continue
        --no-overwrites
        --output "${YT_DLP_OUTPUT_TEMPLATE}"
    )
    printf '%s\n' "${args[@]}"
}
