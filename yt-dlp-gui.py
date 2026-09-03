#!/usr/bin/env python3
"""
yt-dlp Video Downloader — macOS GUI (PySide6)
A premium dark-themed graphical front-end for yt-dlp on macOS.
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import shutil
import signal
import platform
import threading
import subprocess

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QProgressBar, QTextEdit,
    QFileDialog, QFrame, QButtonGroup,
)
from PySide6.QtCore import Qt, QObject, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor

# ═══════════════════════════════════════════════════════════════════════
#  Paths & Helpers
# ═══════════════════════════════════════════════════════════════════════

# When frozen into a .app (PyInstaller), resources live in the bundle and
# writable state (config, temp logs) must go to Application Support —
# the .app itself may sit in read-only /Applications.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

# Writable state (config, temp logs) must live outside the frozen bundle —
# the app itself may sit in a read-only location.
if sys.platform == "win32":
    APP_SUPPORT = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "YouTubeDownloader"
    )
else:
    APP_SUPPORT = os.path.join(
        os.path.expanduser("~/Library/Application Support"), "YouTubeDownloader"
    )
os.makedirs(APP_SUPPORT, exist_ok=True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_SUPPORT, "config.json")

YTDLP_CANDIDATES = [
    os.path.join(BUNDLE_DIR, "yt-dlp"),
    os.path.join(BUNDLE_DIR, "yt-dlp_macos"),
    os.path.join(BUNDLE_DIR, "yt-dlp.exe"),
    os.path.join(SCRIPT_DIR, "yt-dlp"),
    os.path.join(SCRIPT_DIR, "yt-dlp_macos"),
    os.path.join(SCRIPT_DIR, "yt-dlp.exe"),
    os.path.join(SCRIPT_DIR, "engines", "yt-dlp_macos"),
    os.path.join(SCRIPT_DIR, "engines", "yt-dlp"),
    os.path.join(SCRIPT_DIR, "engines", "yt-dlp.exe"),
    "/usr/local/bin/yt-dlp",
    "/opt/homebrew/bin/yt-dlp",
]
# Bundled ffmpeg is arch-specific (ffmpeg_arm64 / ffmpeg_x64);
# also accept a plain "ffmpeg" next to the script and Homebrew installs.
_BUNDLED_FFMPEG = os.path.join(
    BUNDLE_DIR, "ffmpeg_arm64" if platform.machine() == "arm64" else "ffmpeg_x64"
)
FFMPEG_CANDIDATES = [
    _BUNDLED_FFMPEG,
    os.path.join(BUNDLE_DIR, "ffmpeg"),
    os.path.join(BUNDLE_DIR, "ffmpeg.exe"),
    os.path.join(SCRIPT_DIR, "ffmpeg"),
    os.path.join(SCRIPT_DIR, "ffmpeg.exe"),
    os.path.join(SCRIPT_DIR, "ffmpeg_arm64" if platform.machine() == "arm64" else "ffmpeg_x64"),
    os.path.join(SCRIPT_DIR, "engines", "ffmpeg_arm64" if platform.machine() == "arm64" else "ffmpeg_x64"),
    os.path.join(SCRIPT_DIR, "engines", "ffmpeg"),
    os.path.join(SCRIPT_DIR, "engines", "ffmpeg.exe"),
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
]


def find_exe(candidates: list[str]) -> str | None:
    for p in candidates:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    for p in candidates:
        name = os.path.basename(p)
        if name and shutil.which(name):
            return shutil.which(name)
    return None


YTDLP_PATH = find_exe(YTDLP_CANDIDATES)
FFMPEG_PATH = find_exe(FFMPEG_CANDIDATES)


def load_config() -> dict:
    defaults = {"lastSavePath": os.path.expanduser("~/Downloads"), "lastFormat": 0}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            defaults.update(data)
        except Exception:
            pass
    if not os.path.isdir(defaults.get("lastSavePath", "")):
        defaults["lastSavePath"] = os.path.expanduser("~/Downloads")
    return defaults


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


# fmt_index -> (format when ffmpeg is available, format when it is not)
# Prefer AVC/H.264: YouTube's AV1 streams (e.g. 401) currently 403
# mid-download (SABR experiment, yt-dlp#12482). AVC tops out at 1080p.
VIDEO_FORMATS = {
    0: ("bestvideo[vcodec^=avc1]+bestaudio/bestvideo+bestaudio/best",
        "best[ext=mp4]/best"),
    1: ("bestvideo[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "best[height<=1080][ext=mp4]/best[height<=1080]"),
    2: ("bestvideo[height<=720][vcodec^=avc1]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]",
        "best[height<=720][ext=mp4]/best[height<=720]"),
}


def build_args(url: str, save_path: str, fmt_index: int) -> list[str]:
    template = os.path.join(save_path, "%(title).200B [%(id)s].%(ext)s")
    args = ["--newline", "--no-playlist", "-o", template]
    if FFMPEG_PATH:
        args += ["--ffmpeg-location", FFMPEG_PATH]

    if fmt_index in VIDEO_FORMATS:
        merged, direct = VIDEO_FORMATS[fmt_index]
        args += ["-f", merged if FFMPEG_PATH else direct, "--merge-output-format", "mp4"]
    elif FFMPEG_PATH:
        args += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        args += ["-f", "bestaudio[ext=m4a]/bestaudio"]

    args.append(url)
    return args


# ═══════════════════════════════════════════════════════════════════════
#  Progress parsing (yt-dlp --newline output)
# ═══════════════════════════════════════════════════════════════════════

RE_PROGRESS = re.compile(r"\[download\]\s+(\d{1,3}(?:\.\d)?)%")
RE_SPEED = re.compile(r"at\s+([\d.]+\s*\w+/s)")
RE_ETA = re.compile(r"ETA\s+([\d:]+)")


# ═══════════════════════════════════════════════════════════════════════
#  Signal bridge — thread-safe communication to the Qt main thread
# ═══════════════════════════════════════════════════════════════════════

class WorkerSignals(QObject):
    log_line = Signal(str)
    progress = Signal(int)
    status_text = Signal(str)
    speed_text = Signal(str)
    process_done = Signal(int)
    update_done = Signal(int)


# ═══════════════════════════════════════════════════════════════════════
#  Design tokens — deep ink surfaces with one electric-blue accent
# ═══════════════════════════════════════════════════════════════════════

BG = "#0E131B"          # window
RAIL = "#121724"        # sidebar
CARD = "#151B27"        # raised card surface
FIELD = "#0F141E"       # inset inputs / chips
BORDER = "#232B3A"      # card hairline
BORDER_2 = "#2C3650"    # hover hairline
TXT = "#EDF1F7"         # primary text
TXT_2 = "#9AA6BA"       # secondary text
TXT_3 = "#64708A"       # muted text
ACCENT = "#4E7CFF"
ACCENT_SOFT = "#9DB8FF"
SUCCESS = "#3ECF8E"
ERROR = "#F0616D"
NEUTRAL = "#8B96A9"

# Platform fonts — Qt resolves the first available face in each list.
if sys.platform == "win32":
    _SANS = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
    _MONO = ["Consolas", "Cascadia Mono"]
else:
    _SANS = ["Avenir Next", "PingFang SC", "Helvetica Neue"]
    _MONO = ["Menlo", "SF Mono", "PingFang SC"]

# QSS font-family needs a literal list.
_SANS_QSS = ", ".join(f"'{n}'" for n in _SANS)
_MONO_QSS = ", ".join(f"'{n}'" for n in _MONO)

QSS_CARD = f"""
    QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
