#!/usr/bin/env python3
"""Small Tk control panel for Mullvad Speed Guard."""

from __future__ import annotations

import argparse
import traceback
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import List, Optional


APP_DIR = Path(__file__).resolve().parent
GUARD_SCRIPT = APP_DIR / "mullvad_speed_guard.py"
README_PATH = APP_DIR / "README.md"
LOG_PATH = APP_DIR / "results" / "watch_gui.log"
STARTUP_LOG_PATH = APP_DIR / "results" / "gui_startup.log"
PYTHON = "/usr/bin/python3"
APP_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class GuardGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mullvad Speed Guard")
        self.geometry("760x560")
        self.minsize(700, 500)

        self.proc: Optional[subprocess.Popen[str]] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()

        self.health_mode = tk.StringVar(value="adaptive")
        self.speed_check_every = tk.StringVar(value="0")
        self.interval = tk.StringVar(value="60")
        self.min_mbps = tk.StringVar(value="0.5")
        self.max_latency_ms = tk.StringVar(value="2500")
        self.countries = tk.StringVar(value="jp,sg,us")
        self.max_candidates = tk.StringVar(value="20")
        self.status_text = tk.StringVar(value="Idle")

        self._build_ui()
        self.after(150, self._bring_to_front)
        self.after(250, self._drain_log_queue)
        self.after(2000, self.refresh_status)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _bring_to_front(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(1200, lambda: self.attributes("-topmost", False))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        title_row = ttk.Frame(root)
        title_row.pack(fill=tk.X)
        ttk.Label(title_row, text="Mullvad Speed Guard", font=("Helvetica", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_row, textvariable=self.status_text, foreground="#34634a").pack(side=tk.RIGHT)

        controls = ttk.LabelFrame(root, text="Monitor")
        controls.pack(fill=tk.X, pady=(14, 10))

        for index in range(6):
            controls.columnconfigure(index, weight=1)

        ttk.Label(controls, text="Mode").grid(row=0, column=0, sticky=tk.W, padx=8, pady=(10, 4))
        mode = ttk.Combobox(
            controls,
            textvariable=self.health_mode,
            values=("adaptive", "latency", "status", "speed"),
            state="readonly",
            width=12,
        )
        mode.grid(row=1, column=0, sticky=tk.EW, padx=8, pady=(0, 10))

        ttk.Label(controls, text="Interval").grid(row=0, column=1, sticky=tk.W, padx=8, pady=(10, 4))
        ttk.Entry(controls, textvariable=self.interval, width=10).grid(
            row=1, column=1, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        ttk.Label(controls, text="Speed sample").grid(row=0, column=2, sticky=tk.W, padx=8, pady=(10, 4))
        ttk.Entry(controls, textvariable=self.speed_check_every, width=12).grid(
            row=1, column=2, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        ttk.Label(controls, text="Online Mbps").grid(row=0, column=3, sticky=tk.W, padx=8, pady=(10, 4))
        ttk.Entry(controls, textvariable=self.min_mbps, width=10).grid(
            row=1, column=3, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        ttk.Label(controls, text="Max latency").grid(row=0, column=4, sticky=tk.W, padx=8, pady=(10, 4))
        ttk.Entry(controls, textvariable=self.max_latency_ms, width=10).grid(
            row=1, column=4, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        ttk.Label(controls, text="Candidates").grid(row=0, column=5, sticky=tk.W, padx=8, pady=(10, 4))
        ttk.Entry(controls, textvariable=self.max_candidates, width=10).grid(
            row=1, column=5, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        ttk.Label(controls, text="Countries").grid(row=2, column=0, sticky=tk.W, padx=8, pady=(0, 4))
        ttk.Entry(controls, textvariable=self.countries).grid(
            row=3, column=0, columnspan=6, sticky=tk.EW, padx=8, pady=(0, 10)
        )

        button_row = ttk.Frame(root)
        button_row.pack(fill=tk.X, pady=(0, 10))
        self.start_button = ttk.Button(button_row, text="Start", command=self.start_monitor)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(button_row, text="Stop", command=self.stop_monitor, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="Status", command=self.refresh_status).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="Preview Relays", command=self.preview_relays).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="Open Log", command=self.open_log).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="README", command=self.open_readme).pack(side=tk.RIGHT, padx=(0, 8))

        log_frame = ttk.LabelFrame(root, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, wrap=tk.WORD, height=18, padx=8, pady=8)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.configure(yscrollcommand=scrollbar.set)

    def command_env(self) -> dict:
        env = os.environ.copy()
        env["PATH"] = APP_PATH
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def watch_args(self) -> List[str]:
        return [
            PYTHON,
            str(GUARD_SCRIPT),
            "watch",
            "--health-mode",
            self.health_mode.get(),
            "--speed-check-every",
            self.speed_check_every.get().strip() or "0",
            "--interval",
            self.interval.get().strip() or "60",
            "--min-mbps",
            self.min_mbps.get().strip() or "0.5",
            "--max-latency-ms",
            self.max_latency_ms.get().strip() or "2500",
            "--countries",
            self.countries.get().strip() or "jp,sg,us",
            "--blocked-countries",
            "hk",
            "--max-candidates",
            self.max_candidates.get().strip() or "20",
        ]

    def scan_common_args(self) -> List[str]:
        return [
            "--countries",
            self.countries.get().strip() or "jp,sg,us",
            "--blocked-countries",
            "hk",
            "--max-candidates",
            self.max_candidates.get().strip() or "20",
        ]

    def start_monitor(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._append_log(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting monitor\n")
        self.proc = subprocess.Popen(
            self.watch_args(),
            cwd=str(APP_DIR),
            env=self.command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self.reader_thread.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_text.set("Running")

    def stop_monitor(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            self._mark_stopped()
            return
        self._append_log(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Stopping monitor\n")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self._mark_stopped()

    def _mark_stopped(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status_text.set("Stopped")

    def _read_process_output(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.log_queue.put(line)
        self.log_queue.put("[monitor process exited]\n")

    def _drain_log_queue(self) -> None:
        changed = False
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(line)
            changed = True
        if changed and self.proc and self.proc.poll() is not None:
            self._mark_stopped()
        self.after(250, self._drain_log_queue)

    def _append_log(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def run_guard_once(self, args: List[str]) -> str:
        proc = subprocess.run(
            [PYTHON, str(GUARD_SCRIPT)] + args,
            cwd=str(APP_DIR),
            env=self.command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=45,
        )
        return proc.stdout.strip()

    def refresh_status(self) -> None:
        def worker() -> None:
            try:
                output = self.run_guard_once(["status"])
            except Exception as exc:
                output = f"Status failed: {exc}"
            self.log_queue.put(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Status\n{output}\n")

        threading.Thread(target=worker, daemon=True).start()

    def preview_relays(self) -> None:
        def worker() -> None:
            try:
                output = self.run_guard_once(["scan", "--dry-run", "--no-update"] + self.scan_common_args())
            except Exception as exc:
                output = f"Preview failed: {exc}"
            self.log_queue.put(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Preview Relays\n{output}\n")

        threading.Thread(target=worker, daemon=True).start()

    def open_log(self) -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
        subprocess.run(["open", str(LOG_PATH)], check=False)

    def open_readme(self) -> None:
        subprocess.run(["open", str(README_PATH)], check=False)

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Quit", "Stop monitor and quit?"):
                return
            self.stop_monitor()
        self.destroy()


def write_startup_log(message: str) -> None:
    STARTUP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STARTUP_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args, unknown = parser.parse_known_args()
    if unknown:
        write_startup_log(f"Ignored launch arguments: {' '.join(unknown)}")

    if args.self_test:
        missing = [str(path) for path in (GUARD_SCRIPT, README_PATH) if not path.exists()]
        if missing:
            print("missing: " + ", ".join(missing))
            return 1
        print("ok")
        return 0

    try:
        write_startup_log("Starting GUI")
        app = GuardGui()
        app.mainloop()
        write_startup_log("GUI exited")
        return 0
    except Exception:
        write_startup_log(traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
