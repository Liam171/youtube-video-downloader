#!/usr/bin/env python3
"""
yt-dlp Video Downloader — macOS GUI (PySide6)
A native-feeling graphical front-end for yt-dlp on macOS.
"""

from __future__ import annotations

import os
import sys
import json
import time
import shutil
import signal
import threading
import subprocess

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QComboBox, QProgressBar, QTextEdit,
    QFileDialog, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal, Slot
from PySide6.QtGui import QFont, QColor, QPalette, QTextCursor

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
FFMPEG_CANDIDATES = [
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


def build_args(url: str, save_path: str, fmt_index: int) -> list[str]:
    template = os.path.join(save_path, "%(title).200B [%(id)s].%(ext)s")
    args = ["--newline", "--no-playlist", "-o", template]
    if FFMPEG_PATH:
        args += ["--ffmpeg-location", FFMPEG_PATH]

    has_ffmpeg = FFMPEG_PATH is not None

    if fmt_index == 0:
        if has_ffmpeg:
            args += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
        else:
            args += ["-f", "best[ext=mp4]/best", "--merge-output-format", "mp4"]
    elif fmt_index == 1:
        if has_ffmpeg:
            args += ["-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]", "--merge-output-format", "mp4"]
        else:
            args += ["-f", "best[height<=1080][ext=mp4]/best[height<=1080]", "--merge-output-format", "mp4"]
    elif fmt_index == 2:
        if has_ffmpeg:
            args += ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]", "--merge-output-format", "mp4"]
        else:
            args += ["-f", "best[height<=720][ext=mp4]/best[height<=720]", "--merge-output-format", "mp4"]
    else:
        if has_ffmpeg:
            args += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            args += ["-f", "bestaudio[ext=m4a]/bestaudio"]

    args.append(url)
    return args


# ═══════════════════════════════════════════════════════════════════════
#  Signal bridge — thread-safe communication to the Qt main thread
# ═══════════════════════════════════════════════════════════════════════

class WorkerSignals(QObject):
    log_line = Signal(str)
    progress = Signal(int)
    status_text = Signal(str)
    speed_text = Signal(str)
    process_done = Signal(int)