"""
QSS_PRIMARY_BTN = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #5B82FF, stop:1 #3E5EF7);
        color: #FFFFFF; border: 1px solid #4E6FF5; border-radius: 10px;
        padding: 10px 22px;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #6E90FF, stop:1 #4A6CFA);
    }
    QPushButton:pressed { background: #3451D9; }
    QPushButton:disabled { background: #1D2536; border-color: #1D2536; color: #4A5570; }
"""
QSS_SECONDARY_BTN = f"""
    QPushButton {{
        background: #1A2130; color: #C6CFDE;
        border: 1px solid {BORDER}; border-radius: 10px;
        padding: 10px 18px;
    }}
    QPushButton:hover {{ background: #202939; border-color: {BORDER_2}; color: {TXT}; }}
    QPushButton:pressed {{ background: #171E2C; }}
    QPushButton:disabled {{ color: #4A5570; border-color: #1D2432; background: #151B27; }}
"""
QSS_GHOST_BTN = f"""
    QPushButton {{
        background: transparent; color: {TXT_3}; border: none; border-radius: 8px;
        padding: 7px 12px;
    }}
    QPushButton:hover {{ background: #182030; color: {ACCENT_SOFT}; }}
"""
QSS_INPUT = f"""
    QLineEdit {{
        background: {FIELD}; color: {TXT};
        border: 1px solid #263043; border-radius: 10px;
        padding: 0 14px;
    }}
    QLineEdit:hover {{ border-color: #33405C; }}
    QLineEdit:focus {{ background: #111827; border: 1px solid {ACCENT}; }}
    QLineEdit:disabled {{ color: #4A5570; border-color: #1D2432; }}
"""
QSS_CHIP = f"""
    QPushButton {{
        background: {FIELD}; color: {TXT_2};
        border: 1px solid #263043; border-radius: 9px;
        padding: 9px 16px;
    }}
    QPushButton:hover {{ border-color: #3A4763; color: #C6CFDE; }}
    QPushButton:checked {{
        background: rgba(78, 124, 255, 16%);
        border-color: {ACCENT}; color: {ACCENT_SOFT};
    }}
    QPushButton:disabled {{ color: #3E4A63; border-color: #1D2432; }}
    QPushButton:checked:disabled {{ color: #3E5078; border-color: #2A3A66; background: rgba(78, 124, 255, 7%); }}
"""
QSS_PROGRESS = f"""
    QProgressBar {{
        background: #1A2130; border: none; border-radius: 3px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {ACCENT}, stop:1 #7AA5FF);
        border-radius: 3px;
    }}
"""
QSS_LOG = f"""
    QTextEdit {{
        background: #0A0E15; color: #7E93B4; border: none;
        font-family: {_MONO_QSS}; padding: 4px;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #2A3345; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: #37435C; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def _font(size: int, weight: int = QFont.Normal, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(_MONO if mono else _SANS)
    f.setPointSize(size)
    f.setWeight(weight)
    return f


# ═══════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频下载器")
        self.resize(1040, 680)
        self.setMinimumSize(880, 600)

        # State
        self.config = load_config()
        self.proc: subprocess.Popen | None = None
        self.update_proc: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.out_file = ""
        self.err_file = ""

        # Signals
        self.signals = WorkerSignals()
        self.signals.log_line.connect(self._on_log_line)
        self.signals.progress.connect(self._on_progress)
        self.signals.status_text.connect(self._on_status_text)
        self.signals.speed_text.connect(self._on_speed_text)
        self.signals.process_done.connect(self._on_process_done)
        self.signals.update_done.connect(self._on_update_done)

        # Build UI
        self._build_ui()
        self._load_initial_state()
        self._startup_log()

    # ── UI Construction ──────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setObjectName("central")
        central.setStyleSheet(f"QWidget#central {{ background-color: {BG}; }}")

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_rail())

        workspace = QWidget()
        ws = QVBoxLayout(workspace)
        ws.setContentsMargins(36, 30, 36, 24)
        ws.setSpacing(0)

        ws.addLayout(self._build_header())
        ws.addSpacing(22)
        ws.addWidget(self._build_link_card())
        ws.addSpacing(14)
        ws.addWidget(self._build_settings_card())
        ws.addSpacing(16)
        ws.addLayout(self._build_action_row())
        ws.addSpacing(18)
        ws.addLayout(self._build_progress_card())
        ws.addSpacing(10)
        ws.addWidget(self._build_log_section(), stretch=1)

        root.addWidget(workspace, stretch=1)

    def _build_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(264)
        rail.setStyleSheet(f"QFrame#rail {{ background: {RAIL}; border-right: 1px solid #1A2130; }}")
        r = QVBoxLayout(rail)
        r.setContentsMargins(24, 26, 22, 22)
        r.setSpacing(0)

        # Brand row
        brand = QHBoxLayout()
        brand.setSpacing(11)
        mark = QLabel("↓")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(36, 36)
        mark.setFont(_font(17, QFont.Bold))
        mark.setStyleSheet(
            "QLabel#brandMark { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #5B82FF, stop:1 #3E5EF7); border-radius: 10px; color: #FFFFFF; }"
        )
        brand.addWidget(mark)
        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name = QLabel("视频下载器")
        name.setFont(_font(15, QFont.DemiBold))
        name.setStyleSheet(f"color: {TXT}; background: transparent;")
        name_col.addWidget(name)
        sub = QLabel("MEDIA DOWNLOADER")
        sub.setFont(_font(8.5, QFont.DemiBold, mono=True))
        sub.setStyleSheet(f"color: {TXT_3}; letter-spacing: 1.5px; background: transparent;")
        name_col.addWidget(sub)
        brand.addLayout(name_col)
        r.addLayout(brand)

        r.addSpacing(28)

        # Engine section
        caption = QLabel("引擎状态")
        caption.setFont(_font(10, QFont.DemiBold))
        caption.setStyleSheet(f"color: {TXT_3}; letter-spacing: 1px; background: transparent;")
        r.addWidget(caption)
        r.addSpacing(10)

        engine_card = QFrame()
        engine_card.setObjectName("card")
        engine_card.setStyleSheet(QSS_CARD)
        ec = QVBoxLayout(engine_card)
        ec.setContentsMargins(14, 12, 14, 12)
        ec.setSpacing(9)

        self.engine_ytdlp_row = self._engine_row("yt-dlp")
        ec.addLayout(self.engine_ytdlp_row)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #1D2432;")
        ec.addWidget(divider)
        self.engine_ffmpeg_row = self._engine_row("ffmpeg")
        ec.addLayout(self.engine_ffmpeg_row)
        r.addWidget(engine_card)

        r.addStretch()

        # Footer
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: #1D2432;")
        r.addWidget(rule)
        r.addSpacing(12)
        footer = QLabel("macOS · LOCAL-FIRST · v2.0")
        footer.setFont(_font(9, QFont.Normal, mono=True))
        footer.setStyleSheet(f"color: #4A5670; letter-spacing: 0.5px; background: transparent;")
        r.addWidget(footer)
        return rail

    def _engine_row(self, name: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(7, 7)
        dot.setStyleSheet(f"background: {NEUTRAL}; border-radius: 3px;")
        row.addWidget(dot)
        label = QLabel(name)
        label.setFont(_font(11.5, QFont.Medium, mono=True))
        label.setStyleSheet(f"color: {TXT_2}; background: transparent;")
        row.addWidget(label)
        row.addStretch()
        state = QLabel("检测中")
        state.setObjectName(f"engineState_{name}")
        state.setFont(_font(10.5, QFont.Medium))
        state.setStyleSheet(f"color: {TXT_3}; background: transparent;")
        row.addWidget(state)
        # keep references for later updates
        if name == "yt-dlp":
            self._ytdlp_dot, self._ytdlp_state = dot, state
        else:
            self._ffmpeg_dot, self._ffmpeg_state = dot, state
        return row

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel("新建下载")
        title.setFont(_font(25, QFont.DemiBold))
        title.setStyleSheet(f"color: {TXT}; background: transparent;")
        col.addWidget(title)
        subtitle = QLabel("粘贴链接，选择画质，一键下载到本地。")
        subtitle.setFont(_font(12))
        subtitle.setStyleSheet(f"color: {TXT_3}; background: transparent;")
        col.addWidget(subtitle)
        header.addLayout(col)
        header.addStretch()

        self.header_pill = QLabel("已就绪")
        self.header_pill.setAlignment(Qt.AlignCenter)
        self.header_pill.setFixedHeight(26)
        self.header_pill.setFont(_font(11, QFont.DemiBold))
        self._update_pill(NEUTRAL, "rgba(139, 150, 169, 10%)", "已就绪")
        header.addWidget(self.header_pill, alignment=Qt.AlignTop)
        return header

    def _build_link_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(QSS_CARD)
        c = QVBoxLayout(card)
        c.setContentsMargins(18, 16, 18, 16)
        c.setSpacing(12)

        label_row = QHBoxLayout()
        lbl = QLabel("视频链接")
        lbl.setFont(_font(12.5, QFont.DemiBold))
        lbl.setStyleSheet(f"color: #C6CFDE; background: transparent;")
        label_row.addWidget(lbl)
        label_row.addStretch()
        hint = QLabel("支持 YouTube 及数千个 yt-dlp 兼容站点")
        hint.setFont(_font(10.5))
        hint.setStyleSheet(f"color: {TXT_3}; background: transparent;")
        label_row.addWidget(hint)
        c.addLayout(label_row)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_edit.setFixedHeight(44)
        self.url_edit.setFont(_font(13.5, QFont.Medium))
        self.url_edit.setStyleSheet(QSS_INPUT)
        self.url_edit.returnPressed.connect(self.start_download)
        row.addWidget(self.url_edit, stretch=1)
        self.paste_btn = self._make_button("粘贴", "ghost")
        self.paste_btn.setFixedSize(64, 44)
        self.paste_btn.clicked.connect(self._paste_url)
        row.addWidget(self.paste_btn)
        c.addLayout(row)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setVisible(False)
        c.addWidget(self.feedback_label)
        return card

    def _build_settings_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(QSS_CARD)
        c = QHBoxLayout(card)
        c.setContentsMargins(18, 16, 18, 16)
        c.setSpacing(20)

        # Format column
        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(10)
        fmt_lbl = QLabel("画质")
        fmt_lbl.setFont(_font(12.5, QFont.DemiBold))
        fmt_lbl.setStyleSheet("color: #C6CFDE; background: transparent;")
        fmt_col.addWidget(fmt_lbl)
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self.format_group = QButtonGroup(self)
        self.format_group.setExclusive(True)
        self.format_buttons: list[QPushButton] = []
        for i, label in enumerate(["最佳质量", "1080p", "720p", "仅音频 MP3"]):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFont(_font(12, QFont.Medium))
            btn.setStyleSheet(QSS_CHIP)
            btn.setCursor(Qt.PointingHandCursor)
            self.format_group.addButton(btn, i)
            self.format_buttons.append(btn)
            chip_row.addWidget(btn)
        self.format_buttons[0].setChecked(True)
        fmt_col.addLayout(chip_row)
        fmt_col.addStretch()
        c.addLayout(fmt_col, stretch=3)

        # Vertical divider
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: #1D2432;")
        c.addWidget(divider)

        # Path column
        path_col = QVBoxLayout()
        path_col.setSpacing(10)
        path_lbl = QLabel("保存位置")
        path_lbl.setFont(_font(12.5, QFont.DemiBold))
        path_lbl.setStyleSheet("color: #C6CFDE; background: transparent;")
        path_col.addWidget(path_lbl)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择文件夹")
        self.path_edit.setFixedHeight(38)
        self.path_edit.setFont(_font(12))
        self.path_edit.setStyleSheet(QSS_INPUT)
        path_row.addWidget(self.path_edit, stretch=1)
        self.browse_btn = self._make_button("浏览…", "secondary")
        self.browse_btn.setFixedSize(78, 38)
        self.browse_btn.clicked.connect(self._browse_folder)
        path_row.addWidget(self.browse_btn)
        path_col.addLayout(path_row)
        path_col.addStretch()
        c.addLayout(path_col, stretch=4)
        return card

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_btn = self._make_button("开始下载", "primary")
        self.start_btn.setFixedHeight(42)
        self.start_btn.setMinimumWidth(150)
        self.start_btn.setFont(_font(13.5, QFont.DemiBold))
        self.start_btn.clicked.connect(self.start_download)
        row.addWidget(self.start_btn)
        self.stop_btn = self._make_button("停止", "secondary")
        self.stop_btn.setFixedHeight(42)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_download)
        row.addWidget(self.stop_btn)
        row.addStretch()
        self.update_btn = self._make_button("更新下载引擎", "ghost")
        self.update_btn.setFixedHeight(36)
        self.update_btn.clicked.connect(self.update_ytdlp)
        row.addWidget(self.update_btn)
        return row

    def _build_progress_card(self) -> QHBoxLayout:
        wrap = QHBoxLayout()
        wrap.setSpacing(0)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(QSS_CARD)
        c = QVBoxLayout(card)
        c.setContentsMargins(18, 16, 18, 16)
        c.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setStyleSheet(f"background: {SUCCESS}; border-radius: 4px;")
        top.addWidget(self.status_dot)
        self.status_label = QLabel("准备就绪")
        self.status_label.setFont(_font(14, QFont.DemiBold))
        self.status_label.setStyleSheet(f"color: {TXT}; background: transparent;")
        top.addWidget(self.status_label)
        top.addStretch()
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setFont(_font(20, QFont.DemiBold, mono=True))
        self.progress_value_label.setStyleSheet(f"color: {ACCENT_SOFT}; background: transparent;")
        top.addWidget(self.progress_value_label)
        c.addLayout(top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(QSS_PROGRESS)
        c.addWidget(self.progress_bar)

        self.speed_label = QLabel("粘贴一个链接后，即可开始下载。")
        self.speed_label.setFont(_font(11))
        self.speed_label.setStyleSheet(f"color: {TXT_3}; background: transparent;")
        c.addWidget(self.speed_label)

        wrap.addWidget(card)
        return wrap

    def _build_log_section(self) -> QWidget:
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self.log_toggle_btn = QPushButton("查看运行记录")
        self.log_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.log_toggle_btn.setFont(_font(11.5, QFont.Medium))
        self.log_toggle_btn.setStyleSheet(QSS_GHOST_BTN)
        self.log_toggle_btn.clicked.connect(self._toggle_log_view)
        v.addWidget(self.log_toggle_btn, alignment=Qt.AlignLeft)

        self.log_panel = QFrame()
        self.log_panel.setObjectName("logPanel")
        self.log_panel.setStyleSheet(
            "QFrame#logPanel { background: #0A0E15; border: 1px solid #1D2432; border-radius: 10px; }"
        )
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(12, 10, 12, 10)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(_font(10.5, mono=True))
        self.log_view.setStyleSheet(QSS_LOG)
        log_layout.addWidget(self.log_view)
        self.log_panel.setVisible(False)
        v.addWidget(self.log_panel)
        return holder

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _make_button(text: str, kind: str = "secondary") -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        if kind == "primary":
            btn.setStyleSheet(QSS_PRIMARY_BTN)
        elif kind == "ghost":
            btn.setStyleSheet(QSS_GHOST_BTN)
        else:
            btn.setStyleSheet(QSS_SECONDARY_BTN)
        return btn

    def _update_pill(self, color: str, bg_tint: str, text: str):
        self.header_pill.setText(text)
        self.header_pill.setStyleSheet(
            f"color: {color}; background: {bg_tint};"
            f"border: 1px solid {color}44; border-radius: 13px; padding: 0 12px;"
        )

    def _set_engine_row(self, dot_lbl: QLabel, state_lbl: QLabel, ok: bool):
        if ok:
            dot_lbl.setStyleSheet(f"background: {SUCCESS}; border-radius: 3px;")
            state_lbl.setText("就绪")
            state_lbl.setStyleSheet(f"color: {SUCCESS}; background: transparent;")
        else:
            dot_lbl.setStyleSheet(f"background: {ERROR}; border-radius: 3px;")
            state_lbl.setText("未检测到")
            state_lbl.setStyleSheet(f"color: {ERROR}; background: transparent;")

    def _set_status(self, title: str, detail: str = "", tone: str = "ready"):
        tones = {
            "ready":   (SUCCESS,  "rgba(62, 207, 142, 10%)",  "已就绪"),
            "working": (ACCENT,   "rgba(78, 124, 255, 12%)",   "下载中"),
            "success": (SUCCESS,  "rgba(62, 207, 142, 10%)",   "已完成"),
            "error":   (ERROR,    "rgba(240, 97, 109, 10%)",   "需处理"),
            "stopped": (NEUTRAL,  "rgba(139, 150, 169, 10%)",  "已停止"),
        }
        dot, tint, pill_text = tones.get(tone, tones["ready"])
        self.status_label.setText(title)
        self.speed_label.setText(detail)
        self.status_dot.setStyleSheet(f"background: {dot}; border-radius: 4px;")
        self._update_pill(dot, tint, pill_text)

    def _set_feedback(self, text: str = "", tone: str = "error"):
        if not text:
            self.feedback_label.clear()
            self.feedback_label.setVisible(False)
            return
        colors = {
            "error": ("rgba(240, 97, 109, 10%)", "#F28B93", ERROR),
            "info":  ("rgba(78, 124, 255, 10%)", ACCENT_SOFT, ACCENT),
        }
        background, foreground, edge = colors.get(tone, colors["error"])
        self.feedback_label.setText(text)
        self.feedback_label.setStyleSheet(
            f"background: {background}; color: {foreground};"
            f"border-left: 3px solid {edge}; border-radius: 6px; padding: 9px 12px;"
        )
        self.feedback_label.setVisible(True)

    def _toggle_log_view(self):
        visible = not self.log_panel.isVisible()
        self.log_panel.setVisible(visible)
        self.log_toggle_btn.setText("收起运行记录" if visible else "查看运行记录")

    def _paste_url(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)
            self._set_feedback()
            self.url_edit.setFocus()
        else:
            self._set_feedback("剪贴板里没有可用的视频链接。", "info")

    def _current_format_index(self) -> int:
        return max(0, self.format_group.checkedId())

    # ── Initialization ───────────────────────────────────────────────
    def _load_initial_state(self):
        idx = self.config.get("lastFormat", 0)
        if 0 <= idx < len(self.format_buttons):
            self.format_buttons[idx].setChecked(True)
        self.path_edit.setText(self.config.get("lastSavePath", os.path.expanduser("~/Downloads")))
        self._set_engine_row(self._ytdlp_dot, self._ytdlp_state, bool(YTDLP_PATH))
        self._set_engine_row(self._ffmpeg_dot, self._ffmpeg_state, bool(FFMPEG_PATH))
        if not YTDLP_PATH:
            self.start_btn.setEnabled(False)
            self.update_btn.setEnabled(False)
            self._set_status("未找到下载引擎", "请把 yt-dlp 放在应用目录，或通过 Homebrew 安装。", "error")

    def _startup_log(self):
        self._append_log("视频下载器已就绪。")
        if YTDLP_PATH:
            self._append_log(f"yt-dlp: {YTDLP_PATH}")
        else:
            self._append_log("警告：未找到 yt-dlp。可通过 brew install yt-dlp 安装。")
        if FFMPEG_PATH:
            self._append_log(f"ffmpeg: 已检测到 ({FFMPEG_PATH})")
        else:
            self._append_log("ffmpeg: 未找到（合并与转换能力会受限）。")
        self._append_log("粘贴视频链接，然后点击“开始下载”。")

    # ── Logging ──────────────────────────────────────────────────────
    MAX_LOG_BLOCKS = 5000

    def _append_log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(f"[{ts}] {text}\n")

        # keep the log bounded so a long session can't grow it forever
        doc = self.log_view.document()
        if doc.blockCount() > self.MAX_LOG_BLOCKS:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor,
                                doc.blockCount() - self.MAX_LOG_BLOCKS)
            cursor.removeSelectedText()

        # auto-scroll
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Signal slots (thread-safe UI updates) ────────────────────────
    @Slot(str)
    def _on_log_line(self, text: str):
        self._append_log(text)

    @Slot(int)
    def _on_progress(self, value: int):
        self.progress_bar.setValue(value)
        self.progress_value_label.setText(f"{value}%")

    @Slot(str)
    def _on_status_text(self, text: str):
        self._set_status(text, self.speed_label.text(), "working")

    @Slot(str)
    def _on_speed_text(self, text: str):
        self.speed_label.setText(text)

    @Slot(int)
    def _on_process_done(self, exit_code: int):
        self.proc = None
        self.reader_thread = None
        temp_files = [self.out_file, self.err_file]
        self.out_file = ""
        self.err_file = ""
        for path in temp_files:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass

        self._set_ui_running(False)
        if exit_code == 0:
            self.progress_bar.setValue(100)
            self.progress_value_label.setText("100%")
            self._set_status("下载完成", "文件已保存到所选文件夹。", "success")
            self._append_log("下载完成。")
        else:
            self._set_status("下载未完成", f"进程退出代码：{exit_code}。可展开详细日志查看原因。", "error")
            self._set_feedback("下载未完成。请确认链接有效，或在“运行记录”中查看具体原因。")
            self._append_log(f"下载失败，退出代码：{exit_code}")

    @Slot(int)
    def _on_update_done(self, exit_code: int):
        self._set_ui_running(False)
        if exit_code == 0:
            self._set_status("下载引擎已更新", "可以继续开始新的下载。", "success")
            self._append_log("yt-dlp 已更新。")
        elif exit_code < 0:
            self._set_status("更新已停止", "下载引擎保持当前版本。", "stopped")
            self._append_log("更新已停止。")
        else:
            self._set_status("更新失败", f"退出代码：{exit_code}。", "error")
            self._append_log(f"更新失败，退出代码：{exit_code}")

    # ── UI state management ──────────────────────────────────────────
    def _set_ui_running(self, running: bool):
        if running:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.update_btn.setEnabled(False)
            for b in self.format_buttons:
                b.setEnabled(False)
            self.url_edit.setEnabled(False)
            self.path_edit.setEnabled(False)
            self.paste_btn.setEnabled(False)
            self.browse_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(bool(YTDLP_PATH))
            self.stop_btn.setEnabled(False)
            self.update_btn.setEnabled(bool(YTDLP_PATH))
            for b in self.format_buttons:
                b.setEnabled(True)
            self.url_edit.setEnabled(True)
            self.path_edit.setEnabled(True)
            self.paste_btn.setEnabled(True)
            self.browse_btn.setEnabled(True)

    # ── Browse folder ────────────────────────────────────────────────
    def _browse_folder(self):
        initial = self.path_edit.text()
        if not os.path.isdir(initial):
            initial = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹", initial)
        if folder:
            self.path_edit.setText(folder)

    # ── Output reader (background thread) ────────────────────────────
    def _reader_loop(self, out_path: str, err_path: str):
        out_pos = 0
        err_pos = 0
        while not self.stop_event.is_set():
            out_pos = self._read_file_lines(out_path, out_pos, "")
            err_pos = self._read_file_lines(err_path, err_pos, "[ERR] ")
            self.stop_event.wait(0.15)
            if self.proc and self.proc.poll() is not None:
                self._read_file_lines(out_path, out_pos, "")
                self._read_file_lines(err_path, err_pos, "[ERR] ")
                self.signals.process_done.emit(self.proc.returncode)
                return

    def _read_file_lines(self, path: str, pos: int, prefix: str) -> int:
        if not path or not os.path.isfile(path):
            return pos
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if pos > os.path.getsize(path):
                    pos = 0
                f.seek(pos)
                for line in f:
                    self._parse_line(line.rstrip("\n\r"), prefix)
                pos = f.tell()
        except Exception:
            pass
        return pos

    def _parse_line(self, line: str, prefix: str):
        display = f"{prefix}{line}" if prefix else line
        self.signals.log_line.emit(display)

        m = RE_PROGRESS.search(line)
        if m:
            pct = int(float(m.group(1)))
            if 0 <= pct <= 100:
                self.signals.progress.emit(pct)
                self.signals.status_text.emit(f"正在下载 · {pct}%")

        m2 = RE_SPEED.search(line)
        m3 = RE_ETA.search(line)
        details = []
        if m2:
            details.append(m2.group(1))
        if m3:
            details.append(f"预计剩余 {m3.group(1)}")
        if details:
            self.signals.speed_text.emit("  ·  ".join(details))

    # ── Actions ──────────────────────────────────────────────────────
    def start_download(self):
        url = self.url_edit.text().strip()
        save_path = self.path_edit.text().strip()
        fmt_index = self._current_format_index()

        if not url:
            self._set_feedback("请输入视频链接。")
            self._set_status("等待视频链接", "粘贴一个链接后，即可开始下载。", "error")
            self.url_edit.setFocus()
            self._append_log("错误：未输入视频链接。")
            return
        if not save_path:
            self._set_feedback("请选择文件保存位置。")
            self._set_status("等待保存位置", "选择一个文件夹后即可继续。", "error")
            self.path_edit.setFocus()
            self._append_log("错误：未选择保存位置。")
            return
        if not YTDLP_PATH:
            self._set_feedback("未找到 yt-dlp 下载引擎。")
            self._set_status("未找到下载引擎", "请检查应用目录或安装 yt-dlp。", "error")
            self._append_log("错误：未找到 yt-dlp。")
            return

        try:
            os.makedirs(save_path, exist_ok=True)
        except Exception as e:
            self._set_feedback("无法创建或访问这个保存文件夹。")
            self._set_status("保存位置不可用", "请选择一个可写入的文件夹。", "error")
            self._append_log(f"错误：无法创建文件夹：{e}")
            return

        self._kill_process()
        self._set_feedback()

        self.config["lastSavePath"] = save_path
        self.config["lastFormat"] = fmt_index
        save_config(self.config)

        self._set_ui_running(True)
        self.progress_bar.setValue(0)
        self.progress_value_label.setText("0%")
        self._set_status("正在解析视频信息", "正在确认可用格式与下载地址。", "working")

        self._append_log("─" * 40)
        self._append_log(f"链接：{url}")
        self._append_log(f"保存到：{save_path}")

        args = build_args(url, save_path, fmt_index)
        self._append_log(f"Cmd: yt-dlp {' '.join(args)}")

        self.out_file = os.path.join(APP_SUPPORT, ".yt-out.tmp")
        self.err_file = os.path.join(APP_SUPPORT, ".yt-err.tmp")
        for f in [self.out_file, self.err_file]:
            try:
                os.remove(f)
            except Exception:
                pass

        try:
            self.proc = subprocess.Popen(
                [YTDLP_PATH] + args,
                stdout=open(self.out_file, "w", encoding="utf-8", errors="replace"),
                stderr=open(self.err_file, "w", encoding="utf-8", errors="replace"),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            self._set_ui_running(False)
            self._set_feedback("无法启动下载进程。请展开运行记录查看原因。")
            self._set_status("无法开始下载", "下载进程没有成功启动。", "error")
            self._append_log(f"错误：{e}")
            return

        self._append_log(f"下载进程已启动（PID: {self.proc.pid}）")
        self._set_status("正在连接下载源", "即将显示下载速度与剩余时间。", "working")

        self.stop_event.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(self.out_file, self.err_file),
            daemon=True,
        )
        self.reader_thread.start()

    def stop_download(self):
        self._append_log("正在停止下载…")
        self._kill_process()
        self._set_ui_running(False)
        self._set_status("下载已停止", "已保留已下载的临时内容。", "stopped")

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        """SIGTERM the process group; escalate to SIGKILL after 3s."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _kill_process(self):
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self._terminate(self.proc)
        if self.update_proc and self.update_proc.poll() is None:
            self._terminate(self.update_proc)
        self.proc = None
        self.update_proc = None
        self.reader_thread = None
        for f in [self.out_file, self.err_file]:
            if f:
                try:
                    os.remove(f)
                except Exception:
                    pass
        self.out_file = ""
        self.err_file = ""

    def update_ytdlp(self):
        if not YTDLP_PATH:
            self._set_feedback("未找到 yt-dlp 下载引擎，无法更新。")
            self._set_status("未找到下载引擎", "请检查应用目录或安装 yt-dlp。", "error")
            self._append_log("未找到 yt-dlp。")
            return
        self._set_feedback()
        self._append_log("正在更新 yt-dlp…")
        self._set_status("正在更新下载引擎", "更新期间无法开始新的下载。", "working")
        self._set_ui_running(True)

        def _run():
            code = 1
            try:
                proc = subprocess.Popen(
                    [YTDLP_PATH, "-U"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                self.update_proc = proc
                for line in proc.stdout:
                    self.signals.log_line.emit(line.rstrip())
                try:
                    code = proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    self._terminate(proc)
                    code = proc.wait()
            except Exception as e:
                self.signals.log_line.emit(f"更新失败：{e}")
            finally:
                self.update_proc = None
                self.signals.update_done.emit(code)

        threading.Thread(target=_run, daemon=True).start()

    def closeEvent(self, event):
        self._kill_process()
        save_config(self.config)
        event.accept()


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # clean cross-platform look, better than macOS Aqua for this purpose

    # Global font fallback (resolved per platform from the token lists)
    font = QFont()
    font.setFamilies(_SANS)
    font.setPointSize(13)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
