#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
#  yt-dlp Video Downloader — macOS Launcher
#  Double-click this file in Finder to start the app.
# ──────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/yt-dlp-gui.py"

# Find a Python 3 that has PySide6
PYTHON=""
for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    # PySide6 not installed — offer to install
    RESPONSE=$(osascript -e 'display dialog "PySide6 is not installed.\n\nInstall now via pip?\n(pip3 install pyside6)" buttons {"Cancel", "Install"} default button "Install" with icon note')
    if [[ "$RESPONSE" == *"Install"* ]]; then
        PIP_PYTHON=""
        for c in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
            [ -x "$c" ] && PIP_PYTHON="$c" && break
        done
        "$PIP_PYTHON" -m pip install pyside6
        # re-check
        for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
            if [ -x "$candidate" ] && "$candidate" -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
                PYTHON="$candidate"
                break
            fi
        done
    else
        exit 1
    fi
fi

# Check/install yt-dlp if needed
YTDLP_FOUND=false
for candidate in "$SCRIPT_DIR/yt-dlp" "$SCRIPT_DIR/yt-dlp_macos" "/usr/local/bin/yt-dlp" "/opt/homebrew/bin/yt-dlp"; do
    if [ -x "$candidate" ]; then
        YTDLP_FOUND=true
        break
    fi
done

if ! $YTDLP_FOUND && ! command -v yt-dlp &>/dev/null; then
    RESPONSE=$(osascript -e 'display dialog "yt-dlp is not installed.\n\nInstall now with Homebrew?\n(brew install yt-dlp)" buttons {"Cancel", "Install"} default button "Install" with icon note')
    if [[ "$RESPONSE" == *"Install"* ]]; then
        if command -v brew &>/dev/null; then
            brew install yt-dlp
        else
            osascript -e 'display dialog "Homebrew not found.\n\nInstall Homebrew first or install yt-dlp manually:\n  pip3 install yt-dlp" buttons {"OK"} default button "OK" with icon stop'
            exit 1
        fi
    else
        exit 1
    fi
fi

# Launch
cd "$SCRIPT_DIR"
exec "$PYTHON" "$PYTHON_SCRIPT"
