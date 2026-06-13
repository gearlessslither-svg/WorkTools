#!/usr/bin/env python3
"""Run the sleep-time Mullvad inventory and Ollama setup goal."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR
RESULTS_DIR = APP_DIR / "results"
OVERNIGHT_DIR = RESULTS_DIR / "overnight"
DB_PATH = RESULTS_DIR / "relay_inventory.sqlite3"
AUTO_GUARD_CONTROL_LOCK_PATH = RESULTS_DIR / "auto_guard_control.lock"
RUNTIME_RESULTS_DIR = Path.home() / "Library" / "Application Support" / "MullvadSpeedGuard" / "results"
RUNTIME_AUTO_GUARD_CONTROL_LOCK_PATH = RUNTIME_RESULTS_DIR / "auto_guard_control.lock"
MODEL_NAME = os.environ.get("MSG_OLLAMA_MODEL", "qwen3:8b")
MODEL_FALLBACKS = [
    item.strip()
    for item in os.environ.get("MSG_OLLAMA_MODEL_FALLBACKS", "qwen3:8b,qwen2.5:7b,llama3.1:8b").split(",")
    if item.strip()
]
AUTO_GUARD_LABEL = "com.story.mullvad-speed-guard.auto-guard"
AUTO_GUARD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AUTO_GUARD_LABEL}.plist"
OLLAMA_LABEL = "com.story.ollama.serve"
OLLAMA_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{OLLAMA_LABEL}.plist"
PATH_ENV = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run(
    args: List[str],
    *,
    check: bool = True,
    timeout: Optional[int] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    log("RUN " + " ".join(args))
    merged_env = dict(os.environ)
    merged_env["PATH"] = PATH_ENV
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        args,
        cwd=str(APP_DIR),
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if proc.stdout:
        print(proc.stdout.rstrip(), flush=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed with {proc.returncode}: {' '.join(args)}")
    return proc


def stream(args: List[str]) -> int:
    log("STREAM " + " ".join(args))
    env = dict(os.environ)
    env["PATH"] = PATH_ENV
    with subprocess.Popen(
        args,
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line.rstrip(), flush=True)
        return proc.wait()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    with connect_db() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


def query_all(sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
    with connect_db() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def counts() -> Dict[str, int]:
    result = {"total": 0, "unknown": 0, "working": 0, "no_speed": 0, "abandoned": 0, "retired": 0}
    rows = query_all("SELECT status, COUNT(*) AS c FROM relays GROUP BY status")
    for row in rows:
        result[str(row["status"])] = int(row["c"])
        result["total"] += int(row["c"])
    return result


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def export_inventory(stage: str) -> Dict[str, Any]:
    OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "stage": stage,
        "generated_at": now(),
        "counts": counts(),
        "model_target": MODEL_NAME,
        "db_path": str(DB_PATH),
    }
    relays = [
        row_dict(row)
        for row in query_all(
            """
            SELECT
                hostname, country, country_name, city, city_name, provider, ownership,
                status, attempts, success_count, consecutive_failures,
                last_mbps, best_mbps, last_latency_ms, score,
                last_test_at, last_success_at, last_error,
                fast_reachable, fast_latency_ms, fast_score, fast_probe_at, fast_error,
                ipv4, ipv6
            FROM relays
            ORDER BY country, city, hostname
            """
        )
    ]
    full_path = OVERNIGHT_DIR / "full_node_inventory.csv"
    write_csv(full_path, relays, list(relays[0].keys()) if relays else ["hostname"])
    write_csv(OVERNIGHT_DIR / f"full_node_inventory_{stamp}_{stage}.csv", relays, list(relays[0].keys()) if relays else ["hostname"])

    whitelist_rows = [
        row_dict(row)
        for row in query_all(
            """
            SELECT
                hostname, country, country_name, city, city_name, provider, ownership,
                status, last_mbps, best_mbps, last_latency_ms, score,
                attempts, success_count, consecutive_failures,
                last_test_at, last_success_at, fast_latency_ms, fast_probe_at
            FROM relays
            WHERE status='working' AND COALESCE(best_mbps, last_mbps, 0) >= 0.05
            ORDER BY COALESCE(best_mbps, last_mbps, 0) DESC, COALESCE(last_mbps, 0) DESC, score DESC, hostname
            """
        )
    ]
    whitelist_path = OVERNIGHT_DIR / "whitelist.csv"
    write_csv(whitelist_path, whitelist_rows, list(whitelist_rows[0].keys()) if whitelist_rows else ["hostname"])

    fast_rows = [
        row_dict(row)
        for row in query_all(
            """
            SELECT
                hostname, country, city, status, last_mbps, best_mbps, fast_latency_ms,
                fast_reachable, fast_probe_at, last_test_at, consecutive_failures
            FROM relays
            WHERE fast_probe_at IS NOT NULL
            ORDER BY
                fast_reachable DESC,
                CASE
                    WHEN status='working' AND success_count > 0 THEN 0
                    WHEN last_test_at IS NULL THEN 1
                    WHEN status!='working' AND COALESCE(best_mbps, last_mbps) IS NOT NULL AND success_count > 0 THEN 2
                    WHEN status='abandoned' THEN 4
                    WHEN status='no_speed' AND last_test_at IS NOT NULL THEN 3
                    ELSE 2
                END,
                COALESCE(best_mbps, last_mbps, 0) DESC,
                CASE WHEN fast_latency_ms IS NULL THEN 1 ELSE 0 END,
                fast_latency_ms ASC,
                hostname
            LIMIT 200
            """
        )
    ]
    write_csv(OVERNIGHT_DIR / "fast_candidates_top200.csv", fast_rows, list(fast_rows[0].keys()) if fast_rows else ["hostname"])

    summary["artifacts"] = {
        "full_node_inventory_csv": str(full_path),
        "whitelist_csv": str(whitelist_path),
        "fast_candidates_top200_csv": str(OVERNIGHT_DIR / "fast_candidates_top200.csv"),
    }
    summary["whitelist_count"] = len(whitelist_rows)
    summary["fastest_known"] = whitelist_rows[0] if whitelist_rows else None
    summary_path = OVERNIGHT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OVERNIGHT_DIR / f"summary_{stamp}_{stage}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    copy_back_to_project()
    log(f"Exported inventory stage={stage} counts={summary['counts']} whitelist={len(whitelist_rows)}")
    return summary


def copy_back_to_project() -> None:
    if APP_DIR == PROJECT_DIR:
        return
    target = PROJECT_DIR / "results" / "overnight"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for path in OVERNIGHT_DIR.glob("*"):
            if path.is_file():
                shutil.copy2(path, target / path.name)
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, PROJECT_DIR / "results" / "relay_inventory.sqlite3")
    except Exception as exc:
        log(f"Copy-back warning: {exc}")


def write_auto_guard_control_lock(reason: str, ttl_seconds: int = 3600) -> None:
    payload = {
        "reason": reason,
        "pid": os.getpid(),
        "created_at": now(),
        "expires_at_epoch": time.time() + max(30, int(ttl_seconds)),
    }
    raw = json.dumps(payload, ensure_ascii=False)
    for path in {AUTO_GUARD_CONTROL_LOCK_PATH, RUNTIME_AUTO_GUARD_CONTROL_LOCK_PATH}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")


def clear_auto_guard_control_lock() -> None:
    AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)
    RUNTIME_AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)


def start_auto_guard() -> None:
    if AUTO_GUARD_PLIST.exists():
        run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(AUTO_GUARD_PLIST)], check=False, timeout=10)
        run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{AUTO_GUARD_LABEL}"], check=False, timeout=10)


def scan_unknown_relays() -> None:
    remaining = counts().get("unknown", 0)
    if remaining <= 0:
        log("No unknown relays remain.")
        return
    log(f"Scanning {remaining} unknown relay(s).")
    write_auto_guard_control_lock("overnight unknown relay scan", ttl_seconds=7200)
    try:
        code = stream(
            [
                sys.executable,
                str(APP_DIR / "mullvad_speed_guard.py"),
                "inventory",
                "scan",
                "--scope",
                "unknown",
                "--min-working-mbps",
                "0.05",
                "--abandon-after",
                "3",
            ]
        )
    finally:
        clear_auto_guard_control_lock()
    if code != 0:
        raise RuntimeError(f"Unknown relay scan failed with exit code {code}")


def fastest_working_relay() -> Optional[Dict[str, Any]]:
    row = query_one(
        """
        SELECT *
        FROM relays
        WHERE status='working' AND COALESCE(best_mbps, last_mbps, 0) >= 0.05
        ORDER BY COALESCE(best_mbps, last_mbps, 0) DESC, COALESCE(last_mbps, 0) DESC, score DESC, hostname
        LIMIT 1
        """
    )
    return row_dict(row) if row else None


def connect_fastest() -> Optional[Dict[str, Any]]:
    relay = fastest_working_relay()
    if not relay:
        log("No working relay is available for Ollama download.")
        return None
    hostname = str(relay["hostname"])
    write_auto_guard_control_lock("overnight connect fastest relay", ttl_seconds=180)
    try:
        run(
            [
                sys.executable,
                str(APP_DIR / "mullvad_speed_guard.py"),
                "inventory",
                "connect",
                hostname,
                "--json",
            ],
            timeout=90,
        )
    finally:
        clear_auto_guard_control_lock()
    log(f"Connected fastest known relay for download: {hostname}")
    return relay


def find_ollama() -> Optional[str]:
    for candidate in [
        shutil.which("ollama"),
        "/opt/homebrew/bin/ollama",
        "/usr/local/bin/ollama",
        "/Applications/Ollama.app/Contents/Resources/ollama",
    ]:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def install_ollama_if_needed() -> str:
    ollama = find_ollama()
    if ollama:
        run([ollama, "--version"], check=False, timeout=20)
        return ollama
    brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
    if not Path(brew).exists():
        raise RuntimeError("Homebrew not found; cannot install Ollama automatically.")
    run([brew, "install", "ollama"], timeout=3600)
    ollama = find_ollama()
    if not ollama:
        raise RuntimeError("Ollama install finished, but `ollama` CLI was not found.")
    return ollama


def write_ollama_plist(ollama: str) -> None:
    log_path = str(OVERNIGHT_DIR / "ollama_serve.log")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{OLLAMA_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{ollama}</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{PATH_ENV}</string>
    <key>OLLAMA_HOST</key>
    <string>127.0.0.1:11434</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
</dict>
</plist>
"""
    OLLAMA_PLIST.write_text(plist, encoding="utf-8")


