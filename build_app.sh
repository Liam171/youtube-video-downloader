#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
#  Build a standalone macOS .app with PyInstaller.
#  Usage: ./build_app.sh [arm64|x64]
#  Output: dist/YouTube视频下载器.app
# ──────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

ARCH="${1:-arm64}"
case "$ARCH" in
  arm64) FFMPEG=ffmpeg_arm64 ;;
  x64)   FFMPEG=ffmpeg_x64 ;;
  *) echo "unknown arch: $ARCH (use arm64 or x64)"; exit 1 ;;
esac

APP_NAME="YouTube视频下载器"

pyinstaller --noconfirm --windowed \
  --name "$APP_NAME" \
  --icon appicon.icns \
  --osx-bundle-identifier com.liam171.ytdlpdownloader \
  --add-binary "yt-dlp_macos:." \
  --add-binary "$FFMPEG:." \
  --workpath "build/pyi-$ARCH" \
  --distpath "dist" \
  yt-dlp-gui.py

# Ad-hoc signature so the app opens locally without "damaged" warnings
codesign --force --deep --sign - "dist/$APP_NAME.app" 2>/dev/null || true

echo
echo "✅ Built: dist/$APP_NAME.app ($ARCH)"
du -sh "dist/$APP_NAME.app"
