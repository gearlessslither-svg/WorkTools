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
from typing import Any, Dict, Optional, Tuple

import tkinter as tk


APP_DIR = Path(__file__).resolve().parent
RESULTS_DIR = APP_DIR / "results"
POSITION_PATH = RESULTS_DIR / "float_widget_position.json"
LOG_PATH = RESULTS_DIR / "float_widget.log"
PANEL_STATE_URL = os.environ.get("MSG_PANEL_STATE_URL", "http://127.0.0.1:18790/api/state")
PANEL_OPEN_URL = os.environ.get("MSG_PANEL_OPEN_URL", "http://127.0.0.1:18790/")
WIDTH = 260
HEIGHT = 120
POLL_SECONDS = 1.5
SLOW_THRESHOLD_MBPS = 5.0


COLORS = {
    "red": {
        "accent": "#ef4444",
        "accent_dim": "#7f1d1d",
        "bg": "#17191c",
        "panel": "#202327",
        "text": "#f7f7f5",
        "muted": "#a3a9b1",
        "soft": "#3a2426",
    },
    "yellow": {
        "accent": "#f2b84b",
        "accent_dim": "#8a5b14",
        "bg": "#17191c",
        "panel": "#202327",
        "text": "#f7f7f5",
        "muted": "#a3a9b1",
        "soft": "#352b1c",
    },
    "green": {
        "accent": "#35c46f",
        "accent_dim": "#15663b",
        "bg": "#17191c",
        "panel": "#202327",
        "text": "#f7f7f5",
        "muted": "#a3a9b1",
        "soft": "#1b3025",
    },
}


def log(message: str) -> None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def fmt_bytes(value: Optional[float]) -> str:
    if value is None:
        return "--"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(max(0.0, value))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{size:.0f} {unit}"
            if size < 10:
                return f"{size:.1f} {unit}"
            return f"{size:.0f} {unit}"
        size /= 1024.0
    return "--"


def fmt_mbps(value: Optional[float]) -> str:
    if value is None:
        return "--"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: Any) -> None:
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    canvas.create_polygon(points, smooth=True, splinesteps=10, **kwargs)


