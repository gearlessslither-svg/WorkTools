#!/usr/bin/env python3
"""Always-on-top floating traffic widget for Mullvad Speed Guard."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import tkinter as tk


APP_DIR = Path(__file__).resolve().parent
RESULTS_DIR = APP_DIR / "results"
POSITION_PATH = RESULTS_DIR / "float_widget_position.json"
LOG_PATH = RESULTS_DIR / "float_widget.log"
PANEL_STATE_URL = os.environ.get("MSG_PANEL_STATE_URL", "http://127.0.0.1:18790/api/state")
PANEL_OPEN_URL = os.environ.get("MSG_PANEL_OPEN_URL", "http://127.0.0.1:18790/")
WIDTH = 274
HEIGHT = 124
POLL_SECONDS = 1.25
SLOW_THRESHOLD_MBPS = 5.0


THEMES = {
    "red": {
        "accent": "#ef4444",
        "strip": "#3b1f22",
        "bg": "#1b1e22",
        "text": "#f8fafc",
        "muted": "#a9b0ba",
    },
    "yellow": {
        "accent": "#f2b84b",
        "strip": "#3a2d1c",
        "bg": "#1b1e22",
        "text": "#f8fafc",
        "muted": "#a9b0ba",
    },
    "green": {
        "accent": "#35c46f",
        "strip": "#1f3328",
        "bg": "#1b1e22",
        "text": "#f8fafc",
        "muted": "#a9b0ba",
    },
}


def log(message: str) -> None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def fmt_bytes(value: Optional[int]) -> str:
    if value is None:
        return "--"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{size:.0f} {unit}"
            if size < 10:
                return f"{size:.1f} {unit}"
            return f"{size:.0f} {unit}"
        size /= 1024
    return "--"


def fmt_mbps(value: Optional[float]) -> str:
    if value is None:
        return "--"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


class TrafficSampler:
    def __init__(self) -> None:
        self.last_sample: Optional[Dict[str, Any]] = None

    def fetch(self) -> Dict[str, Any]:
        request = urllib.request.Request(PANEL_STATE_URL, headers={"User-Agent": "mullvad-speed-guard-float/1.1"})
        with urllib.request.urlopen(request, timeout=0.9) as response:
            data = json.loads(response.read().decode("utf-8"))
        return self.compute(data)

    def compute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        traffic = data.get("traffic") if isinstance(data.get("traffic"), dict) else {}
        state = str(connection.get("state") or "Unknown")
        now = time.monotonic()
        download_total = int(traffic.get("download_bytes") or 0)
        upload_total = int(traffic.get("upload_bytes") or 0)
        ok = bool(traffic.get("ok")) and state.lower().startswith("connected")

        down_mbps: Optional[float] = None
        up_mbps: Optional[float] = None
        if self.last_sample:
            elapsed = max(0.01, now - float(self.last_sample["monotonic"]))
            down_delta = download_total - int(self.last_sample["download_total"])
            up_delta = upload_total - int(self.last_sample["upload_total"])
            if down_delta >= 0 and up_delta >= 0:
                down_mbps = (down_delta * 8) / elapsed / 1_000_000
                up_mbps = (up_delta * 8) / elapsed / 1_000_000

        self.last_sample = {
            "monotonic": now,
            "download_total": download_total,
            "upload_total": upload_total,
        }

        current_mbps: Optional[float]
        if down_mbps is None and up_mbps is None:
            current_mbps = None
        else:
            current_mbps = (down_mbps or 0.0) + (up_mbps or 0.0)

        if not ok:
            status = "red"
            state_text = "DISCONNECTED" if not state.lower().startswith("connected") else "NO TUNNEL"
            current_mbps = None
            down_mbps = None
            up_mbps = None
        elif current_mbps is None or current_mbps < SLOW_THRESHOLD_MBPS:
            status = "yellow"
            state_text = "SLOW"
        else:
            status = "green"
            state_text = "FAST"

        return {
            "ok": ok,
            "status": status,
            "state_text": state_text,
            "download_total": download_total,
            "upload_total": upload_total,
            "current_mbps": current_mbps,
            "down_mbps": down_mbps,
            "up_mbps": up_mbps,
            "sampled_at": time.strftime("%H:%M:%S"),
        }


class FloatingTrafficWidget(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VPN Traffic")
        self.geometry(self.initial_geometry())
        self.minsize(WIDTH, HEIGHT)
        self.maxsize(WIDTH, HEIGHT)
        self.resizable(False, False)
        self.configure(bg=THEMES["red"]["bg"])
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.dragging = False
        self.last_geometry: Optional[str] = None
        self.latest: Dict[str, Any] = {
            "status": "red",
            "state_text": "PANEL OFF",
            "download_total": None,
            "upload_total": None,
            "current_mbps": None,
            "down_mbps": None,
            "up_mbps": None,
            "sampled_at": "",
        }
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.stop_event = threading.Event()

        self.build_ui()
        self.bind_tree(self)
        self.render_latest()
        self.after(150, self.raise_window)
        self.after(200, self.drain_events)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.worker = threading.Thread(target=self.poll_loop, name="float-widget-poll", daemon=True)
        self.worker.start()

    def initial_geometry(self) -> str:
        try:
            payload = json.loads(POSITION_PATH.read_text(encoding="utf-8"))
            x = max(0, int(payload.get("x", 80)))
            y = max(0, int(payload.get("y", 80)))
        except Exception:
            x, y = 80, 80
        return f"{WIDTH}x{HEIGHT}+{x}+{y}"

    def build_ui(self) -> None:
        self.outer = tk.Frame(self, bg=THEMES["red"]["bg"], highlightthickness=2, bd=0)
        self.outer.pack(fill=tk.BOTH, expand=True)

        self.top = tk.Frame(self.outer, height=26, bd=0)
        self.top.pack(fill=tk.X)
        self.top.pack_propagate(False)

        self.status_dot = tk.Frame(self.top, width=10, height=10, bd=0)
        self.status_dot.pack(side=tk.LEFT, padx=(10, 6), pady=(8, 8))
        self.status_dot.pack_propagate(False)

        self.title_label = tk.Label(self.top, text="VPN", font=("Helvetica", 10, "bold"), bd=0)
        self.title_label.pack(side=tk.LEFT)

        self.state_label = tk.Label(self.top, text="PANEL OFF", font=("Helvetica", 9, "bold"), bd=0)
        self.state_label.pack(side=tk.LEFT, padx=(12, 0))

        self.grip_label = tk.Label(self.top, text="...", font=("Helvetica", 10, "bold"), bd=0)
        self.grip_label.pack(side=tk.RIGHT, padx=(0, 10))

        self.body = tk.Frame(self.outer, bd=0)
        self.body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(9, 11))

        self.speed_col = tk.Frame(self.body, width=125, bd=0)
        self.speed_col.pack(side=tk.LEFT, fill=tk.Y)
        self.speed_col.pack_propagate(False)

        self.now_caption = tk.Label(self.speed_col, text="NOW", font=("Helvetica", 9, "bold"), bd=0)
        self.now_caption.pack(anchor="w")

        self.speed_row = tk.Frame(self.speed_col, bd=0)
        self.speed_row.pack(anchor="w", pady=(2, 0), fill=tk.X)

        self.speed_value = tk.Label(
            self.speed_row,
            text="--",
            font=("Helvetica", 28, "bold"),
            width=4,
            anchor="w",
            bd=0,
        )
        self.speed_value.pack(side=tk.LEFT)

        self.speed_unit = tk.Label(self.speed_row, text="Mbps", font=("Helvetica", 10, "bold"), bd=0)
        self.speed_unit.pack(side=tk.LEFT, pady=(15, 0))

        self.total_col = tk.Frame(self.body, bd=0)
        self.total_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        self.down_caption = tk.Label(self.total_col, text="DOWN", font=("Helvetica", 9, "bold"), anchor="w", bd=0)
        self.down_caption.pack(fill=tk.X)
        self.down_value = tk.Label(self.total_col, text="--", font=("Helvetica", 13, "bold"), anchor="w", bd=0)
        self.down_value.pack(fill=tk.X, pady=(2, 8))

        self.up_caption = tk.Label(self.total_col, text="UP", font=("Helvetica", 9, "bold"), anchor="w", bd=0)
        self.up_caption.pack(fill=tk.X)
        self.up_value = tk.Label(self.total_col, text="--", font=("Helvetica", 13, "bold"), anchor="w", bd=0)
        self.up_value.pack(fill=tk.X, pady=(2, 0))

    def bind_tree(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self.start_drag)
        widget.bind("<B1-Motion>", self.drag)
        widget.bind("<ButtonRelease-1>", self.end_drag)
        widget.bind("<Double-Button-1>", self.open_panel)
        widget.bind("<Button-2>", self.open_panel)
        widget.bind("<Button-3>", self.open_panel)
        for child in widget.winfo_children():
            self.bind_tree(child)

    def raise_window(self) -> None:
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(1200, self.raise_window)

    def start_drag(self, event: tk.Event) -> None:
        self.dragging = True
        self.drag_offset_x = int(event.x_root) - self.winfo_rootx()
        self.drag_offset_y = int(event.y_root) - self.winfo_rooty()
        self.last_geometry = None

    def drag(self, event: tk.Event) -> None:
        x = int(event.x_root) - self.drag_offset_x
        y = int(event.y_root) - self.drag_offset_y
        max_x = max(0, self.winfo_screenwidth() - WIDTH)
        max_y = max(0, self.winfo_screenheight() - HEIGHT)
        x = min(max(0, x), max_x)
        y = min(max(0, y), max_y)
        geometry = f"{WIDTH}x{HEIGHT}+{x}+{y}"
        if geometry != self.last_geometry:
            self.geometry(geometry)
            self.last_geometry = geometry

    def end_drag(self, event: tk.Event) -> None:
        del event
        self.dragging = False
        self.save_position()
        self.render_latest()

    def save_position(self) -> None:
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            POSITION_PATH.write_text(
                json.dumps({"x": self.winfo_x(), "y": self.winfo_y()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log(f"position save failed: {exc}")

    def open_panel(self, event: Optional[tk.Event] = None) -> None:
        del event
        try:
            subprocess.Popen(["open", PANEL_OPEN_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            log(f"open panel failed: {exc}")

    def close(self) -> None:
        self.stop_event.set()
        self.destroy()

    def poll_loop(self) -> None:
        sampler = TrafficSampler()
        while not self.stop_event.is_set():
            try:
                self.events.put(sampler.fetch())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, RuntimeError) as exc:
                self.events.put(
                    {
                        "status": "red",
                        "state_text": "PANEL OFF",
                        "download_total": None,
                        "upload_total": None,
                        "current_mbps": None,
                        "down_mbps": None,
                        "up_mbps": None,
                        "sampled_at": time.strftime("%H:%M:%S"),
                        "error": str(exc),
                    }
                )
            self.stop_event.wait(POLL_SECONDS)

    def drain_events(self) -> None:
        updated = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("error"):
                log(f"refresh failed: {event['error']}")
                event["download_total"] = self.latest.get("download_total")
                event["upload_total"] = self.latest.get("upload_total")
            self.latest = event
            updated = True

        if updated and not self.dragging:
            self.render_latest()
        self.after(250, self.drain_events)

    def render_latest(self) -> None:
        status = str(self.latest.get("status") or "red")
        theme = THEMES.get(status, THEMES["red"])
        bg = theme["bg"]
        strip = theme["strip"]
        accent = theme["accent"]
        text = theme["text"]
        muted = theme["muted"]

        self.configure(bg=bg)
        self.outer.configure(bg=bg, highlightbackground=accent, highlightcolor=accent)
        self.top.configure(bg=strip)
        self.status_dot.configure(bg=accent)
        for widget in (self.title_label, self.state_label, self.grip_label):
            widget.configure(bg=strip)
        self.title_label.configure(fg=text)
        self.state_label.configure(text=str(self.latest.get("state_text") or "PANEL OFF"), fg=muted)
        self.grip_label.configure(fg=muted)

        for frame in (self.body, self.speed_col, self.speed_row, self.total_col):
            frame.configure(bg=bg)
        for label in (
            self.now_caption,
            self.speed_unit,
            self.down_caption,
            self.up_caption,
        ):
            label.configure(bg=bg, fg=muted)
        self.speed_value.configure(bg=bg, fg=accent)
        self.down_value.configure(bg=bg, fg=text)
        self.up_value.configure(bg=bg, fg=text)

        current_mbps = self.latest.get("current_mbps")
        speed = "--" if status == "red" else fmt_mbps(float(current_mbps) if current_mbps is not None else None)
        self.speed_value.configure(text=speed)
        self.down_value.configure(text=fmt_bytes(self.latest.get("download_total")))
        self.up_value.configure(text=fmt_bytes(self.latest.get("upload_total")))
        self.update_idletasks()
        self.lift()


def main() -> int:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        app = FloatingTrafficWidget()
        app.mainloop()
        return 0
    except Exception as exc:
        log(f"fatal: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
