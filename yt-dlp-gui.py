#!/usr/bin/env python3
"""
yt-dlp Video Downloader — macOS GUI (PySide6)
A native-feeling graphical front-end for yt-dlp on macOS.
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
    QComboBox, QProgressBar, QTextEdit,
    QFileDialog, QFrame,
)
from PySide6.QtCore import Qt, QObject, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor

# ═══════════════════════════════════════════════════════════════════════
#  Paths & Helpers
# ═══════════════════════════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

YTDLP_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "yt-dlp"),
    os.path.join(SCRIPT_DIR, "yt-dlp_macos"),
    "/usr/local/bin/yt-dlp",
    "/opt/homebrew/bin/yt-dlp",
]
# Bundled ffmpeg is arch-specific (ffmpeg_arm64 / ffmpeg_x64);
# also accept a plain "ffmpeg" next to the script and Homebrew installs.
_BUNDLED_FFMPEG = os.path.join(
    SCRIPT_DIR, "ffmpeg_arm64" if platform.machine() == "arm64" else "ffmpeg_x64"
)
FFMPEG_CANDIDATES = [
    _BUNDLED_FFMPEG,
    os.path.join(SCRIPT_DIR, "ffmpeg"),
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
#  Main Window
# ═══════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    # Stone, graphite and cobalt form the visual system. Blue is an action
    # colour, never a large decorative surface.
    _QSS_BTN_PRIMARY = """
        QPushButton {
            background-color: #356DB3; color: #FFFFFF;
            border: 1px solid #356DB3; border-radius: 4px;
            padding: 11px 20px;
        }
        QPushButton:hover { background-color: #2A5F9F; border-color: #2A5F9F; }
        QPushButton:pressed { background-color: #214E84; border-color: #214E84; padding-top: 12px; padding-bottom: 10px; }
        QPushButton:focus { border: 2px solid #8FB3E2; }
        QPushButton:disabled { background-color: #B9C3CD; border-color: #B9C3CD; color: #E9EEF3; }
    """
    _QSS_BTN_SECONDARY = """
        QPushButton {
            background: transparent; color: #314256;
            border: 1px solid #B9C5D0; border-radius: 4px;
            padding: 10px 15px;
        }
        QPushButton:hover { background: #F7F9FB; border-color: #7F9FC5; color: #244F87; }
        QPushButton:pressed { background: #E2E8EE; padding-top: 11px; padding-bottom: 9px; }
        QPushButton:focus { border: 2px solid #8FB3E2; }
        QPushButton:disabled { color: #97A4B0; border-color: #D2DAE2; }
    """
    _QSS_PROGRESS = """
        QProgressBar {
            background: #C6D0DA; border: none; border-radius: 2px;
        }
        QProgressBar::chunk {
            background: #356DB3; border-radius: 2px;
        }
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频下载器")
        self.resize(980, 700)
        self.setMinimumSize(800, 570)

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
        central.setStyleSheet("QWidget#central { background-color: #E9EEF2; }")

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The rail is product identity and quiet system state, not a promo panel.
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setMinimumWidth(268)
        rail.setMaximumWidth(296)
        rail.setStyleSheet("""
            QFrame#rail {
                background: #18212B;
            }
        """)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(29, 30, 28, 27)
        rail_layout.setSpacing(0)

        product_mark = QLabel("MEDIA / DOWNLOAD")
        product_mark.setFont(QFont("Menlo", 9, QFont.DemiBold))
        product_mark.setStyleSheet("color: #86A9D1; letter-spacing: 1.2px;")
        rail_layout.addWidget(product_mark)

        rail_layout.addSpacing(25)
        rail_accent = QFrame()
        rail_accent.setFixedSize(42, 3)
        rail_accent.setStyleSheet("background: #356DB3;")
        rail_layout.addWidget(rail_accent)
        rail_layout.addSpacing(18)
        rail_title = QLabel("视频\n下载器")
        rail_title.setFont(QFont("Avenir Next", 29, QFont.DemiBold))
        rail_title.setStyleSheet("color: #F5F7FA; line-height: 1.08;")
        rail_layout.addWidget(rail_title)
        rail_layout.addSpacing(12)
        rail_copy = QLabel("专注处理链接、格式与文件。\n所有下载记录都保留在这台 Mac。")
        rail_copy.setFont(QFont("Avenir Next", 11))
        rail_copy.setStyleSheet("color: #ABBAC9; line-height: 1.5;")
        rail_layout.addWidget(rail_copy)

        rail_layout.addStretch()
        rail_rule = QFrame()
        rail_rule.setFixedHeight(1)
        rail_rule.setStyleSheet("background: #3B4856;")
        rail_layout.addWidget(rail_rule)
        rail_layout.addSpacing(16)
        rail_status_caption = QLabel("下载状态")
        rail_status_caption.setFont(QFont("Avenir Next", 10, QFont.DemiBold))
        rail_status_caption.setStyleSheet("color: #8091A2;")
        rail_layout.addWidget(rail_status_caption)
        rail_layout.addSpacing(6)
        self.status_badge = QLabel("已就绪")
        self.status_badge.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_badge.setFixedHeight(28)
        self.status_badge.setFont(QFont("Avenir Next", 12, QFont.DemiBold))
        rail_layout.addWidget(self.status_badge)
        rail_layout.addSpacing(14)
        rail_footer = QLabel("MACOS  ·  LOCAL-FIRST")
        rail_footer.setFont(QFont("Menlo", 9))
        rail_footer.setStyleSheet("color: #6F8499;")
        rail_layout.addWidget(rail_footer)
        root.addWidget(rail)

        workspace = QFrame()
        workspace.setObjectName("workspace")
        workspace.setStyleSheet("""
            QFrame#workspace {
                background: #E9EEF2;
            }
        """)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(44, 33, 48, 27)
        workspace_layout.setSpacing(0)

        workspace_header = QHBoxLayout()
        header_copy = QVBoxLayout()
        header_copy.setSpacing(4)
        eyebrow = QLabel("视频下载")
        eyebrow.setFont(QFont("Avenir Next", 10, QFont.DemiBold))
        eyebrow.setStyleSheet("color: #547397;")
        header_copy.addWidget(eyebrow)
        title = QLabel("新建下载")
        title.setFont(QFont("Avenir Next", 27, QFont.DemiBold))
        title.setStyleSheet("color: #162434;")
        header_copy.addWidget(title)
        workspace_header.addLayout(header_copy)
        workspace_header.addStretch()
        engine_label = QLabel("YT-DLP · LOCAL ENGINE")
        engine_label.setFont(QFont("Menlo", 9))
        engine_label.setStyleSheet("color: #66788B; padding: 5px 0;")
        workspace_header.addWidget(engine_label, alignment=Qt.AlignTop | Qt.AlignRight)
        workspace_layout.addLayout(workspace_header)
        workspace_layout.addSpacing(32)

        form_panel = QFrame()
        form_panel.setObjectName("formPanel")
        form_panel.setStyleSheet("""
            QFrame#formPanel {
                background: transparent; border: none;
            }
        """)
        composer_layout = QVBoxLayout(form_panel)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(12)

        link_heading = QHBoxLayout()
        link_heading.addWidget(self._make_label("视频链接"))
        link_heading.addStretch()
        link_hint = QLabel("支持 YouTube 及 yt-dlp 兼容网站")
        link_hint.setFont(QFont("Avenir Next", 10))
        link_hint.setStyleSheet("color: #798898;")
        link_heading.addWidget(link_hint)
        composer_layout.addLayout(link_heading)
        url_row = QHBoxLayout()
        url_row.setSpacing(9)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴视频链接")
        self._style_input(self.url_edit, large=True)
        self.url_edit.returnPressed.connect(self.start_download)
        url_row.addWidget(self.url_edit, stretch=1)
        self.paste_btn = self._make_button("粘贴", primary=False)
        self.paste_btn.setFixedWidth(74)
        self.paste_btn.clicked.connect(self._paste_url)
        url_row.addWidget(self.paste_btn)
        composer_layout.addLayout(url_row)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setVisible(False)
        composer_layout.addWidget(self.feedback_label)
        composer_layout.addSpacing(10)
        settings_rule = QFrame()
        settings_rule.setFixedHeight(1)
        settings_rule.setStyleSheet("background: #C9D2DB;")
        composer_layout.addWidget(settings_rule)
        composer_layout.addSpacing(6)

        settings_row = QVBoxLayout()
        settings_row.setSpacing(12)

        format_col = QVBoxLayout()
        format_col.setSpacing(6)
        format_col.addWidget(self._make_label("下载格式"))
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "最佳质量 · MP4", "最高 1080p · MP4", "最高 720p · MP4", "仅音频 · MP3"
        ])
        self.format_combo.setFont(QFont("Avenir Next", 12, QFont.Medium))
        self.format_combo.setMinimumWidth(250)
        self.format_combo.setStyleSheet("""
            QComboBox {
                background: #F8FAFB; color: #263442; border: 1px solid #BBC7D1;
                border-radius: 4px; padding: 10px 34px 10px 12px;
            }
            QComboBox:hover { background: #FFFFFF; border-color: #829FBE; }
            QComboBox:focus { background: #FFFFFF; border: 2px solid #7EA7D8; }
            QComboBox::drop-down { border: none; width: 30px; }
        """)
        format_col.addWidget(self.format_combo)
        settings_row.addLayout(format_col)

        path_col = QVBoxLayout()
        path_col.setSpacing(6)
        path_col.addWidget(self._make_label("保存位置"))
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择文件夹")
        self._style_input(self.path_edit)
        path_row.addWidget(self.path_edit, stretch=1)
        self.browse_btn = self._make_button("选择…", primary=False)
        self.browse_btn.clicked.connect(self._browse_folder)
        path_row.addWidget(self.browse_btn)
        path_col.addLayout(path_row)
        settings_row.addLayout(path_col)
        composer_layout.addLayout(settings_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(9)
        self.start_btn = self._make_button("开始下载", primary=True, bold=True)
        self.start_btn.setMinimumWidth(130)
        self.start_btn.clicked.connect(self.start_download)
        action_row.addWidget(self.start_btn)
        self.stop_btn = self._make_button("停止", primary=False)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_download)
        action_row.addWidget(self.stop_btn)
        action_row.addStretch()
        self.update_btn = self._make_button("更新下载引擎", primary=False)
        self.update_btn.clicked.connect(self.update_ytdlp)
        action_row.addWidget(self.update_btn)
        composer_layout.addLayout(action_row)
        workspace_layout.addWidget(form_panel)
        workspace_layout.addSpacing(38)

        activity = QWidget()
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.setSpacing(10)

        activity_caption = QLabel("下载进度")
        activity_caption.setFont(QFont("Avenir Next", 10, QFont.DemiBold))
        activity_caption.setStyleSheet("color: #547397;")
        activity_layout.addWidget(activity_caption)

        activity_top = QHBoxLayout()
        activity_top.setSpacing(10)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        activity_top.addWidget(self.status_dot)
        self.status_label = QLabel("准备就绪")
        self.status_label.setFont(QFont("Avenir Next", 14, QFont.DemiBold))
        self.status_label.setStyleSheet("color: #223247;")
        activity_top.addWidget(self.status_label)
        activity_top.addStretch()
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setFont(QFont("Menlo", 12, QFont.DemiBold))
        self.progress_value_label.setStyleSheet("color: #456B98;")
        activity_top.addWidget(self.progress_value_label)
        activity_layout.addLayout(activity_top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet(self._QSS_PROGRESS)
        activity_layout.addWidget(self.progress_bar)

        self.speed_label = QLabel("粘贴一个链接后，即可开始下载。")
        self.speed_label.setFont(QFont("Avenir Next", 12))
        self.speed_label.setStyleSheet("color: #697A8D;")
        activity_layout.addWidget(self.speed_label)
        workspace_layout.addWidget(activity)
        workspace_layout.addStretch()

        self.log_toggle_btn = QPushButton("查看运行记录")
        self.log_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.log_toggle_btn.setFont(QFont("Avenir Next", 11, QFont.Medium))
        self.log_toggle_btn.setStyleSheet("""
            QPushButton { color: #5B718A; background: transparent; border: none; text-align: left; padding: 7px 0; }
            QPushButton:hover { color: #356DB3; }
            QPushButton:focus { color: #356DB3; }
        """)
        self.log_toggle_btn.clicked.connect(self._toggle_log_view)
        workspace_layout.addWidget(self.log_toggle_btn)

        self.log_panel = QFrame()
        self.log_panel.setObjectName("logPanel")
        self.log_panel.setStyleSheet("QFrame#logPanel { background: #1C2733; border: none; }")
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(14, 12, 14, 12)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(130)
        self.log_view.setMaximumHeight(170)
        self.log_view.setFont(QFont("Menlo", 11))
        self.log_view.setStyleSheet("QTextEdit { background: transparent; color: #C7D4E1; border: none; padding: 3px; }")
        log_layout.addWidget(self.log_view)
        self.log_panel.setVisible(False)
        workspace_layout.addWidget(self.log_panel)

        self._set_status("准备就绪", "粘贴一个链接后，即可开始下载。", "ready")
        root.addWidget(workspace, stretch=1)

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Avenir Next", 12, QFont.DemiBold))
        lbl.setStyleSheet("color: #26394F; background: transparent;")
        return lbl

    @classmethod
    def _make_button(cls, text: str, primary: bool, bold: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Avenir Next", 13, QFont.DemiBold if bold else QFont.Medium))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(cls._QSS_BTN_PRIMARY if primary else cls._QSS_BTN_SECONDARY)
        return btn

    @staticmethod
    def _style_input(w: QLineEdit, large: bool = False):
        w.setFont(QFont("Avenir Next", 15 if large else 13,
                        QFont.Medium if large else QFont.Normal))
        w.setStyleSheet("""
            QLineEdit {
                background: #F8FAFB; color: #263442;
                border: 1px solid #BBC7D1; border-radius: 4px;
                padding: 10px 12px;
            }
            QLineEdit:hover { background: #FFFFFF; border-color: #829FBE; }
            QLineEdit:focus { background: #FFFFFF; border: 2px solid #7EA7D8; }
        """)

    def _set_status(self, title: str, detail: str = "", tone: str = "ready"):
        tones = {
            "ready": ("#356DB3", "#91B4DF", "已就绪"),
            "working": ("#356DB3", "#A9C6E9", "处理中"),
            "success": ("#4C8A67", "#A6CDB4", "已完成"),
            "error": ("#B85C69", "#E4B0B8", "需处理"),
            "stopped": ("#748291", "#B6C1CC", "已停止"),
        }
        dot, badge_fg, badge_text = tones.get(tone, tones["ready"])
        self.status_label.setText(title)
        self.speed_label.setText(detail)
        self.status_dot.setStyleSheet(f"background: {dot}; border-radius: 4px;")
        self.status_badge.setText(badge_text)
        self.status_badge.setStyleSheet(
            f"background: transparent; color: {badge_fg}; padding: 0;"
        )

    def _set_feedback(self, text: str = "", tone: str = "error"):
        if not text:
            self.feedback_label.clear()
            self.feedback_label.setVisible(False)
            return
        colors = {
            "error": ("#F1E0E3", "#823844"),
            "info": ("#DFEAF5", "#244F87"),
        }
        background, foreground = colors.get(tone, colors["error"])
        self.feedback_label.setText(text)
        self.feedback_label.setStyleSheet(
            f"background: {background}; color: {foreground}; border-left: 3px solid {foreground}; padding: 8px 10px;"
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

    # ── Initialization ───────────────────────────────────────────────
    def _load_initial_state(self):
        self.format_combo.setCurrentIndex(self.config.get("lastFormat", 0))
        self.path_edit.setText(self.config.get("lastSavePath", os.path.expanduser("~/Downloads")))
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
            self._set_feedback("下载未完成。请确认链接有效，或在“详细日志”中查看具体原因。")
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
            self.format_combo.setEnabled(False)
            self.url_edit.setEnabled(False)
            self.path_edit.setEnabled(False)
            self.paste_btn.setEnabled(False)
            self.browse_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(bool(YTDLP_PATH))
            self.stop_btn.setEnabled(False)
            self.update_btn.setEnabled(bool(YTDLP_PATH))
            self.format_combo.setEnabled(True)
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
        fmt_index = self.format_combo.currentIndex()

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

        self.out_file = os.path.join(SCRIPT_DIR, ".yt-out.tmp")
        self.err_file = os.path.join(SCRIPT_DIR, ".yt-err.tmp")
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
            self._set_feedback("无法启动下载进程。请展开详细日志查看原因。")
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

    # Global font fallback
    font = QFont("Avenir Next", 13)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