# ═══════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("yt-dlp Video Downloader")
        self.resize(820, 620)
        self.setMinimumSize(680, 480)

        # State
        self.config = load_config()
        self.proc: subprocess.Popen | None = None
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

        # Build UI
        self._build_ui()
        self._load_initial_state()
        self._startup_log()

    # ── UI Construction ──────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        # Light grey background like Mac sidebars
        central.setStyleSheet("background-color: #F0F0F2;")

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        # ── Title ──
        title = QLabel("yt-dlp Video Downloader")
        title.setFont(QFont("Helvetica Neue", 20, QFont.Bold))
        title.setStyleSheet("color: #1D1D1F; background: transparent;")
        root.addWidget(title)

        # ── URL input ──
        root.addWidget(self._make_label("Video URL"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste YouTube / video URL here…")
        self._style_input(self.url_edit)
        root.addWidget(self.url_edit)

        # ── Save path ──
        root.addWidget(self._make_label("Save To"))
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self._style_input(self.path_edit)
        path_row.addWidget(self.path_edit, stretch=1)

        browse_btn = QPushButton(" Browse… ")
        browse_btn.setFont(QFont("Helvetica Neue", 13))
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #0071E3; color: white;
                border: none; border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #0077ED; }
            QPushButton:pressed { background-color: #0068D0; }
        """)
        browse_btn.clicked.connect(self._browse_folder)
        path_row.addWidget(browse_btn)
        root.addLayout(path_row)

        # ── Quality + Status row ──
        mid_row = QHBoxLayout()
        mid_row.setSpacing(16)

        # Quality
        qual_col = QVBoxLayout()
        qual_col.setSpacing(4)
        qual_col.addWidget(self._make_label("Quality"))
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "Best (MP4)", "1080p (MP4)", "720p (MP4)", "Audio Only (MP3)"
        ])
        self.format_combo.setFont(QFont("Helvetica Neue", 13))
        self.format_combo.setMinimumWidth(260)
        self.format_combo.setStyleSheet("""
            QComboBox {
                background: white; border: 1px solid #C6C6C8;
                border-radius: 6px; padding: 8px 12px;
            }
            QComboBox:hover { border-color: #0071E3; }
            QComboBox::drop-down { border: none; width: 24px; }
        """)
        qual_col.addWidget(self.format_combo)
        mid_row.addLayout(qual_col)

        # Status panel
        status_panel = QFrame()
        status_panel.setStyleSheet("""
            QFrame {
                background: white; border: 1px solid #D8D8DC;
                border-radius: 8px;
            }
        """)
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(8)

        status_top = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.status_label.setFont(QFont("Helvetica Neue", 12))
        self.status_label.setStyleSheet("color: #6E6E73; border: none; background: transparent;")
        status_top.addWidget(self.status_label)
        status_top.addStretch()
        self.speed_label = QLabel("")
        self.speed_label.setFont(QFont("Helvetica Neue", 11))
        self.speed_label.setStyleSheet("color: #9CA3AF; border: none; background: transparent;")
        status_top.addWidget(self.speed_label)
        status_layout.addLayout(status_top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #E8E8ED; border: none; border-radius: 4px;
            }
            QProgressBar::chunk {
                background: #0071E3; border-radius: 4px;
            }
        """)
        status_layout.addWidget(self.progress_bar)

        mid_row.addWidget(status_panel, stretch=1)
        root.addLayout(mid_row)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.start_btn = QPushButton("  Start Download  ")
        self.start_btn.setFont(QFont("Helvetica Neue", 13, QFont.Bold))
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #0071E3; color: white;
                border: none; border-radius: 6px;
                padding: 10px 22px;
            }
            QPushButton:hover { background-color: #0077ED; }
            QPushButton:pressed { background-color: #0068D0; }
            QPushButton:disabled { background-color: #A0A0A0; }
        """)
        self.start_btn.clicked.connect(self.start_download)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("  Stop  ")
        self.stop_btn.setFont(QFont("Helvetica Neue", 13))
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: #F0F0F2; color: #1D1D1F;
                border: 1px solid #C6C6C8; border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover { background: #E5E5EA; }
            QPushButton:disabled { color: #A0A0A0; }
        """)
        self.stop_btn.clicked.connect(self.stop_download)
        btn_row.addWidget(self.stop_btn)

        self.update_btn = QPushButton("  Update yt-dlp  ")
        self.update_btn.setFont(QFont("Helvetica Neue", 13))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setStyleSheet("""
            QPushButton {
                background: white; color: #1D1D1F;
                border: 1px solid #C6C6C8; border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover { background: #E5E5EA; }
            QPushButton:disabled { color: #A0A0A0; }
        """)
        self.update_btn.clicked.connect(self.update_ytdlp)
        btn_row.addWidget(self.update_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Log output ──
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo", 11))
        self.log_view.setStyleSheet("""
            QTextEdit {
                background: #1E1E1E; color: #D4D4D4;
                border: 1px solid #444444; border-radius: 6px;
                padding: 10px;
            }
        """)
        root.addWidget(self.log_view, stretch=1)

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Helvetica Neue", 13, QFont.Bold))
        lbl.setStyleSheet("color: #1D1D1F; background: transparent;")
        return lbl

    @staticmethod
    def _style_input(w: QLineEdit):
        w.setFont(QFont("Helvetica Neue", 15))
        w.setStyleSheet("""
            QLineEdit {
                background: white; color: #1D1D1F;
                border: 1px solid #C6C6C8; border-radius: 6px;
                padding: 10px 12px;
            }
            QLineEdit:focus { border-color: #0071E3; }
        """)

    # ── Initialization ───────────────────────────────────────────────
    def _load_initial_state(self):
        self.format_combo.setCurrentIndex(self.config.get("lastFormat", 0))
        self.path_edit.setText(self.config.get("lastSavePath", os.path.expanduser("~/Downloads")))
        if not YTDLP_PATH:
            self.start_btn.setEnabled(False)
            self.update_btn.setEnabled(False)

    def _startup_log(self):
        self._append_log("yt-dlp Downloader ready.")
        if YTDLP_PATH:
            self._append_log(f"yt-dlp: {YTDLP_PATH}")
        else:
            self._append_log("WARNING: yt-dlp not found! Install with: brew install yt-dlp")
        if FFMPEG_PATH:
            self._append_log(f"ffmpeg: detected ({FFMPEG_PATH})")
        else:
            self._append_log("ffmpeg: NOT found (merge/convert limited)")
        self._append_log("Paste a URL and click Start Download.")

    # ── Logging ──────────────────────────────────────────────────────
    def _append_log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(f"[{ts}] {text}\n")
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

    @Slot(str)
    def _on_status_text(self, text: str):
        self.status_label.setText(text)

    @Slot(str)
    def _on_speed_text(self, text: str):
        self.speed_label.setText(text)

    @Slot(int)
    def _on_process_done(self, exit_code: int):
        self.proc = None
        self.reader_thread = None
        self.out_file = ""
        self.err_file = ""

        if exit_code == -1:  # update just finished
            self._set_ui_running(False)
            return

        self._set_ui_running(False)
        if exit_code == 0:
            self.progress_bar.setValue(100)
            self.status_label.setText("Completed")
            self.speed_label.setText("")
            self._append_log("Download completed.")
        else:
            self.status_label.setText(f"Failed (exit: {exit_code})")
            self.speed_label.setText("")
            self._append_log(f"Download failed, exit code: {exit_code}")

    # ── UI state management ──────────────────────────────────────────
    def _set_ui_running(self, running: bool):
        if running:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.update_btn.setEnabled(False)
            self.format_combo.setEnabled(False)
        else:
            self.start_btn.setEnabled(bool(YTDLP_PATH))
            self.stop_btn.setEnabled(False)
            self.update_btn.setEnabled(bool(YTDLP_PATH))
            self.format_combo.setEnabled(True)

    # ── Browse folder ────────────────────────────────────────────────
    def _browse_folder(self):
        initial = self.path_edit.text()
        if not os.path.isdir(initial):
            initial = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Select Save Folder", initial)
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

        import re
        m = re.search(r'\[download\]\s+(\d{1,3}(?:\.\d)?)%', line)
        if m:
            pct = int(float(m.group(1)))
            if 0 <= pct <= 100:
                self.signals.progress.emit(pct)
                self.signals.status_text.emit(f"Downloading... {pct}%")

        m2 = re.search(r'at\s+([\d.]+\s*\w+/s)', line)
        if m2:
            self.signals.speed_text.emit(m2.group(1))

    # ── Actions ──────────────────────────────────────────────────────
    def start_download(self):
        url = self.url_edit.text().strip()
        save_path = self.path_edit.text().strip()
        fmt_index = self.format_combo.currentIndex()

        if not url:
            self._append_log("ERROR: Please enter a video URL.")
            return
        if not YTDLP_PATH:
            self._append_log("ERROR: yt-dlp not found.")
            return

        try:
            os.makedirs(save_path, exist_ok=True)
        except Exception as e:
            self._append_log(f"ERROR: Cannot create folder: {e}")
            return

        self._kill_process()

        self.config["lastSavePath"] = save_path
        self.config["lastFormat"] = fmt_index
        save_config(self.config)

        self._set_ui_running(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Resolving...")
        self.speed_label.setText("")

        self._append_log("-" * 40)
        self._append_log(f"URL: {url}")
        self._append_log(f"Save to: {save_path}")

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
            self._append_log(f"ERROR: {e}")
            return

        self._append_log(f"Started (PID: {self.proc.pid})")
        self.status_label.setText("Downloading...")

        self.stop_event.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(self.out_file, self.err_file),
            daemon=True,
        )
        self.reader_thread.start()

    def stop_download(self):
        self._append_log("Stopping...")
        self._kill_process()
        self._set_ui_running(False)
        self.status_label.setText("Stopped")
        self.speed_label.setText("")

    def _kill_process(self):
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
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
            self._append_log("yt-dlp not found.")
            return
        self._append_log("Updating yt-dlp...")
        self.status_label.setText("Updating...")
        self._set_ui_running(True)

        def _run():
            try:
                result = subprocess.run(
                    [YTDLP_PATH, "-U"],
                    capture_output=True, text=True, timeout=120,
                )
                for line in result.stdout.splitlines():
                    self.signals.log_line.emit(line)
                self.signals.log_line.emit("Update done.")
            except Exception as e:
                self.signals.log_line.emit(f"Update failed: {e}")
            finally:
                self.signals.status_text.emit("Ready")
                self.signals.process_done.emit(-1)

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
    font = QFont("Helvetica Neue", 13)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