def ensure_ollama_server(ollama: str) -> None:
    if run([ollama, "list"], check=False, timeout=10).returncode == 0:
        return
    brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
    if Path(brew).exists():
        run([brew, "services", "start", "ollama"], check=False, timeout=60)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if run([ollama, "list"], check=False, timeout=10).returncode == 0:
            return
        time.sleep(3)
    write_ollama_plist(ollama)
    run(["launchctl", "bootout", f"gui/{os.getuid()}", str(OLLAMA_PLIST)], check=False, timeout=10)
    run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(OLLAMA_PLIST)], check=False, timeout=10)
    run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{OLLAMA_LABEL}"], check=False, timeout=10)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if run([ollama, "list"], check=False, timeout=10).returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError("Ollama server did not become ready.")


def setup_ollama(relay: Optional[Dict[str, Any]]) -> None:
    ollama = install_ollama_if_needed()
    ensure_ollama_server(ollama)
    pulled_model: Optional[str] = None
    pull_errors: Dict[str, str] = {}
    for model in MODEL_FALLBACKS:
        proc = run([ollama, "pull", model], check=False, timeout=14400)
        if proc.returncode == 0:
            pulled_model = model
            break
        pull_errors[model] = proc.stdout.strip()
    if not pulled_model:
        raise RuntimeError(f"All Ollama model pulls failed: {pull_errors}")
    prompt = "请用一句中文回答：本地 Ollama 模型已经可以正常运行了吗？"
    proc = run([ollama, "run", pulled_model, prompt], check=False, timeout=300)
    ok = proc.returncode == 0 and bool(proc.stdout.strip())
    status = {
        "generated_at": now(),
        "ok": ok,
        "model": pulled_model,
        "preferred_model": MODEL_NAME,
        "model_fallbacks": MODEL_FALLBACKS,
        "pull_errors": pull_errors,
        "ollama": ollama,
        "relay_used": relay,
        "smoke_prompt": prompt,
        "smoke_returncode": proc.returncode,
        "smoke_output": proc.stdout.strip(),
    }
    (OVERNIGHT_DIR / "ollama_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not ok:
        raise RuntimeError("Ollama smoke test failed.")


def main() -> int:
    OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)
    log("Overnight goal runner started.")
    export_inventory("before")
    try:
        scan_unknown_relays()
        export_inventory("after_scan")
        relay = connect_fastest()
        setup_ollama(relay)
        start_auto_guard()
        export_inventory("complete")
        log("Overnight goal runner completed successfully.")
        return 0
    except Exception as exc:
        log(f"FAILED: {exc}")
        export_inventory("failed")
        start_auto_guard()
        raise
    finally:
        copy_back_to_project()


if __name__ == "__main__":
    raise SystemExit(main())
