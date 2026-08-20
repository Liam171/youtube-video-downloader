# YouTube Video Downloader

A desktop GUI for [yt-dlp](https://github.com/yt-dlp/yt-dlp), with separate launchers for Windows and macOS.

## Features

- Paste a video URL and download with one click
- Choose best quality, 1080p, 720p, or MP3 audio
- See download progress, speed, and estimated time remaining
- Update the bundled download engine from the app
- Keep downloads and app settings on the local computer

## Project files

| Platform | Main file | Launch file |
| --- | --- | --- |
| macOS | `yt-dlp-gui.py` | `启动图形下载器.command` |
| Windows | `yt-dlp-gui.ps1` | `启动图形下载器.bat` |

## macOS quick start

1. Install Python 3 and PySide6: `python3 -m pip install pyside6`
2. Install yt-dlp: `brew install yt-dlp`
3. Optional, for merging video and audio streams: `brew install ffmpeg`
4. Double-click `启动图形下载器.command`.

## Windows quick start

1. Download `yt-dlp.exe` from the [official releases page](https://github.com/yt-dlp/yt-dlp/releases) and place it beside the PowerShell script.
2. Optionally add `ffmpeg.exe` for format merging.
3. Double-click `启动图形下载器.bat`.

## Repository notes

- `config.json` stores local preferences and is intentionally ignored by Git.
- Download engines, Python caches, temporary files, and `dist/` release artifacts are ignored.
- Use GitHub Releases for packaged application downloads instead of committing large binaries.

## License

MIT