class FloatingTrafficWidget(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VPN Traffic")
        self.geometry(self.initial_geometry())
        self.resizable(False, False)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.94)

        self.canvas = tk.Canvas(self, width=WIDTH, height=HEIGHT, highlightthickness=0, bg="#17191c")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.drag_start: Optional[Tuple[int, int]] = None
        self.drag_window_start: Optional[Tuple[int, int]] = None
        self.last_sample: Optional[Dict[str, Any]] = None
        self.latest: Dict[str, Any] = {
            "status": "red",
            "state_text": "PANEL OFF",
            "download_total": None,
            "upload_total": None,
            "down_mbps": None,
            "up_mbps": None,
            "sampled_at": "",
        }
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.stop_event = threading.Event()

        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.end_drag)
        self.bind("<Double-Button-1>", self.open_panel)
        self.bind("<Button-2>", self.open_panel)
        self.bind("<Button-3>", self.open_panel)

        self.draw()
        self.after(200, self.drain_queue)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.worker = threading.Thread(target=self.poll_loop, name="traffic-float-poll", daemon=True)
        self.worker.start()

    def initial_geometry(self) -> str:
        default = f"{WIDTH}x{HEIGHT}+80+80"
        try:
            payload = json.loads(POSITION_PATH.read_text(encoding="utf-8"))
            x = int(payload.get("x", 80))
            y = int(payload.get("y", 80))
            return f"{WIDTH}x{HEIGHT}+{max(0, x)}+{max(0, y)}"
        except Exception:
            return default

    def start_drag(self, event: tk.Event) -> None:
        self.drag_start = (int(event.x_root), int(event.y_root))
        self.drag_window_start = (self.winfo_x(), self.winfo_y())

    def drag(self, event: tk.Event) -> None:
        if not self.drag_start or not self.drag_window_start:
            return
        dx = int(event.x_root) - self.drag_start[0]
        dy = int(event.y_root) - self.drag_start[1]
        x = self.drag_window_start[0] + dx
        y = self.drag_window_start[1] + dy
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def end_drag(self, event: tk.Event) -> None:
        del event
        self.save_position()

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
        while not self.stop_event.is_set():
            try:
                self.queue.put(self.fetch_state())
            except Exception as exc:
                self.queue.put(
                    {
                        "status": "red",
                        "state_text": "NO DATA",
                        "download_total": self.latest.get("download_total"),
                        "upload_total": self.latest.get("upload_total"),
                        "down_mbps": None,
                        "up_mbps": None,
                        "sampled_at": time.strftime("%H:%M:%S"),
                        "error": str(exc),
                    }
                )
            self.stop_event.wait(POLL_SECONDS)

    def fetch_state(self) -> Dict[str, Any]:
        request = urllib.request.Request(PANEL_STATE_URL, headers={"User-Agent": "mullvad-speed-guard-float/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=0.9) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"panel unavailable: {exc}") from exc

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

        if not ok:
            status = "red"
            state_text = "DISCONNECTED" if not state.lower().startswith("connected") else "NO TUNNEL"
            down_mbps = None
            up_mbps = None
        elif down_mbps is None or down_mbps < SLOW_THRESHOLD_MBPS:
            status = "yellow"
            state_text = "SLOW"
        else:
            status = "green"
            state_text = "FAST"

        return {
            "status": status,
            "state_text": state_text,
            "download_total": download_total,
            "upload_total": upload_total,
            "down_mbps": down_mbps,
            "up_mbps": up_mbps,
            "sampled_at": time.strftime("%H:%M:%S"),
        }

    def drain_queue(self) -> None:
        updated = False
        while True:
            try:
                self.latest = self.queue.get_nowait()
                updated = True
            except queue.Empty:
                break
        if updated:
            self.draw()
        self.after(250, self.drain_queue)

    def draw(self) -> None:
        status = str(self.latest.get("status") or "red")
        palette = COLORS.get(status, COLORS["red"])
        self.canvas.delete("all")
        self.canvas.configure(bg=palette["bg"])

        rounded_rect(
            self.canvas,
            2,
            2,
            WIDTH - 2,
            HEIGHT - 2,
            8,
            fill=palette["panel"],
            outline=palette["accent"],
            width=2,
        )
        self.canvas.create_rectangle(4, 4, WIDTH - 4, 20, fill=palette["soft"], outline="")
        self.canvas.create_oval(12, 9, 22, 19, fill=palette["accent"], outline="")
        self.canvas.create_text(31, 14, text="VPN", anchor="w", fill=palette["text"], font=("Helvetica", 10, "bold"))
        self.canvas.create_text(
            82,
            14,
            text=str(self.latest.get("state_text") or ""),
            anchor="w",
            fill=palette["muted"],
            font=("Helvetica", 9, "bold"),
        )
        for x in (122, 130, 138):
            self.canvas.create_oval(x, 10, x + 3, 13, fill=palette["muted"], outline="")

        down_mbps = self.latest.get("down_mbps")
        speed = "--" if status == "red" else fmt_mbps(float(down_mbps) if down_mbps is not None else None)
        self.canvas.create_text(18, 48, text="NOW", anchor="w", fill=palette["muted"], font=("Helvetica", 9, "bold"))
        self.canvas.create_text(18, 77, text=speed, anchor="w", fill=palette["accent"], font=("Helvetica", 30, "bold"))
        self.canvas.create_text(123, 76, text="Mbps", anchor="w", fill=palette["muted"], font=("Helvetica", 10, "bold"))

        download_total = self.latest.get("download_total")
        upload_total = self.latest.get("upload_total")
        self.canvas.create_text(
            162,
            48,
            text="DOWN",
            anchor="w",
            fill=palette["muted"],
            font=("Helvetica", 9, "bold"),
        )
        self.canvas.create_text(
            162,
            65,
            text=fmt_bytes(float(download_total) if download_total is not None else None),
            anchor="w",
            fill=palette["text"],
            font=("Helvetica", 13, "bold"),
        )
        self.canvas.create_text(
            162,
            86,
            text="UP",
            anchor="w",
            fill=palette["muted"],
            font=("Helvetica", 9, "bold"),
        )
        self.canvas.create_text(
            162,
            103,
            text=fmt_bytes(float(upload_total) if upload_total is not None else None),
            anchor="w",
            fill=palette["text"],
            font=("Helvetica", 13, "bold"),
        )


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
