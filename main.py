"""XBot GUI — PySide6 interface for the Ollama-driven Twitter engagement bot."""

import sys
import threading
import subprocess
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox, QSpinBox, QSlider,
    QPushButton, QGroupBox, QScrollArea, QMessageBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
)
from PySide6.QtGui import QFont, QColor, QTextCursor

from config import Config, load_config, save_config
from profiles import (
    Profile, get_builtin_presets, list_custom_profiles,
    save_profile, delete_profile, find_profile,
)
from bot_engine import BotEngine, RunStatus


class LogBridge(QObject):
    """Bridge to pass log messages from worker thread to GUI thread."""
    log_signal = Signal(str, str)  # (message, level)


class XBotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.engine: BotEngine | None = None
        self.log_bridge = LogBridge()
        self.log_bridge.log_signal.connect(self._append_log)
        self._init_ui()
        self._load_config_to_ui()
        self._start_stats_timer()

    # ── UI Construction ────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("XBot Controller")
        self.setMinimumSize(800, 900)
        self.resize(900, 950)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        # Model section
        layout.addWidget(self._build_model_group())

        # Profile section
        layout.addWidget(self._build_profile_group())

        # Filters section
        layout.addWidget(self._build_filters_group())

        # Schedule section
        layout.addWidget(self._build_schedule_group())

        # Buttons
        layout.addLayout(self._build_button_row())

        # Log
        layout.addWidget(self._build_log_group(), stretch=1)

        # Stats bar
        self.stats_label = QLabel("Today: 0 liked | 0 retweeted | 0 skipped | 0 errors")
        self.stats_label.setStyleSheet("padding: 4px; background: #f0f0f0; font-weight: bold;")
        layout.addWidget(self.stats_label)

    def _build_model_group(self) -> QGroupBox:
        grp = QGroupBox("Model")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Model:"), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self._refresh_models()
        layout.addWidget(self.model_combo, 0, 1)

        self.refresh_models_btn = QPushButton("⟳")
        self.refresh_models_btn.setFixedWidth(30)
        self.refresh_models_btn.setToolTip("Refresh model list")
        self.refresh_models_btn.clicked.connect(self._refresh_models)
        layout.addWidget(self.refresh_models_btn, 0, 2)

        layout.addWidget(QLabel("Context:"), 1, 0)
        self.context_spin = QSpinBox()
        self.context_spin.setRange(1024, 131072)
        self.context_spin.setSingleStep(1024)
        layout.addWidget(self.context_spin, 1, 1)

        layout.addWidget(QLabel("Temperature:"), 2, 0)
        self.temp_spin = QLineEdit("0.3")
        self.temp_spin.setFixedWidth(80)
        layout.addWidget(self.temp_spin, 2, 1)

        layout.addWidget(QLabel("Top-P:"), 2, 2)
        self.top_p_spin = QLineEdit("0.9")
        self.top_p_spin.setFixedWidth(80)
        layout.addWidget(self.top_p_spin, 2, 3)

        return grp

    def _build_profile_group(self) -> QGroupBox:
        grp = QGroupBox("System Profile")
        layout = QVBoxLayout(grp)

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self._refresh_presets()
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self.preset_combo, stretch=1)

        self.save_profile_btn = QPushButton("Save")
        self.save_as_btn = QPushButton("Save As...")
        self.delete_profile_btn = QPushButton("Delete")
        for btn in [self.save_profile_btn, self.save_as_btn, self.delete_profile_btn]:
            preset_row.addWidget(btn)

        self.save_profile_btn.clicked.connect(self._save_current_profile)
        self.save_as_btn.clicked.connect(self._save_as_profile)
        self.delete_profile_btn.clicked.connect(self._delete_profile)

        layout.addLayout(preset_row)

        # System prompt
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(120)
        self.prompt_edit.setMaximumHeight(200)
        layout.addWidget(self.prompt_edit)

        return grp

    def _build_filters_group(self) -> QGroupBox:
        grp = QGroupBox("Filters & Thresholds")
        layout = QGridLayout(grp)
        row = 0

        # Search section
        layout.addWidget(QLabel("Search Terms:"), row, 0)
        self.search_terms_edit = QLineEdit()
        layout.addWidget(self.search_terms_edit, row, 1, 1, 3)
        row += 1

        layout.addWidget(QLabel("Exclude Terms:"), row, 0)
        self.exclude_terms_edit = QLineEdit()
        layout.addWidget(self.exclude_terms_edit, row, 1, 1, 3)
        row += 1

        layout.addWidget(QLabel("Watched Accounts:"), row, 0)
        self.accounts_edit = QLineEdit()
        layout.addWidget(self.accounts_edit, row, 1, 1, 3)
        row += 1

        # Thresholds
        layout.addWidget(QLabel("Min Relevance:"), row, 0)
        self.relevance_slider = QSlider(Qt.Horizontal)
        self.relevance_slider.setRange(0, 100)
        self.relevance_slider.setValue(60)
        self.relevance_label = QLabel("60")
        self.relevance_label.setFixedWidth(30)
        self.relevance_slider.valueChanged.connect(lambda v: self.relevance_label.setText(str(v)))
        layout.addWidget(self.relevance_slider, row, 1, 1, 2)
        layout.addWidget(self.relevance_label, row, 3)
        row += 1

        layout.addWidget(QLabel("Min Quality:"), row, 0)
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(70)
        self.quality_label = QLabel("70")
        self.quality_label.setFixedWidth(30)
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        layout.addWidget(self.quality_slider, row, 1, 1, 2)
        layout.addWidget(self.quality_label, row, 3)
        row += 1

        layout.addWidget(QLabel("Max Age (hours):"), row, 0)
        self.max_age_spin = QSpinBox()
        self.max_age_spin.setRange(1, 168)
        self.max_age_spin.setValue(24)
        layout.addWidget(self.max_age_spin, row, 1)
        row += 1

        layout.addWidget(QLabel("Language:"), row, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["en", "all"])
        layout.addWidget(self.lang_combo, row, 1)
        row += 1

        # Checkboxes
        self.videos_only_cb = QCheckBox("Videos Only")
        self.verified_only_cb = QCheckBox("Verified Only")
        self.like_cb = QCheckBox("Auto-Like")
        self.retweet_cb = QCheckBox("Auto-Retweet")
        self.reply_cb = QCheckBox("Auto-Reply")
        self.download_cb = QCheckBox("Download Video")
        for cb in [self.videos_only_cb, self.verified_only_cb, self.like_cb, self.retweet_cb, self.reply_cb, self.download_cb]:
            layout.addWidget(cb, row, 1 if cb in [self.videos_only_cb, self.like_cb, self.reply_cb] else 2)
            if cb in [self.like_cb, self.reply_cb]:
                row += 1

        row += 1
        layout.addWidget(QLabel("Max Actions/Hour:"), row, 0)
        self.max_hour_spin = QSpinBox()
        self.max_hour_spin.setRange(1, 100)
        self.max_hour_spin.setValue(10)
        layout.addWidget(self.max_hour_spin, row, 1)

        layout.addWidget(QLabel("Max Actions/Day:"), row, 2)
        self.max_day_spin = QSpinBox()
        self.max_day_spin.setRange(1, 500)
        self.max_day_spin.setValue(50)
        layout.addWidget(self.max_day_spin, row, 3)

        return grp

    def _build_schedule_group(self) -> QGroupBox:
        grp = QGroupBox("Schedule")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Run every:"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 1440)
        self.interval_spin.setSuffix(" min")
        self.interval_spin.setValue(60)
        layout.addWidget(self.interval_spin, 0, 1)

        self.schedule_enabled_cb = QCheckBox("Enabled")
        layout.addWidget(self.schedule_enabled_cb, 0, 2)

        layout.addWidget(QLabel("Active hours:"), 1, 0)
        self.start_time_edit = QLineEdit("06:00")
        self.start_time_edit.setFixedWidth(60)
        self.end_time_edit = QLineEdit("23:00")
        self.end_time_edit.setFixedWidth(60)
        layout.addWidget(self.start_time_edit, 1, 1)
        layout.addWidget(QLabel("to"), 1, 2)
        layout.addWidget(self.end_time_edit, 1, 3)

        return grp

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.start_btn = QPushButton("▶  START")
        self.start_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        self.start_btn.clicked.connect(self._start_bot)

        self.pause_btn = QPushButton("⏸  PAUSE")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._pause_bot)

        self.stop_btn = QPushButton("⏹  STOP")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_bot)

        self.dry_run_btn = QPushButton("🔍  DRY RUN")
        self.dry_run_btn.clicked.connect(self._dry_run)

        self.save_config_btn = QPushButton("💾 Save Config")
        self.save_config_btn.clicked.connect(self._save_config)

        for btn in [self.start_btn, self.pause_btn, self.stop_btn, self.dry_run_btn, self.save_config_btn]:
            row.addWidget(btn)

        return row

    def _build_log_group(self) -> QGroupBox:
        grp = QGroupBox("Activity Log")
        layout = QVBoxLayout(grp)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 9))
        self.log_edit.setStyleSheet("QTextEdit { background: #1e1e1e; color: #d4d4d4; }")
        layout.addWidget(self.log_edit)

        return grp

    # ── Data Loading ───────────────────────────────────────────────

    def _refresh_models(self):
        """Populate model dropdown from ollama list."""
        self.model_combo.clear()
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n")[1:]:  # skip header
                    parts = line.split()
                    if parts:
                        name = parts[0]
                        size = parts[2] if len(parts) > 2 else ""
                        label = f"{name}  ({size})" if size else name
                        self.model_combo.addItem(label, name)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Select current config model
        idx = self.model_combo.findData(self.config.model.name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)

    def _refresh_presets(self):
        """Populate preset dropdown with builtins + custom."""
        self.preset_combo.clear()
        self.preset_combo.addItem("(custom)", "")
        for p in get_builtin_presets():
            self.preset_combo.addItem(p.name, p.name)
        for name in list_custom_profiles():
            self.preset_combo.addItem(f"{name} (custom)", name)

    def _load_config_to_ui(self):
        """Load config values into UI widgets."""
        # Model
        self.context_spin.setValue(self.config.model.context_window)
        self.temp_spin.setText(str(self.config.model.temperature))
        self.top_p_spin.setText(str(self.config.model.top_p))

        # Profile
        self.prompt_edit.setPlainText(self.config.system_prompt)

        # Filters
        self.search_terms_edit.setText(", ".join(self.config.filters.search_terms))
        self.exclude_terms_edit.setText(", ".join(self.config.filters.exclude_terms))
        self.accounts_edit.setText(", ".join(self.config.filters.watched_accounts))
        self.relevance_slider.setValue(self.config.thresholds.min_relevance)
        self.quality_slider.setValue(self.config.thresholds.min_quality)
        self.max_age_spin.setValue(self.config.filters.max_age_hours)
        self.lang_combo.setCurrentText(self.config.filters.language)
        self.videos_only_cb.setChecked(self.config.filters.videos_only)
        self.like_cb.setChecked(self.config.actions.like)
        self.retweet_cb.setChecked(self.config.actions.retweet)
        self.reply_cb.setChecked(self.config.actions.reply)
        self.download_cb.setChecked(self.config.actions.download_video)
        self.max_hour_spin.setValue(self.config.rate_limit.max_per_hour)
        self.max_day_spin.setValue(self.config.rate_limit.max_per_day)

        # Schedule
        self.interval_spin.setValue(self.config.schedule.interval_minutes)
        self.schedule_enabled_cb.setChecked(self.config.schedule.enabled)
        self.start_time_edit.setText(self.config.schedule.active_hours_start)
        self.end_time_edit.setText(self.config.schedule.active_hours_end)

    def _collect_config(self) -> Config:
        """Read current UI values into a Config object."""
        cfg = self.config
        cfg.model.name = self.model_combo.currentData() or self.model_combo.currentText()
        cfg.model.context_window = self.context_spin.value()
        cfg.model.temperature = float(self.temp_spin.text or "0.3")
        cfg.model.top_p = float(self.top_p_spin.text or "0.9")
        cfg.system_prompt = self.prompt_edit.toPlainText()
        cfg.filters.search_terms = [t.strip() for t in self.search_terms_edit.text().split(",") if t.strip()]
        cfg.filters.exclude_terms = [t.strip() for t in self.exclude_terms_edit.text().split(",") if t.strip()]
        cfg.filters.watched_accounts = [t.strip() for t in self.accounts_edit.text().split(",") if t.strip()]
        cfg.thresholds.min_relevance = self.relevance_slider.value()
        cfg.thresholds.min_quality = self.quality_slider.value()
        cfg.filters.max_age_hours = self.max_age_spin.value()
        cfg.filters.language = self.lang_combo.currentText()
        cfg.filters.videos_only = self.videos_only_cb.isChecked()
        cfg.actions.like = self.like_cb.isChecked()
        cfg.actions.retweet = self.retweet_cb.isChecked()
        cfg.actions.reply = self.reply_cb.isChecked()
        cfg.actions.download_video = self.download_cb.isChecked()
        cfg.rate_limit.max_per_hour = self.max_hour_spin.value()
        cfg.rate_limit.max_per_day = self.max_day_spin.value()
        cfg.schedule.interval_minutes = self.interval_spin.value()
        cfg.schedule.enabled = self.schedule_enabled_cb.isChecked()
        cfg.schedule.active_hours_start = self.start_time_edit.text()
        cfg.schedule.active_hours_end = self.end_time_edit.text()
        return cfg

    # ── Actions ────────────────────────────────────────────────────

    def _save_config(self):
        cfg = self._collect_config()
        save_config(cfg)
        self.config = cfg
        self._append_log("Config saved", "INFO")

    def _on_preset_selected(self, name: str):
        if not name or name == "(custom)":
            return
        profile = find_profile(name)
        if profile:
            self.prompt_edit.setPlainText(profile.system_prompt)
            self.search_terms_edit.setText(", ".join(profile.search_terms))
            self.exclude_terms_edit.setText(", ".join(profile.exclude_terms))
            self.relevance_slider.setValue(profile.min_relevance)
            self.quality_slider.setValue(profile.min_quality)
            self.videos_only_cb.setChecked(profile.videos_only)

    def _save_current_profile(self):
        name = self.preset_combo.currentText()
        if not name or name == "(custom)":
            self._save_as_profile()
            return
        profile = Profile(
            name=name,
            system_prompt=self.prompt_edit.toPlainText(),
            search_terms=[t.strip() for t in self.search_terms_edit.text().split(",") if t.strip()],
            exclude_terms=[t.strip() for t in self.exclude_terms_edit.text().split(",") if t.strip()],
            min_relevance=self.relevance_slider.value(),
            min_quality=self.quality_slider.value(),
            videos_only=self.videos_only_cb.isChecked(),
        )
        save_profile(profile)
        self._refresh_presets()
        self._append_log(f"Profile '{name}' saved", "INFO")

    def _save_as_profile(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Profile As", "Profile name:")
        if ok and name:
            profile = Profile(
                name=name,
                system_prompt=self.prompt_edit.toPlainText(),
                search_terms=[t.strip() for t in self.search_terms_edit.text().split(",") if t.strip()],
                exclude_terms=[t.strip() for t in self.exclude_terms_edit.text().split(",") if t.strip()],
                min_relevance=self.relevance_slider.value(),
                min_quality=self.quality_slider.value(),
                videos_only=self.videos_only_cb.isChecked(),
            )
            save_profile(profile)
            self._refresh_presets()
            self.preset_combo.setCurrentText(name)
            self._append_log(f"Profile '{name}' created", "INFO")

    def _delete_profile(self):
        name = self.preset_combo.currentText()
        if not name or name == "(custom)":
            return
        reply = QMessageBox.question(self, "Delete Profile", f"Delete profile '{name}'?")
        if reply == QMessageBox.Yes:
            delete_profile(name)
            self._refresh_presets()
            self._append_log(f"Profile '{name}' deleted", "INFO")

    def _start_bot(self):
        """Start the bot in a background thread."""
        cfg = self._collect_config()
        save_config(cfg)
        self.config = cfg

        self.engine = BotEngine(cfg, log_callback=self._threaded_log)
        self.engine.set_dry_run(False)

        self.start_btn.setEnabled(False)
        self.dry_run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._run_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._run_thread.start()

    def _dry_run(self):
        """Run once in dry-run mode (score only, no actions)."""
        cfg = self._collect_config()
        save_config(cfg)
        self.config = cfg

        self.engine = BotEngine(cfg, log_callback=self._threaded_log)
        self.engine.set_dry_run(True)

        self._append_log("=== DRY RUN ===", "WARN")

        self._run_thread = threading.Thread(target=self._dry_run_once, daemon=True)
        self._run_thread.start()

    def _dry_run_once(self):
        try:
            result = self.engine.run_dry()
            self._append_log(f"Dry run complete: {result.message}", "INFO")
        except Exception as e:
            self._threaded_log(f"Fatal error: {e}", "ERROR")
        finally:
            self.engine = None

    def _run_loop(self):
        """Main bot loop running in background thread."""
        interval = self.config.schedule.interval_minutes
        while self.engine and self.engine._running:
            try:
                result = self.engine.run_once()
                if result.status == RunStatus.SKIPPED:
                    self._threaded_log(f"Run skipped: {result.message}", "WARN")
                else:
                    self._threaded_log(
                        f"Run complete: {result.engaged} engaged, "
                        f"{result.skipped} skipped, {result.errors} errors",
                        "INFO"
                    )
            except Exception as e:
                self._threaded_log(f"Fatal error: {e}", "ERROR")

            # Wait for next interval
            for _ in range(interval * 60):
                if not self.engine or not self.engine._running:
                    break
                import time
                time.sleep(1)

        self._threaded_log("Bot stopped", "INFO")

    def _pause_bot(self):
        if self.engine:
            self.engine._running = False
            self._threaded_log("Bot pausing after current run...", "WARN")

    def _stop_bot(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.start_btn.setEnabled(True)
        self.dry_run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)

    # ── Logging ────────────────────────────────────────────────────

    def _threaded_log(self, msg: str, level: str = "INFO"):
        """Called from worker thread — emits signal to GUI thread."""
        self.log_bridge.log_signal.emit(msg, level)

    def _append_log(self, msg: str, level: str = "INFO"):
        """Append to the log widget (must be called from GUI thread)."""
        color = {
            "INFO": "#d4d4d4",
            "WARN": "#ffcc00",
            "ERROR": "#ff4444",
        }.get(level, "#d4d4d4")

        ts = datetime.now().strftime("%H:%M:%S")
        self.log_edit.append(f'<span style="color:{color}">{ts}  {level:<5}  {msg}</span>')
        self.log_edit.moveCursor(QTextCursor.End)

    # ── Stats Timer ────────────────────────────────────────────────

    def _start_stats_timer(self):
        """Refresh stats every 30 seconds."""
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(30000)
        self._refresh_stats()

    def _refresh_stats(self):
        try:
            from database import Database
            db = Database()
            stats = db.get_recent_stats(24)
            liked = stats.get("like_success", 0)
            retweeted = stats.get("retweet_success", 0)
            skipped = stats.get("skip_skip", 0)
            errors = stats.get("like_fail", 0) + stats.get("retweet_fail", 0) + stats.get("error_error", 0)
            self.stats_label.setText(
                f"Today: {liked} liked | {retweeted} retweeted | {skipped} skipped | {errors} errors"
            )
            db.close()
        except Exception:
            pass

    # ── Cleanup ────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.engine:
            self.engine.stop()
        cfg = self._collect_config()
        save_config(cfg)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = XBotWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()