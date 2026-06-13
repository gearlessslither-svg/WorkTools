#!/usr/bin/env python3
"""Mullvad relay speed guard.

This tool uses the local `mullvad` CLI to monitor connection health, test
candidate relays, and reconnect to the best relay when the current tunnel drops
or becomes too slow.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import random
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.example.json"
DEFAULT_RESULTS_PATH = APP_DIR / "results" / "mullvad_speed_results.jsonl"


DEFAULT_CONFIG: Dict[str, Any] = {
    "candidate_countries": [],
    "candidate_cities": [],
    "candidate_hostnames": [],
    "exclude_hostnames": [],
    "max_candidates": 0,
    "shuffle_candidates": False,
    "update_relay_list_before_scan": True,
    "connect_timeout_seconds": 45,
    "settle_seconds": 4,
    "latency_checks": [
        {"host": "1.1.1.1", "port": 443},
        {"host": "8.8.8.8", "port": 443},
        {"host": "www.cloudflare.com", "port": 443},
    ],
    "latency_attempts_per_target": 2,
    "latency_timeout_seconds": 3,
    "download_urls": [
        "https://speed.cloudflare.com/__down?bytes=5000000",
        "https://cachefly.cachefly.net/10mb.test",
    ],
    "download_bytes": 4000000,
    "health_download_bytes": 1000000,
    "download_timeout_seconds": 12,
    "url_checks": [],
    "url_check_timeout_seconds": 10,
    "passive_traffic_enabled": True,
    "passive_min_down_mbps": 8.0,
    "passive_activity_threshold_bytes": 131072,
    "passive_probe_period_seconds": 900,
    "active_speed_when_passive_idle": True,
    "passive_low_speed_can_fail": False,
    "health_mode": "adaptive",
    "max_latency_ms": 2500,
    "speed_check_every_seconds": 1800,
    "speed_check_on_degraded": False,
    "min_mbps": 0.5,
    "preferred_mbps": 8.0,
    "watch_interval_seconds": 60,
    "failure_threshold": 2,
    "rescan_cooldown_seconds": 300,
    "use_inventory_for_reconnect": True,
    "reconnect_inventory_limit": 20,
    "reconnect_fallback_full_limit": 30,
    "quick_connect_timeout_seconds": 12,
    "disconnect_wait_timeout_seconds": 5,
    "quick_connect": False,
    "strict_hostname": True,
    "inventory_min_working_mbps": 0.05,
    "inventory_abandon_after": 3,
    "backup_pool_size": 5,
    "backup_ready_target": 3,
    "backup_candidate_limit": 25,
    "backup_attempt_limit": 3,
    "backup_retry_cooldown_seconds": 900,
    "fast_rank_workers": 64,
    "fast_rank_timeout_seconds": 1.2,
    "fast_rank_port": 443,
    "fast_rank_interval_seconds": 3600,
    "idle_refresh_enabled": True,
    "idle_refresh_after_seconds": 1800,
    "idle_refresh_user_idle_seconds": 1800,
    "idle_refresh_activity_threshold_bytes": 262144,
    "idle_refresh_batch_size": 1,
    "connecting_grace_seconds": 45,
    "daemon_retry_cooldown_seconds": 180,
    "url_emergency_min_failed": 0,
    "results_path": str(DEFAULT_RESULTS_PATH),
}


@dataclasses.dataclass
class Relay:
    hostname: str
    country: str
    country_name: str
    city: str
    city_name: str
    provider: str
    ownership: str
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None


@dataclasses.dataclass
class TestResult:
    hostname: str
    country: str
    city: str
    provider: str
    ownership: str
    observed_at: str
    connected: bool
    latency_ms: Optional[float]
    download_mbps: Optional[float]
    score: float
    error: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class HealthSample:
    ok: bool
    reason: str
    latency_ms: Optional[float] = None
    speed_mbps: Optional[float] = None
    passive_down_mbps: Optional[float] = None
    passive_up_mbps: Optional[float] = None
    ran_speed_test: bool = False


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_cmd(args: Sequence[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def require_mullvad() -> None:
    try:
        proc = run_cmd(["mullvad", "--version"], timeout=5)
    except FileNotFoundError as exc:
        raise SystemExit("Cannot find `mullvad` CLI. Install Mullvad VPN first.") from exc
    if proc.returncode != 0:
        raise SystemExit(f"`mullvad --version` failed: {proc.stderr.strip()}")


def deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        config = deep_merge(config, loaded)
    return config


def csv_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def apply_overrides(config: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    updated = dict(config)
    for attr, key in [
        ("countries", "candidate_countries"),
        ("cities", "candidate_cities"),
        ("hostnames", "candidate_hostnames"),
    ]:
        value = getattr(args, attr, None)
        parsed = csv_list(value)
        if parsed is not None:
            updated[key] = parsed
    if getattr(args, "all_countries", False):
        updated["candidate_countries"] = []
        updated["candidate_cities"] = []
        updated["candidate_hostnames"] = []
    if getattr(args, "max_candidates", None) is not None:
        updated["max_candidates"] = args.max_candidates
    if getattr(args, "min_mbps", None) is not None:
        updated["min_mbps"] = args.min_mbps
    if getattr(args, "preferred_mbps", None) is not None:
        updated["preferred_mbps"] = args.preferred_mbps
    if getattr(args, "max_latency_ms", None) is not None:
        updated["max_latency_ms"] = args.max_latency_ms
    if getattr(args, "health_mode", None) is not None:
        updated["health_mode"] = args.health_mode
    if getattr(args, "speed_check_every", None) is not None:
        updated["speed_check_every_seconds"] = args.speed_check_every
    if getattr(args, "url_checks", None):
        updated["url_checks"] = [part.strip() for part in args.url_checks.split(",") if part.strip()]
    if getattr(args, "url_check_timeout", None) is not None:
        updated["url_check_timeout_seconds"] = args.url_check_timeout
    if getattr(args, "passive_period", None) is not None:
        updated["passive_probe_period_seconds"] = args.passive_period
    if getattr(args, "passive_min_down_mbps", None) is not None:
        updated["passive_min_down_mbps"] = args.passive_min_down_mbps
    if getattr(args, "passive_activity_threshold_bytes", None) is not None:
        updated["passive_activity_threshold_bytes"] = args.passive_activity_threshold_bytes
    if getattr(args, "no_active_speed_when_passive_idle", False):
        updated["active_speed_when_passive_idle"] = False
    if getattr(args, "passive_low_speed_can_fail", False):
        updated["passive_low_speed_can_fail"] = True
    if getattr(args, "interval", None) is not None:
        updated["watch_interval_seconds"] = args.interval
    if getattr(args, "results_path", None):
        updated["results_path"] = args.results_path
    if getattr(args, "shuffle", False):
        updated["shuffle_candidates"] = True
    return updated


def mullvad_status(timeout: int = 10) -> Tuple[str, str]:
    proc = run_cmd(["mullvad", "status"], timeout=timeout)
    text = (proc.stdout or proc.stderr).strip()
    first = text.splitlines()[0].strip() if text else "Unknown"
    return first, text


def mullvad_rpc_unavailable_text(text: str) -> bool:
    lowered = (text or "").lower()
    markers = [
        "management rpc server",
        "transport error",
        "connection refused",
        "os error 61",
    ]
    return any(marker in lowered for marker in markers)


def mullvad_connecting_text(text: str) -> bool:
    return (text or "").strip().lower().startswith("connecting")


def nudge_mullvad_app() -> Optional[str]:
    """Ask macOS to start/focus Mullvad without requiring admin privileges."""
    try:
        proc = run_cmd(["open", "-gj", "-a", "Mullvad VPN"], timeout=10)
    except Exception as exc:
        return str(exc)
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout or "open Mullvad VPN failed").strip()


def current_relay_hostname() -> Optional[str]:
    _, text = mullvad_status()
    match = re.search(r"Relay:\s+([a-z0-9-]+)", text)
    return match.group(1) if match else None


def vpn_interface_traffic(status_text: Optional[str] = None) -> Dict[str, Any]:
    status_text = status_text if status_text is not None else mullvad_status()[1]
    interface_match = re.search(r"Tunnel interface:\s+([A-Za-z0-9_.-]+)", status_text)
    interface = interface_match.group(1) if interface_match else None
    if not interface:
        return {"ok": False, "interface": None, "download_bytes": None, "upload_bytes": None}

    try:
        proc = run_cmd(["netstat", "-ibn"], timeout=2)
    except Exception as exc:
        return {"ok": False, "interface": interface, "download_bytes": None, "upload_bytes": None, "error": str(exc)}

    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 9 or parts[0] != interface or not parts[2].startswith("<Link#"):
            continue
        try:
            if len(parts) >= 11:
                download_bytes = int(parts[6])
                upload_bytes = int(parts[9])
            else:
                download_bytes = int(parts[5])
                upload_bytes = int(parts[8])
        except (ValueError, IndexError):
            break
        return {
            "ok": True,
            "interface": interface,
            "download_bytes": download_bytes,
            "upload_bytes": upload_bytes,
        }
    return {"ok": False, "interface": interface, "download_bytes": None, "upload_bytes": None}


def passive_traffic_sample(config: Dict[str, Any], status_text: str) -> Optional[HealthSample]:
    if not bool(config.get("passive_traffic_enabled", True)):
        return None
    stats = vpn_interface_traffic(status_text)
    if not stats.get("ok"):
        return None

    now_mono = time.monotonic()
    state = config.setdefault("_passive_traffic_state", {})
    current = {
        "time": now_mono,
        "interface": stats["interface"],
        "download_bytes": int(stats["download_bytes"]),
        "upload_bytes": int(stats["upload_bytes"]),
    }
    previous = state.get("last")
    state["last"] = current
    if not previous or previous.get("interface") != current["interface"]:
        return None

    elapsed = now_mono - float(previous["time"])
    if elapsed <= 0.5:
        return None
    down_delta = current["download_bytes"] - int(previous["download_bytes"])
    up_delta = current["upload_bytes"] - int(previous["upload_bytes"])
    if down_delta < 0 or up_delta < 0:
        return None

    down_mbps = (down_delta * 8) / elapsed / 1_000_000
    up_mbps = (up_delta * 8) / elapsed / 1_000_000
    active_bytes = down_delta + up_delta
    activity_threshold = int(config.get("passive_activity_threshold_bytes", 131072) or 0)
    if active_bytes < activity_threshold:
        return HealthSample(
            True,
            f"passive traffic idle; down {down_mbps:.2f} Mbps up {up_mbps:.2f} Mbps",
            passive_down_mbps=round(down_mbps, 2),
            passive_up_mbps=round(up_mbps, 2),
        )

    min_down = float(config.get("passive_min_down_mbps", config.get("min_mbps", 0.5)) or 0)
    if min_down > 0 and down_mbps < min_down:
        if bool(config.get("passive_low_speed_can_fail", False)):
            return HealthSample(
                False,
                f"passive down {down_mbps:.2f} Mbps below {min_down:.2f} Mbps while traffic is active",
                speed_mbps=round(down_mbps, 2),
                passive_down_mbps=round(down_mbps, 2),
                passive_up_mbps=round(up_mbps, 2),
            )
        return HealthSample(
            True,
            f"passive down {down_mbps:.2f} Mbps; below threshold but not treated as failure",
            passive_down_mbps=round(down_mbps, 2),
            passive_up_mbps=round(up_mbps, 2),
        )
    return HealthSample(
        True,
        f"passive down {down_mbps:.2f} Mbps up {up_mbps:.2f} Mbps",
        speed_mbps=round(down_mbps, 2),
        passive_down_mbps=round(down_mbps, 2),
        passive_up_mbps=round(up_mbps, 2),
    )


def relay_list_text() -> str:
    proc = run_cmd(["mullvad", "relay", "list"], timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def update_relay_list() -> None:
    proc = run_cmd(["mullvad", "relay", "update"], timeout=60)
    if proc.returncode != 0:
        eprint(f"Relay list update failed, using cached list: {proc.stderr.strip()}")


def parse_relays(text: str) -> List[Relay]:
    relays: List[Relay] = []
    country = ""
    country_name = ""
    city = ""
    city_name = ""
    country_re = re.compile(r"^([^\t].*?) \(([a-z]{2})\)$")
    city_re = re.compile(r"^\t(.+?) \(([a-z0-9]{3})\) @")
    relay_re = re.compile(r"^\t\t([a-z0-9-]+) \((.*?)\) - hosted by (.*?) \((.*?)\)$")

    for line in text.splitlines():
        country_match = country_re.match(line)
        if country_match:
            country_name, country = country_match.group(1), country_match.group(2)
            city = ""
            city_name = ""
            continue
        city_match = city_re.match(line)
        if city_match:
            city_name, city = city_match.group(1), city_match.group(2)
            continue
        relay_match = relay_re.match(line)
        if relay_match and country and city:
            addresses = [part.strip() for part in relay_match.group(2).split(",")]
            ipv4 = next((item for item in addresses if "." in item), None)
            ipv6 = next((item for item in addresses if ":" in item), None)
            relays.append(
                Relay(
                    hostname=relay_match.group(1),
                    country=country,
                    country_name=country_name,
                    city=city,
                    city_name=city_name,
                    provider=relay_match.group(3),
                    ownership=relay_match.group(4),
                    ipv4=ipv4,
                    ipv6=ipv6,
                )
            )
    return relays


def filter_relays(relays: Iterable[Relay], config: Dict[str, Any]) -> List[Relay]:
    countries = {str(item).lower() for item in config.get("candidate_countries", [])}
    cities = {str(item).lower() for item in config.get("candidate_cities", [])}
    hostnames = {str(item).lower() for item in config.get("candidate_hostnames", [])}
    excluded = {str(item).lower() for item in config.get("exclude_hostnames", [])}

    selected: List[Relay] = []
    for relay in relays:
        hostname = relay.hostname.lower()
        if hostname in excluded:
            continue
        if hostnames:
            if hostname in hostnames:
                selected.append(relay)
            continue
        if countries and relay.country.lower() not in countries:
            continue
        if cities and relay.city.lower() not in cities:
            continue
        selected.append(relay)

    if config.get("shuffle_candidates", False):
        random.shuffle(selected)

    max_candidates = int(config.get("max_candidates") or 0)
    if max_candidates > 0:
        selected = selected[:max_candidates]
    return selected


def set_relay(hostname: str, timeout: int) -> None:
    proc = run_cmd(["mullvad", "relay", "set", "location", hostname], timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def connect(timeout: int) -> None:
    proc = run_cmd(["mullvad", "connect", "--wait"], timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def disconnect(timeout: int = 20) -> None:
    proc = run_cmd(["mullvad", "disconnect", "--wait"], timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def disconnect_fast(timeout: int = 5) -> None:
    """Ask Mullvad to disconnect, then poll briefly instead of blocking on --wait."""
    proc = run_cmd(["mullvad", "disconnect"], timeout=max(2, min(timeout, 10)))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    deadline = time.monotonic() + max(0, timeout)
    last = ""
    while time.monotonic() < deadline:
        state, text = mullvad_status(timeout=3)
        last = text
        if not state.lower().startswith(("connected", "connecting")):
            return
        time.sleep(0.5)
    if last:
        eprint(f"Disconnect still not settled after {timeout}s; continuing. Last status: {last.splitlines()[0]}")


def connect_fast(timeout: int = 12) -> None:
    """Start connecting and poll status with a short timeout for emergency recovery."""
    proc = run_cmd(["mullvad", "connect"], timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    deadline = time.monotonic() + max(1, timeout)
    last = ""
    while time.monotonic() < deadline:
        state, text = mullvad_status(timeout=3)
        last = text
        if state.lower().startswith("connected"):
            return
        time.sleep(0.8)
    raise RuntimeError(f"Mullvad did not reach Connected within {timeout}s. Last status: {last}")


def connect_quick_then_wait(quick_timeout: int = 12, wait_timeout: int = 45) -> None:
    """Try the short connect path first, then wait normally before declaring failure."""
    try:
        connect_fast(timeout=quick_timeout)
        return
    except Exception as exc:
        eprint(f"Quick connect did not settle; falling back to --wait. {exc}")
    connect(timeout=wait_timeout)


def tcp_latency_ms(host: str, port: int, timeout: float) -> Optional[float]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - start) * 1000
    except OSError:
        return None


def measure_latency(config: Dict[str, Any]) -> Optional[float]:
    latencies: List[float] = []
    attempts = int(config.get("latency_attempts_per_target", 2))
    timeout = float(config.get("latency_timeout_seconds", 3))
    for target in config.get("latency_checks", []):
        host = str(target["host"])
        port = int(target.get("port", 443))
        for _ in range(attempts):
            latency = tcp_latency_ms(host, port, timeout)
            if latency is not None:
                latencies.append(latency)
    if not latencies:
        return None
    return sum(latencies) / len(latencies)


def download_speed_mbps(url: str, max_bytes: int, timeout: int) -> Optional[float]:
    request = urllib.request.Request(url, headers={"User-Agent": "mullvad-speed-guard/1.0"})
    start = time.monotonic()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while total < max_bytes:
                chunk = response.read(min(65536, max_bytes - total))
                if not chunk:
                    break
                total += len(chunk)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    elapsed = time.monotonic() - start
    if elapsed <= 0 or total <= 0:
        return None
    return (total * 8) / elapsed / 1_000_000


def measure_download(config: Dict[str, Any], bytes_key: str = "download_bytes") -> Optional[float]:
    speeds: List[float] = []
    max_bytes = int(config.get(bytes_key, config.get("download_bytes", 4_000_000)))
    timeout = int(config.get("download_timeout_seconds", 12))
    for url in config.get("download_urls", []):
        speed = download_speed_mbps(str(url), max_bytes=max_bytes, timeout=timeout)
        if speed is not None:
            speeds.append(speed)
    if not speeds:
        return None
    return max(speeds)


def url_check(url: str, timeout: int) -> bool:
    hard_timeout = max(1, int(timeout))
    try:
        proc = run_cmd(
            [
                "curl",
                "-L",
                "-sS",
                "--max-time",
                str(hard_timeout),
                "--range",
                "0-0",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                url,
            ],
            timeout=hard_timeout + 2,
        )
        code_text = (proc.stdout or "").strip()[-3:]
        code = int(code_text) if code_text.isdigit() else 0
        return proc.returncode == 0 and 200 <= code < 500
    except FileNotFoundError:
        pass
    except (subprocess.TimeoutExpired, ValueError):
        return False

    request = urllib.request.Request(url, headers={"User-Agent": "mullvad-speed-guard/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=hard_timeout) as response:
            response.read(1)
            return 200 <= int(getattr(response, "status", 200)) < 500
    except urllib.error.HTTPError as exc:
        return 200 <= int(exc.code) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def measure_url_checks(config: Dict[str, Any]) -> Optional[str]:
    urls = [str(url).strip() for url in config.get("url_checks", []) if str(url).strip()]
    if not urls:
        return None
    timeout = int(config.get("url_check_timeout_seconds", 5))
    failed_set = set()
    pool = ThreadPoolExecutor(max_workers=min(len(urls), 4))
    try:
        futures = {pool.submit(url_check, url, timeout): url for url in urls}
        try:
            completed = as_completed(futures, timeout=max(1, timeout) + 3)
            for future in completed:
                url = futures[future]
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                if not ok:
                    failed_set.add(url)
        except FuturesTimeoutError:
            pass
        for future, url in futures.items():
            if not future.done():
                future.cancel()
                failed_set.add(url)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    failed = [url for url in urls if url in failed_set]
    if failed:
        return "url probe failed: " + ", ".join(failed[:3])
    return None


def score_result(download_mbps: Optional[float], latency_ms: Optional[float], connected: bool) -> float:
    if not connected:
        return -1_000_000.0
    speed = download_mbps or 0.0
    latency = latency_ms if latency_ms is not None else 10_000.0
    return (speed * 1000.0) - latency


def test_relay(relay: Relay, config: Dict[str, Any]) -> TestResult:
    timeout = int(config.get("connect_timeout_seconds", 45))
    try:
        quick = bool(config.get("quick_connect", False))
        quick_timeout = int(config.get("quick_connect_timeout_seconds", 12))
        disconnect_timeout = int(config.get("disconnect_wait_timeout_seconds", 5 if quick else 20))
        if quick:
            try:
                disconnect_fast(timeout=disconnect_timeout)
            except Exception:
                pass
        set_relay(relay.hostname, timeout=timeout)
        if quick:
            connect_quick_then_wait(quick_timeout=quick_timeout, wait_timeout=timeout)
        else:
            connect(timeout=timeout)
        time.sleep(float(config.get("settle_seconds", 4)))
        state, _ = mullvad_status(timeout=10)
        connected = state.lower().startswith("connected")
        latency = measure_latency(config) if connected else None
        speed = measure_download(config) if connected else None
        return TestResult(
            hostname=relay.hostname,
            country=relay.country,
            city=relay.city,
            provider=relay.provider,
            ownership=relay.ownership,
            observed_at=utc_now(),
            connected=connected,
            latency_ms=round(latency, 1) if latency is not None else None,
            download_mbps=round(speed, 2) if speed is not None else None,
            score=round(score_result(speed, latency, connected), 2),
        )
    except Exception as exc:
        return TestResult(
            hostname=relay.hostname,
            country=relay.country,
            city=relay.city,
            provider=relay.provider,
            ownership=relay.ownership,
            observed_at=utc_now(),
            connected=False,
            latency_ms=None,
            download_mbps=None,
            score=-1_000_000.0,
            error=str(exc),
        )


def write_result(path: Path, result: TestResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")


def print_result(result: TestResult) -> None:
    speed = "n/a" if result.download_mbps is None else f"{result.download_mbps:.2f} Mbps"
    latency = "n/a" if result.latency_ms is None else f"{result.latency_ms:.1f} ms"
    status = "ok" if result.connected else "failed"
    detail = f" error={result.error}" if result.error else ""
    print(
        f"{result.hostname:18} {status:6} speed={speed:>12} latency={latency:>10} "
        f"score={result.score:>10.2f}{detail}",
        flush=True,
    )


def restore_previous(previous_relay: Optional[str], previous_connected: bool, config: Dict[str, Any]) -> None:
    timeout = int(config.get("connect_timeout_seconds", 45))
    quick = bool(config.get("quick_connect", False))
    quick_timeout = int(config.get("quick_connect_timeout_seconds", 12))
    disconnect_timeout = int(config.get("disconnect_wait_timeout_seconds", 5 if quick else 20))
    if previous_relay:
        eprint(f"Restoring previous relay: {previous_relay}")
        if quick and previous_connected:
            try:
                disconnect_fast(timeout=disconnect_timeout)
            except Exception:
                pass
        set_relay(previous_relay, timeout=timeout)
        if previous_connected:
            if quick:
                connect_quick_then_wait(quick_timeout=quick_timeout, wait_timeout=timeout)
            else:
                connect(timeout=timeout)
        else:
            if quick:
                disconnect_fast(timeout=disconnect_timeout)
            else:
                disconnect(timeout=20)
    elif not previous_connected:
        if quick:
            disconnect_fast(timeout=disconnect_timeout)
        else:
            disconnect(timeout=20)


def load_candidates(config: Dict[str, Any], update: bool) -> List[Relay]:
    if update:
        update_relay_list()
    relays = parse_relays(relay_list_text())
    return filter_relays(relays, config)


def command_list(args: argparse.Namespace) -> int:
    config = apply_overrides(load_config(args.config), args)
    candidates = load_candidates(config, update=False)
    for relay in candidates[: args.limit]:
        print(
            f"{relay.hostname:18} {relay.country}/{relay.city:3} "
            f"{relay.country_name} / {relay.city_name} - {relay.provider} ({relay.ownership})"
        )
    print(f"Shown {min(len(candidates), args.limit)} of {len(candidates)} matching relays.")
    return 0


def command_status(args: argparse.Namespace) -> int:
    config = apply_overrides(load_config(args.config), args)
    state, text = mullvad_status()
    print(text)
    if args.check_speed:
        speed = measure_download(config)
        if speed is None:
            print("Speed sample: failed")
        else:
            print(f"Speed sample: {speed:.2f} Mbps")
            if speed < float(config.get("min_mbps", 0.5)):
                print(f"Below threshold: {config.get('min_mbps')} Mbps")
    return 0 if state.lower().startswith("connected") else 1


def run_scan(args: argparse.Namespace, force_connect_best: bool = False) -> Optional[TestResult]:
    config = apply_overrides(load_config(args.config), args)
    update = bool(config.get("update_relay_list_before_scan", True)) and not args.no_update
    results_path = Path(config.get("results_path", str(DEFAULT_RESULTS_PATH))).expanduser()
    previous_relay = current_relay_hostname()
    previous_state, _ = mullvad_status()
    previous_connected = previous_state.lower().startswith("connected")

    candidates = load_candidates(config, update=update)
    if not candidates:
        print("No matching relays.")
        return None

    print(f"Matched {len(candidates)} relay(s).")
    if getattr(args, "dry_run", False):
        for relay in candidates:
            print(f"{relay.hostname:18} {relay.country}/{relay.city} {relay.provider}")
        return None

    best: Optional[TestResult] = None
    interrupted = False

    def handle_signal(signum: int, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    old_int = signal.signal(signal.SIGINT, handle_signal)
    old_term = signal.signal(signal.SIGTERM, handle_signal)
    try:
        for index, relay in enumerate(candidates, start=1):
            print(f"[{index}/{len(candidates)}] Testing {relay.hostname} ...", flush=True)
            result = test_relay(relay, config)
            write_result(results_path, result)
            print_result(result)
            if result.connected and (best is None or result.score > best.score):
                best = result
    except KeyboardInterrupt:
        eprint("Scan interrupted.")
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        connect_best = getattr(args, "connect_best", False) or force_connect_best
        if best and connect_best and not interrupted:
            print(f"Connecting best relay: {best.hostname}")
            set_relay(best.hostname, timeout=int(config.get("connect_timeout_seconds", 45)))
            connect(timeout=int(config.get("connect_timeout_seconds", 45)))
        else:
            restore_previous(previous_relay, previous_connected, config)

    if best:
        print(
            f"Best: {best.hostname} speed={best.download_mbps} Mbps "
            f"latency={best.latency_ms} ms observed_at={best.observed_at}"
        )
    else:
        print("No relay produced a successful test.")
    return best


def health_check(config: Dict[str, Any], last_speed_check_at: float) -> Tuple[HealthSample, float]:
    mode = str(config.get("health_mode", "adaptive")).lower()
    if mode not in {"status", "latency", "speed", "adaptive"}:
        mode = "adaptive"

    state, status_text = mullvad_status()
    if not state.lower().startswith("connected"):
        return HealthSample(False, f"VPN state is {state}"), last_speed_check_at

    if mode == "status":
        return HealthSample(True, "connected; network probe skipped"), last_speed_check_at

    url_failure = measure_url_checks(config)
    if url_failure:
        return HealthSample(False, url_failure), last_speed_check_at

    latency: Optional[float] = None
    latency_bad = False
    if mode in {"latency", "adaptive"}:
        latency = measure_latency(config)
        if latency is None:
            return HealthSample(False, "latency probe failed"), last_speed_check_at
        max_latency = float(config.get("max_latency_ms") or 0)
        latency_bad = max_latency > 0 and latency > max_latency
        if mode == "latency":
            if latency_bad:
                return (
                    HealthSample(
                        False,
                        f"latency {latency:.1f} ms above {max_latency:.1f} ms",
                        latency_ms=round(latency, 1),
                    ),
                    last_speed_check_at,
                )
            return HealthSample(True, f"latency {latency:.1f} ms", latency_ms=round(latency, 1)), last_speed_check_at

    passive_sample: Optional[HealthSample] = None
    passive_state: Dict[str, Any] = config.setdefault("_passive_traffic_state", {})
    now_mono = time.monotonic()

    if mode == "adaptive" and bool(config.get("passive_traffic_enabled", True)):
        passive_state.setdefault("period_started_at", now_mono)
        passive_state.setdefault("period_has_passive_success", False)
        passive_sample = passive_traffic_sample(config, status_text)
        min_passive = float(config.get("passive_min_down_mbps", config.get("min_mbps", 0.5)) or 0)
        if (
            passive_sample
            and passive_sample.passive_down_mbps is not None
            and min_passive > 0
            and passive_sample.passive_down_mbps >= min_passive
        ):
            passive_state["period_has_passive_success"] = True
            passive_state["last_passive_success_at"] = now_mono

    should_check_speed = mode == "speed"
    if mode == "adaptive":
        every = int(config.get("passive_probe_period_seconds", config.get("speed_check_every_seconds", 1800)) or 0)
        if every <= 0:
            every = int(config.get("speed_check_every_seconds", 1800) or 0)
        passive_enabled = bool(config.get("passive_traffic_enabled", True))
        if passive_enabled:
            period_started = float(passive_state.get("period_started_at", now_mono))
            speed_due = every > 0 and now_mono - period_started >= every
            if speed_due and passive_state.get("period_has_passive_success"):
                passive_state["period_started_at"] = now_mono
                passive_state["period_has_passive_success"] = False
                speed_due = False
            elif speed_due:
                passive_state["period_started_at"] = now_mono
                passive_state["period_has_passive_success"] = False
                speed_due = bool(config.get("active_speed_when_passive_idle", True))
        else:
            speed_due = every > 0 and now_mono - last_speed_check_at >= every
        confirm_degraded = bool(config.get("speed_check_on_degraded", False)) and latency_bad
        should_check_speed = speed_due or confirm_degraded

    if not should_check_speed:
        if passive_sample and not passive_sample.ok:
            return passive_sample, last_speed_check_at
        if latency_bad:
            max_latency = float(config.get("max_latency_ms") or 0)
            return (
                HealthSample(
                    False,
                    f"latency {latency:.1f} ms above {max_latency:.1f} ms",
                    latency_ms=round(latency, 1) if latency is not None else None,
                ),
                last_speed_check_at,
            )
        if passive_sample:
            passive_sample.latency_ms = round(latency, 1) if latency is not None else None
            return passive_sample, last_speed_check_at
        return (
            HealthSample(
                True,
                f"latency {latency:.1f} ms; speed probe skipped"
                if latency is not None
                else "connected; speed probe skipped",
                latency_ms=round(latency, 1) if latency is not None else None,
            ),
            last_speed_check_at,
        )

    speed_checked_at = time.monotonic()
    speed = measure_download(config, bytes_key="health_download_bytes")
    if speed is None:
        return (
            HealthSample(
                False,
                "speed sample failed",
                latency_ms=round(latency, 1) if latency is not None else None,
                ran_speed_test=True,
            ),
            speed_checked_at,
        )
    min_mbps = float(config.get("min_mbps", 0.5))
    if speed < min_mbps:
        return (
            HealthSample(
                False,
                f"speed {speed:.2f} Mbps below {min_mbps:.2f} Mbps",
                latency_ms=round(latency, 1) if latency is not None else None,
                speed_mbps=round(speed, 2),
                ran_speed_test=True,
            ),
            speed_checked_at,
        )
    if latency_bad:
        max_latency = float(config.get("max_latency_ms") or 0)
        return (
            HealthSample(
                False,
                f"latency {latency:.1f} ms above {max_latency:.1f} ms despite speed {speed:.2f} Mbps",
                latency_ms=round(latency, 1) if latency is not None else None,
                speed_mbps=round(speed, 2),
                ran_speed_test=True,
            ),
            speed_checked_at,
        )
    return (
        HealthSample(
            True,
            f"speed {speed:.2f} Mbps",
            latency_ms=round(latency, 1) if latency is not None else None,
            speed_mbps=round(speed, 2),
            ran_speed_test=True,
        ),
        speed_checked_at,
    )


def inventory_reconnect(config: Dict[str, Any]) -> Optional[TestResult]:
    import relay_inventory

    reconnect_config = dict(config)
    reconnect_config["quick_connect"] = bool(reconnect_config.get("quick_connect", True))
    reconnect_config["strict_hostname"] = bool(reconnect_config.get("strict_hostname", False))

    limit = int(config.get("reconnect_inventory_limit", 20) or 20)
    fallback_limit = int(config.get("reconnect_fallback_full_limit", 30) or 30)
    min_working_mbps = float(config.get("inventory_min_working_mbps", 0.05))
    abandon_after = int(config.get("inventory_abandon_after", 3))

    active = relay_inventory.relays_for_scope("active", limit=limit, include_abandoned=False)
    if active:
        print(f"Inventory reconnect: testing {len(active)} known-good relay(s).", flush=True)
        return relay_inventory.scan_inventory(
            reconnect_config,
            scope="active",
            limit=limit,
            include_abandoned=False,
            restore=False,
            connect_best=True,
            min_working_mbps=min_working_mbps,
            abandon_after=abandon_after,
        )

    print(
        f"Inventory reconnect: no known-good relays yet; testing {fallback_limit} all-country relay(s).",
        flush=True,
    )
    return relay_inventory.scan_inventory(
        reconnect_config,
        scope="full",
        limit=fallback_limit,
        include_abandoned=False,
        restore=False,
        connect_best=True,
        min_working_mbps=min_working_mbps,
        abandon_after=abandon_after,
    )


def command_watch(args: argparse.Namespace) -> int:
    config = apply_overrides(load_config(args.config), args)
    interval = int(config.get("watch_interval_seconds", 60))
    threshold = int(config.get("failure_threshold", 2))
    cooldown = int(config.get("rescan_cooldown_seconds", 300))
    failures = 0
    last_scan_at = 0.0
    last_speed_check_at = time.monotonic()

    print(
        f"Watching Mullvad every {interval}s; reconnect after {threshold} failed check(s). "
        f"mode={config.get('health_mode')}; min speed={config.get('min_mbps')} Mbps; "
        f"max latency={config.get('max_latency_ms')} ms.",
        flush=True,
    )
    while True:
        sample, last_speed_check_at = health_check(config, last_speed_check_at)
        probe = " speed-test" if sample.ran_speed_test else ""
        print(f"{utc_now()} health={'ok' if sample.ok else 'bad'}{probe} {sample.reason}", flush=True)
        if sample.ok:
            failures = 0
        else:
            failures += 1
            now = time.monotonic()
            if failures >= threshold and now - last_scan_at >= cooldown:
                print("Triggering relay scan and reconnect.", flush=True)
                if bool(config.get("use_inventory_for_reconnect", True)):
                    inventory_reconnect(config)
                else:
                    run_scan(args, force_connect_best=True)
                failures = 0
                last_scan_at = time.monotonic()
            elif failures >= threshold:
                remaining = int(cooldown - (now - last_scan_at))
                print(f"Cooldown active; next scan allowed in {max(0, remaining)}s.", flush=True)
        time.sleep(interval)


def command_inventory(args: argparse.Namespace) -> int:
    import relay_inventory

    config = apply_overrides(load_config(args.config), args)
    return relay_inventory.cli(args, config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor Mullvad VPN health, test relays, and reconnect to the fastest relay."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"JSON config path. Default: {DEFAULT_CONFIG_PATH}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--countries", help="Comma-separated country codes, for example hk,jp,sg,us.")
        sub.add_argument("--cities", help="Comma-separated Mullvad city codes, for example hkg,tyo,osa.")
        sub.add_argument("--hostnames", help="Comma-separated relay hostnames.")
        sub.add_argument("--all-countries", action="store_true", help="Ignore country/city filters.")
        sub.add_argument("--max-candidates", type=int, help="Limit number of relays tested or listed.")
        sub.add_argument("--min-mbps", type=float, help="Override slow-speed threshold.")
        sub.add_argument("--preferred-mbps", type=float, help="Override comfortable-speed target used for ranking.")
        sub.add_argument("--max-latency-ms", type=float, help="Override latency threshold for watch health checks.")
        sub.add_argument(
            "--health-mode",
            choices=["status", "latency", "speed", "adaptive"],
            help="Health-check mode for watch. Default comes from config.",
        )
        sub.add_argument(
            "--speed-check-every",
            type=int,
            help="Adaptive mode: seconds between small speed samples. 0 disables periodic speed samples.",
        )
        sub.add_argument("--url-checks", help="Comma-separated URLs that must be reachable through the VPN.")
        sub.add_argument("--url-check-timeout", type=int, help="Seconds before a URL health probe is considered failed.")
        sub.add_argument("--passive-period", type=int, help="Seconds per passive-first speed-check period.")
        sub.add_argument("--passive-min-down-mbps", type=float, help="Passive download Mbps that proves the relay is fast enough.")
        sub.add_argument("--passive-activity-threshold-bytes", type=int, help="Minimum interface bytes before passive traffic is considered active.")
        sub.add_argument(
            "--no-active-speed-when-passive-idle",
            action="store_true",
            help="Never run the fallback active speed sample when passive traffic is idle.",
        )
        sub.add_argument(
            "--passive-low-speed-can-fail",
            action="store_true",
            help="Treat low passive throughput during active traffic as a health failure. Off by default to avoid false positives.",
        )
        sub.add_argument("--results-path", help="Path for JSONL scan results.")
        sub.add_argument("--shuffle", action="store_true", help="Shuffle candidate scan order.")

    list_parser = subparsers.add_parser("list", help="List relays matching the configured filters.")
    add_common(list_parser)
    list_parser.add_argument("--limit", type=int, default=80)
    list_parser.set_defaults(func=command_list)

    scan_parser = subparsers.add_parser("scan", help="Test matching relays and optionally connect the best.")
    add_common(scan_parser)
    scan_parser.add_argument("--connect-best", action="store_true", help="Leave VPN connected to best relay.")
    scan_parser.add_argument("--dry-run", action="store_true", help="Only show matching relays.")
    scan_parser.add_argument("--no-update", action="store_true", help="Skip `mullvad relay update`.")
    scan_parser.set_defaults(func=lambda args: 0 if run_scan(args) is not None or args.dry_run else 1)

    watch_parser = subparsers.add_parser("watch", help="Monitor current VPN and rescan on disconnect or slowness.")
    add_common(watch_parser)
    watch_parser.add_argument("--interval", type=int, help="Seconds between health checks.")
    watch_parser.add_argument("--no-update", action="store_true", help="Skip `mullvad relay update` during scans.")
    watch_parser.set_defaults(func=command_watch)

    status_parser = subparsers.add_parser("status", help="Show current Mullvad status.")
    status_parser.add_argument("--check-speed", action="store_true", help="Run one download-speed sample.")
    status_parser.add_argument("--min-mbps", type=float, help="Override slow-speed threshold.")
    status_parser.set_defaults(func=command_status)

    inventory_parser = subparsers.add_parser("inventory", help="Manage all-relay inventory and speed history.")
    inventory_sub = inventory_parser.add_subparsers(dest="inventory_command", required=True)

    inventory_refresh = inventory_sub.add_parser("refresh", help="Refresh all relays into the local inventory DB.")
    inventory_refresh.add_argument("--no-update", action="store_true", help="Skip `mullvad relay update`.")
    inventory_refresh.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_refresh.set_defaults(func=command_inventory)

    inventory_top = inventory_sub.add_parser("top", help="Show fastest known relays.")
    inventory_top.add_argument("--limit", type=int, default=5)
    inventory_top.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_top.set_defaults(func=command_inventory)

    inventory_whitelist = inventory_sub.add_parser("whitelist", help="Show historically reliable relays for this time of day.")
    inventory_whitelist.add_argument("--limit", type=int, default=10)
    inventory_whitelist.add_argument("--min-mbps", type=float, default=0.05)
    inventory_whitelist.add_argument("--preferred-mbps", type=float, default=8.0)
    inventory_whitelist.add_argument("--hour", type=int, help="Local hour 0-23. Default: current hour.")
    inventory_whitelist.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_whitelist.set_defaults(func=command_inventory)

    inventory_fast = inventory_sub.add_parser("fast-rank", help="Quickly rank all relays without switching VPN.")
    inventory_fast.add_argument("--workers", type=int, default=64, help="Concurrent TCP probes.")
    inventory_fast.add_argument("--timeout", type=float, default=1.2, help="TCP timeout per relay.")
    inventory_fast.add_argument("--port", type=int, default=443, help="TCP port to probe.")
    inventory_fast.add_argument("--limit", type=int, default=0, help="Limit relays for testing/debugging. 0 means all.")
    inventory_fast.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_fast.set_defaults(func=command_inventory)

    inventory_verify = inventory_sub.add_parser(
        "verify-pool",
        help="True-test fast candidates and keep testing backups until enough are ready.",
    )
    inventory_verify.add_argument("--pool-size", type=int, default=5)
    inventory_verify.add_argument("--ready-target", type=int, default=3)
    inventory_verify.add_argument("--candidate-limit", type=int, default=25)
    inventory_verify.add_argument("--min-working-mbps", type=float, default=0.05)
    inventory_verify.add_argument("--abandon-after", type=int, default=3)
    inventory_verify.add_argument("--fast-workers", type=int, default=64)
    inventory_verify.add_argument("--fast-timeout", type=float, default=1.2)
    inventory_verify.add_argument("--fast-port", type=int, default=443)
    inventory_verify.add_argument("--connect-best", action="store_true", help="Leave VPN on the best verified relay.")
    inventory_verify.add_argument("--no-restore", action="store_true", help="Do not restore the previous relay after testing.")
    inventory_verify.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_verify.set_defaults(func=command_inventory)

    inventory_summary = inventory_sub.add_parser("summary", help="Show inventory counts and recent results.")
    inventory_summary.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_summary.set_defaults(func=command_inventory)

    inventory_scan = inventory_sub.add_parser("scan", help="Scan relays and update speed history.")
    inventory_scan.add_argument("--scope", choices=["full", "active", "unknown", "untested", "abandoned", "top", "fast"], default="full")
    inventory_scan.add_argument("--limit", type=int, default=0, help="Maximum relays to test. 0 means no limit.")
    inventory_scan.add_argument("--include-abandoned", action="store_true", help="Include abandoned relays.")
    inventory_scan.add_argument("--connect-best", action="store_true", help="Leave VPN connected to best scanned relay.")
    inventory_scan.add_argument("--no-restore", action="store_true", help="Do not restore previous relay after scan.")
    inventory_scan.add_argument("--dry-run", action="store_true", help="Show relay list without connecting.")
    inventory_scan.add_argument("--hostnames", help="Comma-separated relay hostnames to scan instead of a scope.")
    inventory_scan.add_argument("--min-working-mbps", type=float, default=0.05)
    inventory_scan.add_argument("--abandon-after", type=int, default=3)
    inventory_scan.set_defaults(func=command_inventory)

    inventory_daemon = inventory_sub.add_parser("daemon", help="Run periodic active/full inventory scans.")
    inventory_daemon.add_argument("--active-interval", type=int, default=1800)
    inventory_daemon.add_argument("--full-interval", type=int, default=21600)
    inventory_daemon.add_argument("--idle-seconds", type=int, default=900)
    inventory_daemon.add_argument("--active-limit", type=int, default=20)
    inventory_daemon.add_argument("--full-limit", type=int, default=0)
    inventory_daemon.add_argument("--min-working-mbps", type=float, default=0.05)
    inventory_daemon.add_argument("--abandon-after", type=int, default=3)
    inventory_daemon.set_defaults(func=command_inventory)

    inventory_auto_guard = inventory_sub.add_parser(
        "auto-guard",
        help="Monitor current relay and switch through verified backups before refreshing all candidates.",
    )
    inventory_auto_guard.add_argument("--interval", type=int, default=60)
    inventory_auto_guard.add_argument("--health-mode", choices=["adaptive", "latency", "status", "speed"], default="adaptive")
    inventory_auto_guard.add_argument("--speed-check-every", type=int, default=600)
    inventory_auto_guard.add_argument("--min-mbps", type=float, default=0.5)
    inventory_auto_guard.add_argument("--preferred-mbps", type=float, default=8.0)
    inventory_auto_guard.add_argument("--max-latency-ms", type=float, default=2500)
    inventory_auto_guard.add_argument("--pool-size", type=int, default=5)
    inventory_auto_guard.add_argument("--ready-target", type=int, default=3)
    inventory_auto_guard.add_argument("--candidate-limit", type=int, default=25)
    inventory_auto_guard.add_argument("--min-working-mbps", type=float, default=0.05)
    inventory_auto_guard.add_argument("--abandon-after", type=int, default=3)
    inventory_auto_guard.add_argument("--fast-rank-interval", type=int, default=3600)
    inventory_auto_guard.add_argument("--fast-workers", type=int, default=64)
    inventory_auto_guard.add_argument("--fast-timeout", type=float, default=1.2)
    inventory_auto_guard.add_argument("--fast-port", type=int, default=443)
    inventory_auto_guard.add_argument("--failure-threshold", type=int, default=3)
    inventory_auto_guard.add_argument("--backup-attempt-limit", type=int, default=3)
    inventory_auto_guard.add_argument(
        "--recovery-search-limit",
        type=int,
        default=50,
        help="How many whitelist relays to consider during disconnected emergency recovery.",
    )
    inventory_auto_guard.add_argument(
        "--emergency-attempt-limit",
        type=int,
        default=12,
        help="How many whitelist relays to try in one disconnected emergency recovery cycle.",
    )
    inventory_auto_guard.add_argument(
        "--backup-retry-cooldown",
        type=int,
        default=900,
        help="Seconds to skip a verified backup after it fails during recovery.",
    )
    inventory_auto_guard.add_argument(
        "--refresh-pool-on-failure",
        action="store_true",
        help="After verified backups fail, true-test a fresh fast candidate pool. Disabled by default to reduce churn.",
    )
    inventory_auto_guard.add_argument(
        "--refresh-pool-cooldown",
        type=int,
        default=1800,
        help="Minimum seconds between automatic true-test pool refreshes.",
    )
    inventory_auto_guard.add_argument("--quick-connect-timeout", type=int, default=12)
    inventory_auto_guard.add_argument("--disconnect-wait-timeout", type=int, default=5)
    inventory_auto_guard.add_argument(
        "--strict-hostname",
        action="store_true",
        help="Require Mullvad to connect the exact hostname instead of accepting a same-location fallback.",
    )
    inventory_auto_guard.add_argument("--url-checks", help="Comma-separated URLs that must be reachable through the VPN.")
    inventory_auto_guard.add_argument("--url-check-timeout", type=int, default=10)
    inventory_auto_guard.add_argument("--passive-period", type=int, default=900)
    inventory_auto_guard.add_argument("--passive-min-down-mbps", type=float, default=8.0)
    inventory_auto_guard.add_argument("--passive-activity-threshold-bytes", type=int, default=131072)
    inventory_auto_guard.add_argument("--no-active-speed-when-passive-idle", action="store_true")
    inventory_auto_guard.add_argument("--passive-low-speed-can-fail", action="store_true")
    inventory_auto_guard.add_argument("--idle-refresh-after", type=int, default=1800)
    inventory_auto_guard.add_argument("--idle-refresh-user-idle-seconds", type=int, default=1800)
    inventory_auto_guard.add_argument("--idle-refresh-activity-threshold-bytes", type=int, default=262144)
    inventory_auto_guard.add_argument("--idle-refresh-batch-size", type=int, default=1)
    inventory_auto_guard.add_argument("--no-idle-refresh", action="store_true")
    inventory_auto_guard.add_argument(
        "--connecting-grace",
        type=int,
        default=45,
        help="Seconds to let Mullvad finish a Connecting state before auto-guard starts relay recovery.",
    )
    inventory_auto_guard.add_argument(
        "--daemon-retry-cooldown",
        type=int,
        default=180,
        help="Seconds between attempts to relaunch/nudge Mullvad when its management RPC is unavailable.",
    )
    inventory_auto_guard.add_argument(
        "--url-emergency-min-failed",
        type=int,
        default=0,
        help="URL failures required for immediate emergency recovery. 0 means all configured URL checks.",
    )
    inventory_auto_guard.set_defaults(func=command_inventory)

    inventory_connect = inventory_sub.add_parser("connect", help="Connect Mullvad to a specific relay hostname.")
    inventory_connect.add_argument("hostname")
    inventory_connect.add_argument("--json", action="store_true", help="Print JSON.")
    inventory_connect.set_defaults(func=command_inventory)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    require_mullvad()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except subprocess.TimeoutExpired as exc:
        eprint(f"Command timed out: {' '.join(exc.cmd)}")
        return 2
    except KeyboardInterrupt:
        eprint("Stopped.")
        return 130
    except Exception as exc:
        eprint(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
