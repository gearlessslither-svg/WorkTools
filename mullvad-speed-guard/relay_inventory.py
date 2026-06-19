#!/usr/bin/env python3
"""SQLite relay inventory and scan orchestration for Mullvad Speed Guard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import mullvad_speed_guard as guard


APP_DIR = Path(__file__).resolve().parent
RESULTS_DIR = APP_DIR / "results"
DB_PATH = RESULTS_DIR / "relay_inventory.sqlite3"
SCAN_LOG_PATH = RESULTS_DIR / "inventory_scan.log"
AUTO_GUARD_PID_PATH = RESULTS_DIR / "auto_guard.pid"
AUTO_GUARD_CONTROL_LOCK_PATH = RESULTS_DIR / "auto_guard_control.lock"

# Daily scheduled full-node scan. The flag file's PRESENCE means "disabled"; its
# absence (the default) means the 04:00 scan is on. Checked in both the source
# and runtime results dirs so the panel/float widget (runtime) and CLI (source)
# can toggle it from either side.
RUNTIME_RESULTS_DIR = Path.home() / "Library" / "Application Support" / "MullvadSpeedGuard" / "results"
SCHEDULED_FULL_SCAN_DISABLED_FILENAME = "nightly_fullscan.disabled"
META_LAST_SCHEDULED_FULL_SCAN_DATE = "last_scheduled_full_scan_date"


def _scheduled_flag_paths() -> List[Path]:
    seen: List[Path] = []
    for base in (RESULTS_DIR, RUNTIME_RESULTS_DIR):
        path = base / SCHEDULED_FULL_SCAN_DISABLED_FILENAME
        if path not in seen:
            seen.append(path)
    return seen


def scheduled_full_scan_disabled() -> bool:
    """True if the daily 04:00 full scan has been turned off via the flag file."""
    return any(path.exists() for path in _scheduled_flag_paths())


def set_scheduled_full_scan(enabled: bool) -> bool:
    """Enable/disable the daily full scan by removing/creating the flag file in
    every results dir. Returns the resulting enabled state."""
    for path in _scheduled_flag_paths():
        try:
            if enabled:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(now(), encoding="utf-8")
        except Exception:
            pass
    return not scheduled_full_scan_disabled()

MIN_WORKING_MBPS = 0.05
ABANDON_AFTER = 3
DEFAULT_POOL_SIZE = 5
DEFAULT_READY_TARGET = 3
DEFAULT_CANDIDATE_LIMIT = 25
DEFAULT_FAST_WORKERS = 64
DEFAULT_FAST_TIMEOUT = 1.2
DEFAULT_FAST_PORT = 443
RELAY_SYNC_TTL_SECONDS = 6 * 60 * 60
META_LAST_RELAY_SYNC_AT = "last_relay_sync_at"
META_LAST_NIGHTLY_FULL_SCAN_AT = "last_nightly_full_scan_at"
META_LAST_NIGHTLY_FULL_SCAN_RESULT = "last_nightly_full_scan_result"
DEFAULT_NIGHTLY_FULL_SCAN_COOLDOWN_SECONDS = 24 * 60 * 60
DEFAULT_NIGHTLY_FULL_SCAN_MAX_SECONDS = 6 * 60 * 60
DEFAULT_BETTER_MIN_DELTA_MBPS = 0.5
DEFAULT_BETTER_MIN_RATIO = 1.15


def blocked_country_list(config: Optional[Dict[str, Any]] = None) -> List[str]:
    return sorted(guard.blocked_country_codes(config))


def blocked_country_sql(column: str = "country", config: Optional[Dict[str, Any]] = None) -> tuple[str, List[Any]]:
    countries = blocked_country_list(config)
    if not countries:
        return "", []
    placeholders = ",".join("?" for _ in countries)
    return f" AND {column} NOT IN ({placeholders})", list(countries)


def country_is_blocked(country: Optional[str], config: Optional[Dict[str, Any]] = None) -> bool:
    return bool(country and country.strip().lower() in guard.blocked_country_codes(config))


def relay_hostname_blocked(
    hostname: Optional[str],
    db_path: Path = DB_PATH,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    if guard.hostname_is_blocked(hostname, config):
        return True
    if not hostname:
        return False
    try:
        with connect_db(db_path) as conn:
            row = conn.execute("SELECT country FROM relays WHERE hostname=?", (hostname.strip().lower(),)).fetchone()
    except Exception:
        return False
    return bool(row and country_is_blocked(row["country"], config))


def now() -> str:
    return guard.utc_now()


def connect_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=2)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relays (
            hostname TEXT PRIMARY KEY,
            country TEXT NOT NULL,
            country_name TEXT NOT NULL,
            city TEXT NOT NULL,
            city_name TEXT NOT NULL,
            provider TEXT NOT NULL,
            ownership TEXT NOT NULL,
            ipv4 TEXT,
            ipv6 TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            fast_probe_at TEXT,
            fast_latency_ms REAL,
            fast_reachable INTEGER NOT NULL DEFAULT 0,
            fast_score REAL,
            fast_error TEXT,
            last_test_at TEXT,
            last_success_at TEXT,
            last_latency_ms REAL,
            last_mbps REAL,
            best_mbps REAL,
            score REAL,
            attempts INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'unknown',
            last_error TEXT,
            last_health_check_at TEXT,
            last_health_ok INTEGER,
            last_health_error TEXT,
            last_health_latency_ms REAL,
            last_health_mbps REAL
        )
        """
    )
    ensure_columns(
        conn,
        "relays",
        {
            "ipv4": "TEXT",
            "ipv6": "TEXT",
            "fast_probe_at": "TEXT",
            "fast_latency_ms": "REAL",
            "fast_reachable": "INTEGER NOT NULL DEFAULT 0",
            "fast_score": "REAL",
            "fast_error": "TEXT",
            "last_health_check_at": "TEXT",
            "last_health_ok": "INTEGER",
            "last_health_error": "TEXT",
            "last_health_latency_ms": "REAL",
            "last_health_mbps": "REAL",
            "last_loss_pct": "REAL",
            "best_loss_pct": "REAL",
            "last_health_loss_pct": "REAL",
        },
    )
    conn.execute(
        """
        UPDATE relays
        SET last_health_ok=NULL
        WHERE last_health_ok=1
          AND (last_health_latency_ms IS NULL OR last_health_mbps IS NULL)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            connected INTEGER NOT NULL,
            latency_ms REAL,
            download_mbps REAL,
            score REAL,
            error TEXT,
            loss_pct REAL
        )
        """
    )
    ensure_columns(conn, "relay_results", {"loss_pct": "REAL"})
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relays_status ON relays(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relays_score ON relays(score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relays_fast_score ON relays(fast_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_hostname ON relay_results(hostname)")
    conn.commit()


def ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_stamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def meta_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM inventory_meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_meta_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO inventory_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def relay_sync_cache_state(conn: sqlite3.Connection, ttl_seconds: int = RELAY_SYNC_TTL_SECONDS) -> Dict[str, Any]:
    last_sync_at = meta_value(conn, META_LAST_RELAY_SYNC_AT)
    last_sync = parse_stamp(last_sync_at)
    relay_count = int(conn.execute("SELECT COUNT(*) AS c FROM relays").fetchone()["c"])
    age_seconds: Optional[int] = None
    if last_sync is not None:
        elapsed = dt.datetime.now(dt.timezone.utc) - last_sync
        age_seconds = max(0, int(elapsed.total_seconds()))
    fresh = relay_count > 0 and age_seconds is not None and age_seconds < max(0, int(ttl_seconds))
    return {
        "last_sync_at": last_sync_at,
        "cache_age_seconds": age_seconds,
        "cache_ttl_seconds": max(0, int(ttl_seconds)),
        "relay_count": relay_count,
        "fresh": fresh,
    }


def meta_age_state(conn: sqlite3.Connection, key: str, cooldown_seconds: int) -> Dict[str, Any]:
    last_at = meta_value(conn, key)
    last_stamp = parse_stamp(last_at)
    age_seconds: Optional[int] = None
    if last_stamp is not None:
        elapsed = dt.datetime.now(dt.timezone.utc) - last_stamp
        age_seconds = max(0, int(elapsed.total_seconds()))
    cooldown = max(0, int(cooldown_seconds))
    return {
        "last_at": last_at,
        "age_seconds": age_seconds,
        "cooldown_seconds": cooldown,
        "due": age_seconds is None or age_seconds >= cooldown,
    }


def nightly_full_scan_state(
    conn: sqlite3.Connection,
    cooldown_seconds: int = DEFAULT_NIGHTLY_FULL_SCAN_COOLDOWN_SECONDS,
) -> Dict[str, Any]:
    state = meta_age_state(conn, META_LAST_NIGHTLY_FULL_SCAN_AT, cooldown_seconds)
    state["last_result"] = meta_value(conn, META_LAST_NIGHTLY_FULL_SCAN_RESULT)
    return state


def nightly_full_scan_due(
    cooldown_seconds: int = DEFAULT_NIGHTLY_FULL_SCAN_COOLDOWN_SECONDS,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    with connect_db(db_path) as conn:
        return nightly_full_scan_state(conn, cooldown_seconds)


def speed_beats_baseline(
    candidate_speed: Optional[float],
    baseline_speed: Optional[float],
    min_working_mbps: float = MIN_WORKING_MBPS,
    better_min_delta_mbps: float = DEFAULT_BETTER_MIN_DELTA_MBPS,
    better_min_ratio: float = DEFAULT_BETTER_MIN_RATIO,
) -> Dict[str, Any]:
    candidate = optional_float(candidate_speed)
    baseline = optional_float(baseline_speed)
    min_working = float(min_working_mbps)
    delta = max(0.0, float(better_min_delta_mbps))
    ratio = max(1.0, float(better_min_ratio))
    if candidate is None:
        return {"ok": False, "reason": "candidate has no speed"}
    if candidate < min_working:
        return {
            "ok": False,
            "reason": "candidate below working floor",
            "candidate_speed_mbps": round(candidate, 2),
            "min_working_mbps": round(min_working, 2),
        }
    if baseline is None:
        return {
            "ok": True,
            "reason": "no current baseline speed",
            "candidate_speed_mbps": round(candidate, 2),
        }
    required = max(baseline + delta, baseline * ratio)
    return {
        "ok": candidate >= required,
        "reason": "candidate is clearly faster" if candidate >= required else "candidate not clearly faster",
        "candidate_speed_mbps": round(candidate, 2),
        "baseline_speed_mbps": round(baseline, 2),
        "required_speed_mbps": round(required, 2),
        "better_min_delta_mbps": delta,
        "better_min_ratio": ratio,
    }


def sync_relays(update: bool = False, db_path: Path = DB_PATH) -> Dict[str, Any]:
    with connect_db(db_path) as conn:
        cache_state = relay_sync_cache_state(conn)
        if not update and cache_state["fresh"]:
            return {
                "total": cache_state["relay_count"],
                "counts": counts(conn),
                "cached": True,
                "last_sync_at": cache_state["last_sync_at"],
                "cache_age_seconds": cache_state["cache_age_seconds"],
                "cache_ttl_seconds": cache_state["cache_ttl_seconds"],
            }
    if update:
        guard.update_relay_list()
    relays = guard.parse_relays(guard.relay_list_text())
    stamp = now()
    with connect_db(db_path) as conn:
        for relay in relays:
            conn.execute(
                """
                INSERT INTO relays (
                    hostname, country, country_name, city, city_name, provider, ownership, ipv4, ipv6,
                    first_seen_at, last_seen_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown')
                ON CONFLICT(hostname) DO UPDATE SET
                    country=excluded.country,
                    country_name=excluded.country_name,
                    city=excluded.city,
                    city_name=excluded.city_name,
                    provider=excluded.provider,
                    ownership=excluded.ownership,
                    ipv4=excluded.ipv4,
                    ipv6=excluded.ipv6,
                    last_seen_at=excluded.last_seen_at,
                    status=CASE
                        WHEN relays.status='retired' THEN 'unknown'
                        ELSE relays.status
                    END
                """,
                (
                    relay.hostname,
                    relay.country,
                    relay.country_name,
                    relay.city,
                    relay.city_name,
                    relay.provider,
                    relay.ownership,
                    relay.ipv4,
                    relay.ipv6,
                    stamp,
                    stamp,
                ),
            )
        conn.execute(
            "UPDATE relays SET status='retired' WHERE last_seen_at != ? AND status != 'retired'",
            (stamp,),
        )
        set_meta_value(conn, META_LAST_RELAY_SYNC_AT, stamp)
        cache_state = relay_sync_cache_state(conn)
        conn.commit()
        return {
            "total": len(relays),
            "counts": counts(conn),
            "cached": False,
            "last_sync_at": stamp,
            "cache_age_seconds": cache_state["cache_age_seconds"],
            "cache_ttl_seconds": cache_state["cache_ttl_seconds"],
        }


def counts(conn: Optional[sqlite3.Connection] = None, db_path: Path = DB_PATH) -> Dict[str, int]:
    close = False
    if conn is None:
        conn = connect_db(db_path)
        close = True
    try:
        result = {"total": 0, "unknown": 0, "working": 0, "no_speed": 0, "abandoned": 0, "retired": 0}
        for row in conn.execute("SELECT status, COUNT(*) AS c FROM relays GROUP BY status"):
            result[str(row["status"])] = int(row["c"])
            result["total"] += int(row["c"])
        return result
    finally:
        if close:
            conn.close()


def top_relays(limit: int = 5, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    blocked_sql, blocked_params = blocked_country_sql()
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM relays
            WHERE status='working' AND last_mbps IS NOT NULL{blocked_sql}
            ORDER BY
                CASE
                    WHEN last_health_ok=1 AND last_health_latency_ms IS NOT NULL AND last_health_mbps IS NOT NULL THEN 0
                    WHEN last_health_ok=0 AND last_health_check_at IS NOT NULL THEN 2
                    ELSE 1
                END,
                score DESC,
                last_mbps DESC,
                best_mbps DESC
            LIMIT ?
            """,
            (*blocked_params, limit),
        ).fetchall()
        return [row_dict(row) for row in rows]


def fast_top_relays(limit: int = 5, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    blocked_sql, blocked_params = blocked_country_sql()
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT *,
                CASE
                    WHEN status='working' AND success_count > 0 THEN 'verified'
                    WHEN status!='working' AND COALESCE(best_mbps, last_mbps) IS NOT NULL AND success_count > 0 THEN 'previously verified'
                    WHEN status='abandoned' THEN 'avoid'
                    WHEN status='no_speed' AND last_test_at IS NOT NULL THEN 'failed true-test'
                    WHEN consecutive_failures > 0 AND last_test_at IS NOT NULL THEN 'failed true-test'
                    WHEN last_test_at IS NULL THEN 'untested'
                    ELSE 'unverified'
                END AS candidate_risk,
                CASE
                    WHEN status='working' AND success_count > 0 THEN 0
                    WHEN last_test_at IS NULL THEN 1
                    WHEN status!='working' AND COALESCE(best_mbps, last_mbps) IS NOT NULL AND success_count > 0 THEN 2
                    WHEN status='abandoned' THEN 4
                    WHEN status='no_speed' AND last_test_at IS NOT NULL THEN 3
                    WHEN consecutive_failures > 0 AND last_test_at IS NOT NULL THEN 3
                    ELSE 2
                END AS candidate_rank
            FROM relays
            WHERE fast_probe_at IS NOT NULL{blocked_sql}
            ORDER BY
                fast_reachable DESC,
                candidate_rank ASC,
                CASE
                    WHEN last_health_ok=1 AND last_health_latency_ms IS NOT NULL AND last_health_mbps IS NOT NULL THEN 0
                    WHEN last_health_ok=0 AND last_health_check_at IS NOT NULL THEN 2
                    ELSE 1
                END,
                COALESCE(best_mbps, last_mbps, 0) DESC,
                CASE WHEN fast_latency_ms IS NULL THEN 1 ELSE 0 END,
                fast_latency_ms ASC,
                fast_score DESC,
                hostname
            LIMIT ?
            """,
            (*blocked_params, limit),
        ).fetchall()
        return [row_dict(row) for row in rows]


def verified_candidate_relays(
    limit: int = 5,
    min_mbps: float = MIN_WORKING_MBPS,
    hostnames: Optional[Iterable[str]] = None,
    db_path: Path = DB_PATH,
) -> List[Dict[str, Any]]:
    names = [name.strip().lower() for name in (hostnames or []) if name.strip()]
    where = "status='working' AND last_mbps IS NOT NULL AND last_mbps >= ?"
    params: List[Any] = [min_mbps]
    if names:
        placeholders = ",".join("?" for _ in names)
        where += f" AND hostname IN ({placeholders})"
        params.extend(names)
    blocked_sql, blocked_params = blocked_country_sql()
    where += blocked_sql
    params.extend(blocked_params)
    sql = f"""
        SELECT *
        FROM relays
        WHERE {where}
        ORDER BY
            CASE
                WHEN last_health_ok=1 AND last_health_latency_ms IS NOT NULL AND last_health_mbps IS NOT NULL THEN 0
                WHEN last_health_ok=0 AND last_health_check_at IS NOT NULL THEN 2
                ELSE 1
            END,
            CASE
                WHEN last_health_ok=1 AND last_health_latency_ms IS NOT NULL AND last_health_mbps IS NOT NULL
                THEN last_health_check_at
                ELSE NULL
            END DESC,
            score DESC,
            last_mbps DESC,
            best_mbps DESC,
            hostname
        LIMIT ?
    """
    params.append(limit)
    with connect_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_dict(row) for row in rows]


def observed_hour(value: str) -> Optional[int]:
    try:
        return dt.datetime.fromisoformat(value).astimezone().hour
    except (TypeError, ValueError):
        return None


def whitelist_relays(
    limit: int = 10,
    min_mbps: float = MIN_WORKING_MBPS,
    preferred_mbps: float = 8.0,
    current_hour: Optional[int] = None,
    history_limit: int = 2000,
    db_path: Path = DB_PATH,
) -> List[Dict[str, Any]]:
    """Rank historically reliable relays, with extra weight for the current local hour."""
    current_hour = dt.datetime.now().astimezone().hour if current_hour is None else int(current_hour) % 24
    min_mbps = float(min_mbps)
    preferred_mbps = float(preferred_mbps)
    history_limit = max(50, int(history_limit))
    blocked_sql, blocked_params = blocked_country_sql("relays.country")

    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                r.hostname, r.observed_at, r.connected, r.latency_ms, r.download_mbps, r.score AS result_score,
                relays.country, relays.country_name, relays.city, relays.city_name, relays.provider,
                relays.ownership, relays.ipv4, relays.ipv6, relays.status, relays.last_mbps,
                relays.best_mbps, relays.last_latency_ms, relays.consecutive_failures,
                relays.last_health_check_at, relays.last_health_ok, relays.last_health_error,
                relays.last_health_latency_ms, relays.last_health_mbps
            FROM relay_results r
            JOIN relays ON relays.hostname = r.hostname
            WHERE relays.status != 'retired'{blocked_sql}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (*blocked_params, history_limit),
        ).fetchall()

    stats: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        hostname = str(row["hostname"])
        item = stats.setdefault(
            hostname,
            {
                "hostname": hostname,
                "country": row["country"],
                "country_name": row["country_name"],
                "city": row["city"],
                "city_name": row["city_name"],
                "provider": row["provider"],
                "ownership": row["ownership"],
                "ipv4": row["ipv4"],
                "ipv6": row["ipv6"],
                "status": row["status"],
                "last_mbps": row["last_mbps"],
                "best_mbps": row["best_mbps"],
                "last_latency_ms": row["last_latency_ms"],
                "consecutive_failures": row["consecutive_failures"],
                "last_health_check_at": row["last_health_check_at"],
                "last_health_ok": row["last_health_ok"],
                "last_health_error": row["last_health_error"],
                "last_health_latency_ms": row["last_health_latency_ms"],
                "last_health_mbps": row["last_health_mbps"],
                "attempts": 0,
                "successes": 0,
                "preferred_hits": 0,
                "hour_attempts": 0,
                "hour_successes": 0,
                "hour_preferred_hits": 0,
                "speed_sum": 0.0,
                "hour_speed_sum": 0.0,
                "latency_sum": 0.0,
                "latency_count": 0,
                "last_success_at": None,
            },
        )
        item["attempts"] += 1
        hour = observed_hour(str(row["observed_at"]))
        hour_match = hour == current_hour
        if hour_match:
            item["hour_attempts"] += 1

        speed = row["download_mbps"]
        latency = row["latency_ms"]
        success = bool(row["connected"] and speed is not None and float(speed) >= min_mbps)
        if success:
            item["successes"] += 1
            item["speed_sum"] += float(speed)
            item["last_success_at"] = item["last_success_at"] or row["observed_at"]
            if float(speed) >= preferred_mbps:
                item["preferred_hits"] += 1
            if hour_match:
                item["hour_successes"] += 1
                item["hour_speed_sum"] += float(speed)
                if float(speed) >= preferred_mbps:
                    item["hour_preferred_hits"] += 1
        if latency is not None:
            item["latency_sum"] += float(latency)
            item["latency_count"] += 1

    ranked: List[Dict[str, Any]] = []
    for item in stats.values():
        if item["successes"] <= 0:
            continue
        attempts = max(1, int(item["attempts"]))
        hour_attempts = max(1, int(item["hour_attempts"]))
        avg_mbps = item["speed_sum"] / item["successes"]
        hour_avg_mbps = item["hour_speed_sum"] / item["hour_successes"] if item["hour_successes"] else None
        avg_latency = item["latency_sum"] / item["latency_count"] if item["latency_count"] else 9999.0
        success_rate = item["successes"] / attempts
        hour_success_rate = item["hour_successes"] / hour_attempts if item["hour_attempts"] else 0.0
        score = (
            success_rate * 5000
            + avg_mbps * 650
            + int(item["preferred_hits"]) * 1200
            - avg_latency * 0.8
            - int(item["consecutive_failures"] or 0) * 1000
        )
        if item["hour_attempts"]:
            score += 2500 + hour_success_rate * 3500
            if hour_avg_mbps is not None:
                score += hour_avg_mbps * 900
            score += int(item["hour_preferred_hits"]) * 1800
        full_health_ok = (
            item["last_health_ok"] == 1
            and item["last_health_latency_ms"] is not None
            and item["last_health_mbps"] is not None
        )
        if full_health_ok:
            score += 5000
        elif item["last_health_ok"] == 0 and item["last_health_check_at"]:
            score -= 7000
        item["avg_mbps"] = round(avg_mbps, 2)
        item["hour_avg_mbps"] = round(hour_avg_mbps, 2) if hour_avg_mbps is not None else None
        item["avg_latency_ms"] = round(avg_latency, 1) if avg_latency < 9999 else None
        item["success_rate"] = round(success_rate, 3)
        item["hour_success_rate"] = round(hour_success_rate, 3) if item["hour_attempts"] else None
        item["whitelist_score"] = round(score, 2)
        item["current_hour"] = current_hour
        ranked.append(item)

    def health_rank(row: Dict[str, Any]) -> int:
        if (
            row.get("last_health_ok") == 1
            and row.get("last_health_latency_ms") is not None
            and row.get("last_health_mbps") is not None
        ):
            return 0
        if row.get("last_health_ok") == 0 and row.get("last_health_check_at"):
            return 2
        return 1

    ranked.sort(key=lambda row: (health_rank(row), -float(row["whitelist_score"]), str(row["hostname"])))
    return ranked[: max(1, int(limit))]


def recent_results(limit: int = 20, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    blocked = blocked_country_list()
    where = ""
    params: List[Any] = []
    if blocked:
        placeholders = ",".join("?" for _ in blocked)
        where = f"WHERE (relays.country IS NULL OR relays.country NOT IN ({placeholders}))"
        params.extend(blocked)
    params.append(limit)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT r.*, relays.country, relays.city, relays.provider
            FROM relay_results r
            LEFT JOIN relays ON relays.hostname = r.hostname
            {where}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [row_dict(row) for row in rows]


def summary(db_path: Path = DB_PATH) -> Dict[str, Any]:
    with connect_db(db_path) as conn:
        return {
            "counts": counts(conn),
            "relay_sync": relay_sync_cache_state(conn),
            "nightly_full_scan": nightly_full_scan_state(conn),
            "top5": top_relays(5, db_path),
            "fast_top5": fast_top_relays(10, db_path),
            "ready_top5": verified_candidate_relays(5, MIN_WORKING_MBPS, db_path=db_path),
            "whitelist_top5": whitelist_relays(5, min_mbps=MIN_WORKING_MBPS, preferred_mbps=8.0, db_path=db_path),
            "recent": recent_results(15, db_path),
        }


def record_result(
    result: guard.TestResult,
    min_working_mbps: float = MIN_WORKING_MBPS,
    abandon_after: int = ABANDON_AFTER,
    db_path: Path = DB_PATH,
) -> None:
    with connect_db(db_path) as conn:
        row = conn.execute("SELECT * FROM relays WHERE hostname=?", (result.hostname,)).fetchone()
        consecutive = int(row["consecutive_failures"]) if row else 0
        success_count = int(row["success_count"]) if row else 0
        attempts = int(row["attempts"]) if row else 0
        best_mbps = row["best_mbps"] if row else None

        loss_pct = getattr(result, "loss_pct", None)
        best_loss = row["best_loss_pct"] if row and "best_loss_pct" in row.keys() else None

        has_speed = bool(result.connected and result.download_mbps is not None and result.download_mbps >= min_working_mbps)
        if has_speed:
            status = "working"
            consecutive = 0
            success_count += 1
            best_mbps = max(float(best_mbps or 0), float(result.download_mbps or 0))
            last_success_at = result.observed_at
            if loss_pct is not None:
                # "best" loss is the lowest (cleanest) observed on a working test.
                best_loss = loss_pct if best_loss is None else min(float(best_loss), float(loss_pct))
        else:
            consecutive += 1
            last_success_at = row["last_success_at"] if row else None
            status = "abandoned" if consecutive >= abandon_after else "no_speed"

        conn.execute(
            """
            UPDATE relays
            SET
                last_test_at=?,
                last_success_at=?,
                last_latency_ms=?,
                last_mbps=?,
                best_mbps=?,
                score=?,
                attempts=?,
                success_count=?,
                consecutive_failures=?,
                status=?,
                last_error=?,
                last_loss_pct=?,
                best_loss_pct=?
            WHERE hostname=?
            """,
            (
                result.observed_at,
                last_success_at,
                result.latency_ms,
                result.download_mbps,
                best_mbps,
                result.score,
                attempts + 1,
                success_count,
                consecutive,
                status,
                result.error,
                loss_pct,
                best_loss,
                result.hostname,
            ),
        )
        conn.execute(
            """
            INSERT INTO relay_results (
                hostname, observed_at, connected, latency_ms, download_mbps, score, error, loss_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.hostname,
                result.observed_at,
                1 if result.connected else 0,
                result.latency_ms,
                result.download_mbps,
                result.score,
                result.error,
                loss_pct,
            ),
        )
        conn.commit()


def record_health_check(
    hostname: Optional[str],
    sample: guard.HealthSample,
    db_path: Path = DB_PATH,
) -> None:
    """Persist app-level health so speed-only relays do not dominate backups."""
    if not hostname:
        return
    try:
        with connect_db(db_path) as conn:
            loss_pct = getattr(sample, "loss_pct", None)
            conn.execute(
                """
                UPDATE relays
                SET
                    last_health_check_at=?,
                    last_health_ok=?,
                    last_health_error=?,
                    last_health_latency_ms=?,
                    last_health_mbps=?,
                    last_health_loss_pct=?,
                    last_loss_pct=COALESCE(?, last_loss_pct)
                WHERE hostname=?
                """,
                (
                    now(),
                    1 if sample.ok else 0,
                    None if sample.ok else sample.reason,
                    sample.latency_ms,
                    sample.speed_mbps,
                    loss_pct,
                    loss_pct,
                    hostname.strip().lower(),
                ),
            )
            conn.commit()
    except Exception as exc:
        print(f"Health record skipped for {hostname}: {exc}", flush=True)


def full_health_check(config: Dict[str, Any]) -> guard.HealthSample:
    """Require URL probes, latency, and speed before accepting a relay as healthy."""
    state, _ = guard.mullvad_status()
    if not state.lower().startswith("connected"):
        return guard.HealthSample(False, f"VPN state is {state}")

    url_failure = guard.measure_url_checks(config)
    if url_failure:
        return guard.HealthSample(False, url_failure)

    latency = guard.measure_latency(config)
    if latency is None:
        return guard.HealthSample(False, "latency probe failed")
    rounded_latency = round(latency, 1)
    max_latency = float(config.get("max_latency_ms") or 0)
    if max_latency > 0 and latency > max_latency:
        return guard.HealthSample(
            False,
            f"latency {latency:.1f} ms above {max_latency:.1f} ms",
            latency_ms=rounded_latency,
        )

    speed = guard.measure_download(config, bytes_key="health_download_bytes")
    if speed is None:
        return guard.HealthSample(False, "speed sample failed", latency_ms=rounded_latency, ran_speed_test=True)
    min_mbps = float(config.get("min_mbps", 0.5))
    if speed < min_mbps:
        return guard.HealthSample(
            False,
            f"speed {speed:.2f} Mbps below {min_mbps:.2f} Mbps",
            latency_ms=rounded_latency,
            speed_mbps=round(speed, 2),
            ran_speed_test=True,
        )
    loss = guard.measure_packet_loss(config)
    loss_rounded = round(loss, 1) if loss is not None else None
    max_loss = float(config.get("max_loss_pct") or 0)
    if max_loss > 0 and loss is not None and loss > max_loss:
        return guard.HealthSample(
            False,
            f"packet loss {loss:.0f}% above {max_loss:.0f}% (speed {speed:.2f} Mbps)",
            latency_ms=rounded_latency,
            speed_mbps=round(speed, 2),
            ran_speed_test=True,
            loss_pct=loss_rounded,
        )
    loss_note = f" loss {loss:.0f}%" if loss is not None else ""
    return guard.HealthSample(
        True,
        f"full health speed {speed:.2f} Mbps latency {latency:.1f} ms{loss_note}",
        latency_ms=rounded_latency,
        speed_mbps=round(speed, 2),
        ran_speed_test=True,
        loss_pct=loss_rounded,
    )


def relay_from_row(row: sqlite3.Row) -> guard.Relay:
    return guard.Relay(
        hostname=row["hostname"],
        country=row["country"],
        country_name=row["country_name"],
        city=row["city"],
        city_name=row["city_name"],
        provider=row["provider"],
        ownership=row["ownership"],
        ipv4=row["ipv4"],
        ipv6=row["ipv6"],
    )


def tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, Optional[float], Optional[str]]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, (time.monotonic() - start) * 1000, None
    except OSError as exc:
        return False, None, exc.__class__.__name__


def fast_rank_all(
    workers: int = 64,
    timeout: float = 1.2,
    port: int = 443,
    limit: int = 0,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    sync_relays(update=False, db_path=db_path)
    blocked_sql, blocked_params = blocked_country_sql()
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT hostname, ipv4, country, city, provider
            FROM relays
            WHERE status != 'retired' AND ipv4 IS NOT NULL{blocked_sql}
            ORDER BY hostname
            """,
            blocked_params,
        ).fetchall()

    if limit > 0:
        rows = rows[:limit]

    started = time.monotonic()
    stamp = now()
    results: List[Dict[str, Any]] = []

    def probe(row: sqlite3.Row) -> Dict[str, Any]:
        reachable, latency, error = tcp_probe(str(row["ipv4"]), port=port, timeout=timeout)
        score = (100000.0 - float(latency)) if reachable and latency is not None else -100000.0
        return {
            "hostname": row["hostname"],
            "ipv4": row["ipv4"],
            "country": row["country"],
            "city": row["city"],
            "provider": row["provider"],
            "fast_probe_at": stamp,
            "fast_reachable": 1 if reachable else 0,
            "fast_latency_ms": round(latency, 1) if latency is not None else None,
            "fast_score": round(score, 2),
            "fast_error": error,
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(probe, row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())

    with connect_db(db_path) as conn:
        for item in results:
            conn.execute(
                """
                UPDATE relays
                SET fast_probe_at=?, fast_reachable=?, fast_latency_ms=?, fast_score=?, fast_error=?
                WHERE hostname=?
                """,
                (
                    item["fast_probe_at"],
                    item["fast_reachable"],
                    item["fast_latency_ms"],
                    item["fast_score"],
                    item["fast_error"],
                    item["hostname"],
                ),
            )
        conn.commit()

    elapsed = time.monotonic() - started
    reachable_count = sum(1 for item in results if item["fast_reachable"])
    return {
        "tested": len(results),
        "reachable": reachable_count,
        "elapsed_seconds": round(elapsed, 2),
        "top5": fast_top_relays(5, db_path),
    }


def relays_for_scope(
    scope: str,
    limit: int = 0,
    include_abandoned: bool = False,
    db_path: Path = DB_PATH,
) -> List[guard.Relay]:
    sync_relays(update=False, db_path=db_path)
    scope = scope.lower()
    where = "status != 'retired'"
    params: List[Any] = []

    if scope == "active":
        where = "status='working' AND last_mbps IS NOT NULL"
    elif scope == "unknown":
        where = "status='unknown'"
    elif scope == "untested":
        where = "status IN ('unknown', 'no_speed')"
    elif scope == "abandoned":
        where = "status='abandoned'"
    elif scope == "top":
        where = "status='working' AND last_mbps IS NOT NULL"
    elif scope == "fast":
        where = "fast_probe_at IS NOT NULL"
    elif scope == "full":
        where = "status != 'retired'" if include_abandoned else "status NOT IN ('retired', 'abandoned')"
    else:
        raise ValueError(f"Unknown inventory scope: {scope}")

    blocked_sql, blocked_params = blocked_country_sql()
    where += blocked_sql
    params.extend(blocked_params)

    if scope in {"active", "top"}:
        order = "score DESC, last_mbps DESC, hostname"
    elif scope == "fast":
        order = "fast_reachable DESC, fast_score DESC, fast_latency_ms ASC, hostname"
    else:
        order = "hostname"
    sql = f"SELECT * FROM relays WHERE {where} ORDER BY {order}"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    with connect_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [relay_from_row(row) for row in rows]


def relays_by_hostnames(hostnames: Iterable[str], db_path: Path = DB_PATH) -> List[guard.Relay]:
    names = [name.strip().lower() for name in hostnames if name.strip()]
    if not names:
        return []
    sync_relays(update=False, db_path=db_path)
    placeholders = ",".join("?" for _ in names)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM relays WHERE hostname IN ({placeholders}) ORDER BY hostname",
            names,
        ).fetchall()
        blocked = [str(row["hostname"]) for row in rows if country_is_blocked(row["country"])]
        if blocked:
            raise ValueError(
                "Blocked relay hostname(s) by country policy: "
                + ", ".join(blocked)
            )
        by_name = {row["hostname"]: relay_from_row(row) for row in rows}
        found = set(by_name)
    missing = [name for name in names if name not in found]
    if missing:
        raise ValueError(f"Unknown relay hostname(s): {', '.join(missing)}")
    return [by_name[name] for name in names]


def print_scan_line(result: guard.TestResult) -> None:
    speed = "n/a" if result.download_mbps is None else f"{result.download_mbps:.2f} Mbps"
    latency = "n/a" if result.latency_ms is None else f"{result.latency_ms:.1f} ms"
    loss = "n/a" if getattr(result, "loss_pct", None) is None else f"{result.loss_pct:.0f}%"
    status = "ok" if result.connected and result.download_mbps is not None else "no-speed"
    print(
        f"{result.observed_at} {result.hostname:18} {status:8} speed={speed:>12} latency={latency:>10} loss={loss:>5} score={result.score:.2f}",
        flush=True,
    )


def scan_inventory(
    config: Dict[str, Any],
    scope: str = "full",
    limit: int = 0,
    include_abandoned: bool = True,
    restore: bool = True,
    connect_best: bool = False,
    dry_run: bool = False,
    min_working_mbps: float = MIN_WORKING_MBPS,
    abandon_after: int = ABANDON_AFTER,
    db_path: Path = DB_PATH,
    hostnames: Optional[Iterable[str]] = None,
    record_full_health: bool = False,
) -> Optional[guard.TestResult]:
    sync_relays(update=False, db_path=db_path)
    relays = (
        relays_by_hostnames(hostnames, db_path=db_path)
        if hostnames
        else relays_for_scope(scope, limit=limit, include_abandoned=include_abandoned, db_path=db_path)
    )
    print(f"Inventory scan scope={scope} matched {len(relays)} relay(s).", flush=True)
    if dry_run:
        for relay in relays:
            print(f"{relay.hostname:18} {relay.country}/{relay.city} {relay.provider}")
        return None

    previous_relay = guard.current_relay_hostname()
    previous_state, _ = guard.mullvad_status()
    previous_connected = previous_state.lower().startswith("connected")
    best: Optional[guard.TestResult] = None

    interrupted = False

    def handle_stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    try:
        for index, relay in enumerate(relays, start=1):
            print(f"[{index}/{len(relays)}] Testing {relay.hostname}", flush=True)
            result = guard.test_relay(relay, config)
            record_result(result, min_working_mbps=min_working_mbps, abandon_after=abandon_after, db_path=db_path)
            print_scan_line(result)
            if (
                record_full_health
                and result.connected
                and result.download_mbps is not None
                and float(result.download_mbps) >= min_working_mbps
            ):
                try:
                    actual = guard.current_relay_hostname() or relay.hostname
                    health_config = dict(config)
                    sample = full_health_check(health_config)
                    record_health_check(actual, sample, db_path=db_path)
                    print(
                        f"Inventory full-health check {relay.hostname}->{actual}: "
                        f"{'ok' if sample.ok else 'bad'} {sample.reason}",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"Inventory full-health check failed for {relay.hostname}: {exc}", flush=True)
            if result.connected and result.download_mbps is not None and (best is None or result.score > best.score):
                best = result
    except KeyboardInterrupt:
        interrupted = True
        print("Inventory scan interrupted; restoring previous relay.", flush=True)
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        if connect_best and best and not interrupted:
            print(f"Connecting best inventory relay: {best.hostname}", flush=True)
            connected = connect_relay(best.hostname, config)
            print(f"Connected best inventory relay: {connected}", flush=True)
        elif restore:
            guard.restore_previous(previous_relay, previous_connected, config)
    return best


def reachable_fast_hostnames(limit: int, db_path: Path = DB_PATH) -> List[str]:
    rows = fast_top_relays(limit=limit, db_path=db_path)
    return [
        str(row["hostname"])
        for row in rows
        if int(row.get("fast_reachable") or 0) == 1
    ]


def emergency_rescue_hostnames(
    limit: int = 0,
    current_hostname: Optional[str] = None,
    exclude_hostnames: Optional[Iterable[str]] = None,
    db_path: Path = DB_PATH,
) -> List[str]:
    """Rank every non-retired relay for an emergency true-test sweep."""
    sync_relays(update=False, db_path=db_path)
    excluded = {name.strip().lower() for name in (exclude_hostnames or []) if name.strip()}
    if current_hostname:
        excluded.add(current_hostname.strip().lower())

    where = "status != 'retired'"
    params: List[Any] = []
    if excluded:
        placeholders = ",".join("?" for _ in excluded)
        where += f" AND hostname NOT IN ({placeholders})"
        params.extend(sorted(excluded))
    blocked_sql, blocked_params = blocked_country_sql()
    where += blocked_sql
    params.extend(blocked_params)

    sql = f"""
        SELECT hostname
        FROM relays
        WHERE {where}
        ORDER BY
            CASE
                WHEN last_health_ok=1 AND last_health_latency_ms IS NOT NULL AND last_health_mbps IS NOT NULL THEN 0
                WHEN last_health_ok=0 AND last_health_check_at IS NOT NULL THEN 2
                ELSE 1
            END,
            CASE
                WHEN status='working' AND success_count > 0 THEN 0
                WHEN status='unknown' THEN 1
                WHEN status='no_speed' THEN 2
                WHEN status='abandoned' THEN 4
                ELSE 3
            END,
            fast_reachable DESC,
            CASE WHEN fast_latency_ms IS NULL THEN 1 ELSE 0 END,
            fast_latency_ms ASC,
            COALESCE(best_mbps, last_mbps, 0) DESC,
            consecutive_failures ASC,
            CASE WHEN last_test_at IS NULL THEN 0 ELSE 1 END,
            last_test_at ASC,
            hostname
    """
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    with connect_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [str(row["hostname"]) for row in rows]


def emergency_rescue_true_test(
    config: Dict[str, Any],
    batch_size: int = DEFAULT_POOL_SIZE,
    candidate_limit: int = 0,
    min_working_mbps: float = MIN_WORKING_MBPS,
    abandon_after: int = ABANDON_AFTER,
    fast_workers: int = DEFAULT_FAST_WORKERS,
    fast_timeout: float = DEFAULT_FAST_TIMEOUT,
    fast_port: int = DEFAULT_FAST_PORT,
    exclude_hostnames: Optional[Iterable[str]] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """True-test emergency candidates in batches until one passes full health."""
    batch_size = max(1, int(batch_size))
    min_working_mbps = float(min_working_mbps)
    candidate_limit = max(0, int(candidate_limit))
    try:
        initial_state, _ = guard.mullvad_status(timeout=5)
    except Exception as exc:
        print(f"Emergency rescue could not read entry VPN state: {exc}", flush=True)
        initial_state = "Unknown"
    try:
        current = guard.current_relay_hostname()
    except Exception as exc:
        print(f"Emergency rescue could not read entry relay: {exc}", flush=True)
        current = None
    fallback_hostname: Optional[str] = current if initial_state.lower().startswith("connected") and current else None
    fallback_reason: Optional[str] = "entry-current" if fallback_hostname else None
    fallback_score = float("-inf")
    fallback_connected: Optional[Dict[str, Any]] = None
    restore_error: Optional[str] = None

    fast_data = fast_rank_all(
        workers=fast_workers,
        timeout=fast_timeout,
        port=fast_port,
        db_path=db_path,
    )
    candidates = emergency_rescue_hostnames(
        limit=candidate_limit,
        current_hostname=current,
        exclude_hostnames=exclude_hostnames,
        db_path=db_path,
    )
    if not candidates and exclude_hostnames:
        candidates = emergency_rescue_hostnames(
            limit=candidate_limit,
            current_hostname=current,
            db_path=db_path,
        )

    tested: List[str] = []
    attempted_ready: List[str] = []
    connected: Optional[Dict[str, Any]] = None
    post_check: Optional[Dict[str, Any]] = None
    test_config = dict(config)
    health_bytes = int(test_config.get("health_download_bytes", 1_000_000) or 1_000_000)
    download_bytes = int(test_config.get("download_bytes", health_bytes) or health_bytes)
    test_config["download_bytes"] = min(download_bytes, health_bytes)
    test_config["download_timeout_seconds"] = min(int(test_config.get("download_timeout_seconds", 12) or 12), 8)

    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]
        batch_number = (offset // batch_size) + 1
        print(
            f"Emergency rescue true-test batch {batch_number} "
            f"size={len(batch)} tested_so_far={len(tested)} candidates={len(candidates)}",
            flush=True,
        )
        relays = relays_by_hostnames(batch, db_path=db_path)
        for relay in relays:
            print(f"[{len(tested) + 1}/{len(candidates)}] Emergency rescue true-testing {relay.hostname}", flush=True)
            result = guard.test_relay(relay, test_config)
            record_result(
                result,
                min_working_mbps=min_working_mbps,
                abandon_after=abandon_after,
                db_path=db_path,
            )
            print_scan_line(result)
            tested.append(relay.hostname)
            if not (
                result.connected
                and result.download_mbps is not None
                and float(result.download_mbps) >= min_working_mbps
            ):
                continue
            hostname = relay.hostname
            attempted_ready.append(hostname)
            try:
                actual = guard.current_relay_hostname()
            except Exception as exc:
                print(f"Emergency rescue could not read current relay after {hostname}: {exc}", flush=True)
                actual = None
            score = float(result.score or 0.0)
            if score >= fallback_score:
                fallback_hostname = actual or hostname
                fallback_reason = "best-speed-tested"
                fallback_score = score
            check_config = dict(config)
            sample = full_health_check(check_config)
            post_check = {
                "ok": sample.ok,
                "reason": sample.reason,
                "speed_mbps": sample.speed_mbps,
                "latency_ms": sample.latency_ms,
                "ran_speed_test": sample.ran_speed_test,
            }
            record_health_check(actual or hostname, sample, db_path=db_path)
            print(
                f"Emergency rescue check {hostname}->{actual or 'unknown'}: "
                f"{'ok' if sample.ok else 'bad'} {sample.reason}",
                flush=True,
            )
            if sample.ok:
                connected = {
                    "hostname": actual or hostname,
                    "requested_hostname": hostname,
                    "actual_hostname": actual,
                    "changed": True,
                    "exact_match": actual == hostname,
                    "accepted_fallback": bool(actual and actual != hostname),
                    "fallback_reason": "current-connected" if actual and actual != hostname else None,
                }
                return {
                    "candidate_limit": candidate_limit,
                    "candidate_count": len(candidates),
                    "tested_count": len(tested),
                    "tested_hostnames": tested,
                    "attempted_ready": attempted_ready,
                    "fast_rank": fast_data,
                    "connected": connected,
                    "post_check": post_check,
                    "exhausted": False,
                }

    if fallback_hostname:
        try:
            actual = guard.current_relay_hostname()
        except Exception as exc:
            print(f"Emergency rescue could not read current relay before fallback restore: {exc}", flush=True)
            actual = None
        if actual != fallback_hostname:
            try:
                print(
                    f"Emergency rescue exhausted; restoring fallback {fallback_hostname} ({fallback_reason}).",
                    flush=True,
                )
                restore_config = dict(config)
                restore_config["quick_connect"] = bool(restore_config.get("quick_connect", True))
                restore_config["strict_hostname"] = False
                restore_config["accept_any_connected_fallback"] = True
                fallback_connected = connect_relay(fallback_hostname, restore_config)
            except Exception as exc:
                restore_error = str(exc)
                print(f"Emergency rescue fallback restore failed: {exc}", flush=True)
        else:
            fallback_connected = {
                "hostname": fallback_hostname,
                "requested_hostname": fallback_hostname,
                "actual_hostname": actual,
                "changed": False,
                "exact_match": True,
                "accepted_fallback": False,
                "fallback_reason": fallback_reason,
            }

    return {
        "candidate_limit": candidate_limit,
        "candidate_count": len(candidates),
        "tested_count": len(tested),
        "tested_hostnames": tested,
        "attempted_ready": attempted_ready,
        "fast_rank": fast_data,
        "connected": connected,
        "fallback_connected": fallback_connected,
        "fallback_restore_error": restore_error,
        "post_check": post_check,
        "exhausted": True,
    }


def verify_candidate_pool(
    config: Dict[str, Any],
    pool_size: int = DEFAULT_POOL_SIZE,
    ready_target: int = DEFAULT_READY_TARGET,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    min_working_mbps: float = MIN_WORKING_MBPS,
    abandon_after: int = ABANDON_AFTER,
    fast_workers: int = DEFAULT_FAST_WORKERS,
    fast_timeout: float = DEFAULT_FAST_TIMEOUT,
    fast_port: int = DEFAULT_FAST_PORT,
    connect_best: bool = False,
    restore: bool = True,
    keep_current_if_no_better: bool = False,
    better_min_delta_mbps: float = 0.5,
    better_min_ratio: float = 1.15,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """True-test fast candidates and keep testing backups until enough pass."""
    pool_size = max(1, int(pool_size))
    ready_target = max(1, min(int(ready_target), pool_size))
    candidate_limit = max(pool_size, int(candidate_limit))
    min_working_mbps = float(min_working_mbps)
    better_min_delta_mbps = max(0.0, float(better_min_delta_mbps))
    better_min_ratio = max(1.0, float(better_min_ratio))

    sync_relays(update=False, db_path=db_path)
    current_hostname: Optional[str] = None
    current_baseline: Optional[Dict[str, Any]] = None
    if keep_current_if_no_better:
        try:
            current_hostname = guard.current_relay_hostname()
            current_state, _ = guard.mullvad_status(timeout=5)
        except Exception as exc:
            current_baseline = {"ok": False, "reason": f"could not read current relay: {exc}"}
            current_state = "Unknown"
        if current_hostname and current_state.lower().startswith("connected"):
            try:
                sample = full_health_check(dict(config))
                record_health_check(current_hostname, sample, db_path=db_path)
                current_baseline = {
                    "hostname": current_hostname,
                    "ok": sample.ok,
                    "reason": sample.reason,
                    "speed_mbps": sample.speed_mbps,
                    "latency_ms": sample.latency_ms,
                    "ran_speed_test": sample.ran_speed_test,
                }
                print(
                    f"Current relay baseline {current_hostname}: "
                    f"{'ok' if sample.ok else 'bad'} {sample.reason}",
                    flush=True,
                )
            except Exception as exc:
                current_baseline = {
                    "hostname": current_hostname,
                    "ok": False,
                    "reason": f"current baseline failed: {exc}",
                }

    fast_ranked = False
    candidates = reachable_fast_hostnames(candidate_limit, db_path=db_path)
    if len(candidates) < pool_size:
        fast_rank_all(
            workers=fast_workers,
            timeout=fast_timeout,
            port=fast_port,
            db_path=db_path,
        )
        fast_ranked = True
        candidates = reachable_fast_hostnames(candidate_limit, db_path=db_path)

    tested: List[str] = []
    best: Optional[guard.TestResult] = None

    def ready_rows() -> List[Dict[str, Any]]:
        return verified_candidate_relays(
            limit=pool_size,
            min_mbps=min_working_mbps,
            hostnames=tested or candidates,
            db_path=db_path,
        )

    remaining = list(candidates)
    while remaining and (not tested or len(ready_rows()) < ready_target):
        batch_size = pool_size if not tested else max(1, pool_size - len(ready_rows()))
        batch = remaining[:batch_size]
        remaining = remaining[batch_size:]
        tested.extend(batch)
        result = scan_inventory(
            config,
            scope="fast",
            limit=0,
            include_abandoned=True,
            restore=restore,
            connect_best=False,
            dry_run=False,
            min_working_mbps=min_working_mbps,
            abandon_after=abandon_after,
            db_path=db_path,
            hostnames=batch,
            record_full_health=True,
        )
        if result and (best is None or result.score > best.score):
            best = result

    ready = ready_rows()
    connected: Optional[Dict[str, Any]] = None
    skipped_connect: Optional[Dict[str, Any]] = None
    if connect_best and ready:
        chosen = ready[0]
        chosen_speed = (
            optional_float(chosen.get("last_health_mbps"))
            or optional_float(chosen.get("last_mbps"))
            or optional_float(chosen.get("best_mbps"))
        )
        current_speed = optional_float((current_baseline or {}).get("speed_mbps"))
        if (
            keep_current_if_no_better
            and current_hostname
            and current_speed is not None
            and chosen_speed is not None
        ):
            decision = speed_beats_baseline(
                chosen_speed,
                current_speed,
                min_working_mbps=min_working_mbps,
                better_min_delta_mbps=better_min_delta_mbps,
                better_min_ratio=better_min_ratio,
            )
            if not decision["ok"]:
                skipped_connect = {
                    "reason": "no_better_candidate",
                    "kept_hostname": current_hostname,
                    "candidate_hostname": chosen.get("hostname"),
                    "current_speed_mbps": decision.get("baseline_speed_mbps"),
                    "candidate_speed_mbps": decision.get("candidate_speed_mbps"),
                    "required_speed_mbps": decision.get("required_speed_mbps"),
                    "better_min_delta_mbps": better_min_delta_mbps,
                    "better_min_ratio": better_min_ratio,
                }
                print(
                    f"Keeping current relay {current_hostname}: best candidate {chosen.get('hostname')} "
                    f"{chosen_speed:.2f} Mbps did not beat current baseline {current_speed:.2f} Mbps "
                    f"(required {decision.get('required_speed_mbps')} Mbps).",
                    flush=True,
                )
            else:
                connected = connect_relay(str(chosen["hostname"]), config)
        else:
            connected = connect_relay(str(chosen["hostname"]), config)

    return {
        "pool_size": pool_size,
        "ready_target": ready_target,
        "candidate_limit": candidate_limit,
        "min_working_mbps": min_working_mbps,
        "fast_ranked": fast_ranked,
        "candidate_count": len(candidates),
        "tested_count": len(tested),
        "tested_hostnames": tested,
        "ready_count": len(ready),
        "ready_relays": ready,
        "best": best.to_json() if best else None,
        "connected": connected,
        "current_baseline": current_baseline,
        "skipped_connect": skipped_connect,
    }


def backup_relays_for_current_pool(
    pool_size: int = DEFAULT_POOL_SIZE,
    min_mbps: float = MIN_WORKING_MBPS,
    preferred_mbps: float = 8.0,
    db_path: Path = DB_PATH,
) -> List[Dict[str, Any]]:
    whitelist = whitelist_relays(
        limit=pool_size,
        min_mbps=min_mbps,
        preferred_mbps=preferred_mbps,
        db_path=db_path,
    )
    if whitelist:
        return whitelist
    fast_hosts = reachable_fast_hostnames(pool_size, db_path=db_path)
    rows = verified_candidate_relays(
        limit=pool_size,
        min_mbps=min_mbps,
        hostnames=fast_hosts,
        db_path=db_path,
    )
    if rows:
        return rows
    return verified_candidate_relays(limit=pool_size, min_mbps=min_mbps, db_path=db_path)


def idle_refresh_candidates(
    limit: int = 1,
    current_hostname: Optional[str] = None,
    include_abandoned: bool = False,
    db_path: Path = DB_PATH,
) -> List[str]:
    """Choose low-risk relays to true-test while the computer appears idle."""
    sync_relays(update=False, db_path=db_path)
    limit = max(1, int(limit))
    where = "status != 'retired'"
    params: List[Any] = []
    if not include_abandoned:
        where += " AND status != 'abandoned'"
    if current_hostname:
        where += " AND hostname != ?"
        params.append(current_hostname.strip().lower())
    blocked_sql, blocked_params = blocked_country_sql()
    where += blocked_sql
    params.extend(blocked_params)

    sql = f"""
        SELECT hostname
        FROM relays
        WHERE {where}
        ORDER BY
            CASE
                WHEN last_test_at IS NULL THEN 0
                WHEN status='working' THEN 3
                ELSE 1
            END,
            fast_reachable DESC,
            CASE WHEN fast_latency_ms IS NULL THEN 1 ELSE 0 END,
            fast_latency_ms ASC,
            consecutive_failures ASC,
            last_test_at ASC,
            hostname
        LIMIT ?
    """
    params.append(limit)
    with connect_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [str(row["hostname"]) for row in rows]


def relay_row(hostname: str, db_path: Path = DB_PATH) -> Optional[sqlite3.Row]:
    with connect_db(db_path) as conn:
        return conn.execute("SELECT * FROM relays WHERE hostname=?", (hostname,)).fetchone()


def same_location(hostname_a: str, hostname_b: str, db_path: Path = DB_PATH) -> bool:
    if not hostname_a or not hostname_b:
        return False
    row_a = relay_row(hostname_a, db_path=db_path)
    row_b = relay_row(hostname_b, db_path=db_path)
    if not row_a or not row_b:
        return False
    return row_a["country"] == row_b["country"] and row_a["city"] == row_b["city"]


class TrafficIdleMonitor:
    """Track VPN tunnel byte counters and decide when maintenance can run."""

    def __init__(self, idle_after_seconds: int, activity_threshold_bytes: int) -> None:
        self.idle_after_seconds = max(0, int(idle_after_seconds))
        self.activity_threshold_bytes = max(0, int(activity_threshold_bytes))
        self.idle_since: Optional[float] = None
        self.last: Optional[Dict[str, Any]] = None

    def _current(self) -> Optional[Dict[str, Any]]:
        try:
            _, status_text = guard.mullvad_status(timeout=5)
            stats = guard.vpn_interface_traffic(status_text)
        except Exception:
            return None
        if not stats.get("ok"):
            return None
        return {
            "time": time.monotonic(),
            "interface": stats["interface"],
            "download_bytes": int(stats["download_bytes"]),
            "upload_bytes": int(stats["upload_bytes"]),
        }

    def observe(self) -> Dict[str, Any]:
        current = self._current()
        now_mono = time.monotonic()
        if current is None:
            return {"ok": False, "idle": False, "reason": "traffic counters unavailable", "idle_for": 0.0}
        previous = self.last
        self.last = current
        if previous is None or previous.get("interface") != current["interface"]:
            self.idle_since = now_mono
            return {"ok": True, "idle": False, "reason": "traffic baseline initialized", "idle_for": 0.0}
        down_delta = current["download_bytes"] - int(previous["download_bytes"])
        up_delta = current["upload_bytes"] - int(previous["upload_bytes"])
        if down_delta < 0 or up_delta < 0:
            self.idle_since = now_mono
            return {"ok": True, "idle": False, "reason": "traffic counters reset", "idle_for": 0.0}
        active_bytes = down_delta + up_delta
        if active_bytes > self.activity_threshold_bytes:
            self.idle_since = now_mono
            return {
                "ok": True,
                "idle": False,
                "reason": f"traffic active {active_bytes} bytes",
                "idle_for": 0.0,
                "active_bytes": active_bytes,
            }
        if self.idle_since is None:
            self.idle_since = now_mono
        idle_for = now_mono - self.idle_since
        return {
            "ok": True,
            "idle": idle_for >= self.idle_after_seconds,
            "reason": f"traffic idle for {int(idle_for)}s",
            "idle_for": idle_for,
            "active_bytes": active_bytes,
        }

    def reset_baseline(self, preserve_idle: bool = True) -> None:
        current = self._current()
        now_mono = time.monotonic()
        if current is not None:
            self.last = current
        if not preserve_idle:
            self.idle_since = now_mono
        elif self.idle_since is None:
            self.idle_since = now_mono


def user_idle_ready(required_seconds: int) -> tuple[bool, Optional[float]]:
    required_seconds = int(required_seconds)
    if required_seconds <= 0:
        return True, None
    idle = user_idle_seconds()
    if idle is None:
        return True, None
    return idle >= required_seconds, idle


def maintenance_idle_ready(
    traffic_state: Dict[str, Any],
    user_ok: bool,
    user_idle: Optional[float],
) -> tuple[bool, str]:
    if traffic_state.get("idle"):
        return True, str(traffic_state.get("reason", "traffic idle"))
    reason = str(traffic_state.get("reason", "traffic active"))
    if not traffic_state.get("ok") and user_ok:
        if user_idle is None:
            return True, f"{reason}; using HID idle fallback"
        return True, f"{reason}; HID idle {user_idle:.1f}s"
    return False, reason


def auto_guard_control_lock() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(AUTO_GUARD_CONTROL_LOCK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)
        return None
    pid = payload.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
        except (ProcessLookupError, ValueError):
            AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)
            return None
        except PermissionError:
            pass
    expires_at = float(payload.get("expires_at_epoch") or 0)
    if expires_at and time.time() > expires_at:
        AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)
        return None
    return payload


def functional_outage(sample: guard.HealthSample, config: Dict[str, Any]) -> bool:
    reason = sample.reason.lower()
    if not reason.startswith("url probe failed"):
        return False
    configured_urls = [str(url).strip() for url in config.get("url_checks", []) if str(url).strip()]
    if not configured_urls:
        return False
    _, _, failed_text = sample.reason.partition(":")
    failed_urls = [part.strip() for part in failed_text.split(",") if part.strip()]
    failed_url_count = len(failed_urls)
    configured_count = len(configured_urls)
    configured_floor = int(config.get("url_emergency_min_failed", 0) or 0)
    if configured_floor <= 0:
        configured_floor = configured_count
    failure_floor = max(2, min(configured_floor, configured_count))
    return failed_url_count >= failure_floor


def maintenance_activity_gate(
    monitor: TrafficIdleMonitor,
    user_idle_seconds_required: int,
) -> tuple[bool, str, Dict[str, Any], Optional[float]]:
    control_lock = auto_guard_control_lock()
    if control_lock:
        reason = str(control_lock.get("reason", "manual maintenance"))
        return False, f"control lock active: {reason}", {"ok": True, "idle": False}, None
    traffic_state = monitor.observe()
    user_ok, user_idle = user_idle_ready(user_idle_seconds_required)
    idle_ready, idle_reason = maintenance_idle_ready(traffic_state, user_ok, user_idle)
    if not idle_ready:
        return False, idle_reason, traffic_state, user_idle
    if not user_ok:
        return False, f"user activity detected; HID idle {user_idle:.1f}s", traffic_state, user_idle
    return True, idle_reason, traffic_state, user_idle


def nightly_full_scan_relays(db_path: Path = DB_PATH) -> List[guard.Relay]:
    sync_relays(update=False, db_path=db_path)
    blocked_sql, blocked_params = blocked_country_sql()
    with connect_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM relays
            WHERE status != 'retired'{blocked_sql}
            ORDER BY
                CASE WHEN fast_probe_at IS NULL THEN 1 ELSE 0 END,
                fast_reachable DESC,
                CASE WHEN fast_score IS NULL THEN -1000000.0 ELSE fast_score END DESC,
                CASE
                    WHEN status='working' THEN 0
                    WHEN status='unknown' THEN 1
                    WHEN status='no_speed' THEN 2
                    WHEN status='abandoned' THEN 3
                    ELSE 4
                END,
                CASE WHEN last_test_at IS NULL THEN 0 ELSE 1 END,
                last_test_at ASC,
                hostname
            """,
            blocked_params,
        ).fetchall()
        return [relay_from_row(row) for row in rows]


def restore_after_maintenance(
    previous_relay: Optional[str],
    previous_connected: bool,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if previous_relay:
        if relay_hostname_blocked(previous_relay, config=config):
            guard.restore_previous(previous_relay, previous_connected, config)
            return {
                "action": "blocked_previous_not_restored",
                "hostname": previous_relay,
                "previous_connected": previous_connected,
            }
        guard.restore_previous(previous_relay, previous_connected, config)
        return {
            "action": "restored_previous",
            "hostname": previous_relay,
            "previous_connected": previous_connected,
        }
    if not previous_connected:
        quick = bool(config.get("quick_connect", False))
        timeout = int(config.get("disconnect_wait_timeout_seconds", 5 if quick else 20))
        if quick:
            guard.disconnect_fast(timeout=timeout)
        else:
            guard.disconnect(timeout=timeout)
        return {"action": "disconnected", "previous_connected": False}
    return {"action": "left_current", "reason": "previous relay unknown"}


def nightly_full_scan(
    config: Dict[str, Any],
    monitor: TrafficIdleMonitor,
    min_working_mbps: float,
    abandon_after: int,
    user_idle_seconds_required: int,
    max_duration_seconds: int = DEFAULT_NIGHTLY_FULL_SCAN_MAX_SECONDS,
    better_min_delta_mbps: float = DEFAULT_BETTER_MIN_DELTA_MBPS,
    better_min_ratio: float = DEFAULT_BETTER_MIN_RATIO,
    db_path: Path = DB_PATH,
    enforce_activity_gate: bool = True,
) -> Dict[str, Any]:
    """True-test every non-retired relay during a quiet maintenance window.

    When enforce_activity_gate is False (the user-scheduled 04:00 run) the scan
    ignores user/traffic activity and keeps going until every node is tested or
    the time limit is hit — "test all nodes by default unless turned off"."""
    relays = nightly_full_scan_relays(db_path=db_path)
    if not relays:
        return {"started": False, "reason": "no nightly scan candidates", "tested": []}

    if enforce_activity_gate:
        gate_ok, gate_reason, _, _ = maintenance_activity_gate(monitor, user_idle_seconds_required)
        if not gate_ok:
            return {"started": False, "reason": gate_reason, "tested": []}

    started_at_mono = time.monotonic()
    started_at = now()
    max_duration_seconds = max(0, int(max_duration_seconds))
    deadline = started_at_mono + max_duration_seconds if max_duration_seconds > 0 else None
    with connect_db(db_path) as conn:
        set_meta_value(conn, META_LAST_NIGHTLY_FULL_SCAN_AT, started_at)
        set_meta_value(
            conn,
            META_LAST_NIGHTLY_FULL_SCAN_RESULT,
            json.dumps({"status": "running", "started_at": started_at, "total": len(relays)}, ensure_ascii=False),
        )
        conn.commit()

    previous_relay: Optional[str] = None
    previous_connected = False
    current_baseline: Dict[str, Any] = {"ok": False, "reason": "not measured"}
    try:
        previous_relay = guard.current_relay_hostname()
        previous_state, _ = guard.mullvad_status(timeout=5)
        previous_connected = previous_state.lower().startswith("connected")
    except Exception as exc:
        current_baseline = {"ok": False, "reason": f"could not read current relay: {exc}"}

    if previous_relay and previous_connected:
        try:
            sample = full_health_check(dict(config))
            record_health_check(previous_relay, sample, db_path=db_path)
            current_baseline = {
                "hostname": previous_relay,
                "ok": sample.ok,
                "reason": sample.reason,
                "speed_mbps": sample.speed_mbps,
                "latency_ms": sample.latency_ms,
                "ran_speed_test": sample.ran_speed_test,
            }
            print(
                f"Nightly baseline {previous_relay}: {'ok' if sample.ok else 'bad'} {sample.reason}",
                flush=True,
            )
        except Exception as exc:
            current_baseline = {
                "hostname": previous_relay,
                "ok": False,
                "reason": f"current baseline failed: {exc}",
            }
    monitor.reset_baseline(preserve_idle=True)

    tested: List[str] = []
    best: Optional[guard.TestResult] = None
    stopped_reason: Optional[str] = None
    print(f"Nightly full scan starting total={len(relays)}.", flush=True)
    for index, relay in enumerate(relays, start=1):
        if deadline is not None and time.monotonic() >= deadline:
            stopped_reason = f"time limit reached after {max_duration_seconds}s"
            print(f"Nightly full scan stopping before {relay.hostname}: {stopped_reason}", flush=True)
            break
        if enforce_activity_gate:
            gate_ok, gate_reason, _, _ = maintenance_activity_gate(monitor, user_idle_seconds_required)
            if not gate_ok:
                stopped_reason = gate_reason
                print(f"Nightly full scan stopping before {relay.hostname}: {gate_reason}", flush=True)
                break
        else:
            gate_reason = "scheduled run (activity gate bypassed)"

        print(f"[{index}/{len(relays)}] Nightly true-testing {relay.hostname}. {gate_reason}", flush=True)
        result = guard.test_relay(relay, config)
        tested.append(relay.hostname)
        record_result(result, min_working_mbps=min_working_mbps, abandon_after=abandon_after, db_path=db_path)
        print_scan_line(result)
        if (
            result.connected
            and result.download_mbps is not None
            and float(result.download_mbps) >= float(min_working_mbps)
            and (best is None or result.score > best.score)
        ):
            best = result
        monitor.reset_baseline(preserve_idle=True)
        time.sleep(2)

    completed = stopped_reason is None and len(tested) >= len(relays)
    current_speed = optional_float(current_baseline.get("speed_mbps")) if current_baseline.get("ok") else None
    best_speed = optional_float(best.download_mbps) if best else None
    decision = speed_beats_baseline(
        best_speed,
        current_speed,
        min_working_mbps=min_working_mbps,
        better_min_delta_mbps=better_min_delta_mbps,
        better_min_ratio=better_min_ratio,
    )
    connected: Optional[Dict[str, Any]] = None
    restored: Optional[Dict[str, Any]] = None
    decision["best_hostname"] = best.hostname if best else None
    decision["previous_hostname"] = previous_relay
    previous_blocked = relay_hostname_blocked(previous_relay, db_path=db_path, config=config)
    decision["previous_blocked"] = previous_blocked
    if best and previous_blocked and not decision.get("ok"):
        decision["ok"] = True
        decision["reason"] = "previous relay is blocked by country policy; switching to best tested non-blocked relay"
    try:
        actual = guard.current_relay_hostname()
    except Exception:
        actual = None

    if best and decision.get("ok"):
        if actual == best.hostname:
            connected = {
                "hostname": best.hostname,
                "requested_hostname": best.hostname,
                "actual_hostname": actual,
                "changed": False,
                "exact_match": True,
                "accepted_fallback": False,
                "reason": "best already connected",
            }
            decision["action"] = "kept_best_already_connected"
        else:
            print(
                f"Nightly full scan connecting best {best.hostname} "
                f"{best.download_mbps:.2f} Mbps. Decision: {decision.get('reason')}",
                flush=True,
            )
            try:
                connected = connect_relay(best.hostname, config)
                decision["action"] = "connected_best"
            except Exception as exc:
                decision["action"] = "connect_best_failed_restore_previous"
                decision["connect_error"] = str(exc)
                print(f"Nightly best connect failed: {exc}; restoring previous relay.", flush=True)
                try:
                    restored = restore_after_maintenance(previous_relay, previous_connected, config)
                except Exception as restore_exc:
                    restored = {"action": "restore_failed", "error": str(restore_exc)}
                    print(f"Nightly restore after connect failure also failed: {restore_exc}", flush=True)
    else:
        print(
            f"Nightly full scan restoring previous relay. "
            f"Best={best.hostname if best else 'none'} decision={decision.get('reason')}",
            flush=True,
        )
        try:
            restored = restore_after_maintenance(previous_relay, previous_connected, config)
            decision["action"] = restored.get("action")
        except Exception as exc:
            restored = {"action": "restore_failed", "error": str(exc)}
            decision["action"] = "restore_failed"
            decision["restore_error"] = str(exc)
            print(f"Nightly restore failed: {exc}", flush=True)

    result_payload = {
        "started": True,
        "started_at": started_at,
        "finished_at": now(),
        "completed": completed,
        "stopped_reason": stopped_reason,
        "total": len(relays),
        "tested_count": len(tested),
        "tested": tested,
        "best": best.to_json() if best else None,
        "current_baseline": current_baseline,
        "decision": decision,
        "connected": connected,
        "restored": restored,
    }
    with connect_db(db_path) as conn:
        set_meta_value(
            conn,
            META_LAST_NIGHTLY_FULL_SCAN_RESULT,
            json.dumps(
                {
                    "status": "completed" if completed else "stopped",
                    "finished_at": result_payload["finished_at"],
                    "tested_count": len(tested),
                    "total": len(relays),
                    "best_hostname": best.hostname if best else None,
                    "decision": decision,
                    "stopped_reason": stopped_reason,
                },
                ensure_ascii=False,
            ),
        )
        conn.commit()
    return result_payload


def idle_refresh_batch(
    config: Dict[str, Any],
    monitor: TrafficIdleMonitor,
    batch_size: int,
    min_working_mbps: float,
    abandon_after: int,
    user_idle_seconds_required: int,
) -> Dict[str, Any]:
    """True-test a tiny candidate batch, stopping between relays if activity returns."""
    tested: List[str] = []
    current = guard.current_relay_hostname()
    candidates = idle_refresh_candidates(
        limit=max(1, int(batch_size)),
        current_hostname=current,
    )
    if not candidates:
        return {"started": False, "reason": "no idle refresh candidates", "tested": tested}

    print(f"Idle refresh starting candidates={','.join(candidates)}", flush=True)
    for hostname in candidates:
        traffic_state = monitor.observe()
        user_ok, user_idle = user_idle_ready(user_idle_seconds_required)
        idle_ready, idle_reason = maintenance_idle_ready(traffic_state, user_ok, user_idle)
        if not idle_ready:
            print(f"Idle refresh stopped before {hostname}: {idle_reason}", flush=True)
            return {"started": True, "reason": idle_reason, "tested": tested}
        if not user_ok:
            reason = f"user activity detected; HID idle {user_idle:.1f}s"
            print(f"Idle refresh stopped before {hostname}: {reason}", flush=True)
            return {"started": True, "reason": reason, "tested": tested}

        print(f"Idle refresh true-testing {hostname}. {idle_reason}", flush=True)
        try:
            scan_inventory(
                config,
                scope="fast",
                limit=0,
                include_abandoned=False,
                restore=True,
                connect_best=False,
                dry_run=False,
                min_working_mbps=min_working_mbps,
                abandon_after=abandon_after,
                hostnames=[hostname],
                record_full_health=True,
            )
        except Exception as exc:
            tested.append(hostname)
            monitor.reset_baseline(preserve_idle=True)
            print(f"Idle refresh failed for {hostname}: {exc}", flush=True)
            return {"started": True, "reason": f"failed: {exc}", "tested": tested}
        tested.append(hostname)
        monitor.reset_baseline(preserve_idle=True)
        time.sleep(2)

    return {"started": True, "reason": "batch complete", "tested": tested}


def auto_guard(
    config: Dict[str, Any],
    interval: int = 60,
    pool_size: int = DEFAULT_POOL_SIZE,
    ready_target: int = DEFAULT_READY_TARGET,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    min_working_mbps: float = MIN_WORKING_MBPS,
    abandon_after: int = ABANDON_AFTER,
    fast_rank_interval: int = 3600,
    fast_workers: int = DEFAULT_FAST_WORKERS,
    fast_timeout: float = DEFAULT_FAST_TIMEOUT,
    fast_port: int = DEFAULT_FAST_PORT,
    failure_threshold: int = 3,
    backup_attempt_limit: int = 3,
    recovery_search_limit: int = 50,
    emergency_attempt_limit: int = 12,
    backup_retry_cooldown: int = 900,
    refresh_pool_on_failure: bool = False,
    refresh_pool_cooldown: int = 1800,
    preferred_mbps: float = 8.0,
    idle_refresh_enabled: bool = True,
    idle_refresh_after_seconds: int = 1800,
    idle_refresh_user_idle_seconds: int = 1800,
    idle_refresh_activity_threshold_bytes: int = 262144,
    idle_refresh_batch_size: int = 1,
    nightly_full_scan_enabled: bool = True,
    nightly_full_scan_cooldown_seconds: int = DEFAULT_NIGHTLY_FULL_SCAN_COOLDOWN_SECONDS,
    nightly_full_scan_max_seconds: int = DEFAULT_NIGHTLY_FULL_SCAN_MAX_SECONDS,
    nightly_full_scan_better_min_delta_mbps: float = DEFAULT_BETTER_MIN_DELTA_MBPS,
    nightly_full_scan_better_min_ratio: float = DEFAULT_BETTER_MIN_RATIO,
    connecting_grace_seconds: int = 45,
    daemon_retry_cooldown: int = 180,
) -> int:
    """Closed-loop guard: prefer verified backups; refresh candidates only when needed."""
    interval = max(10, int(interval))
    pool_size = max(1, int(pool_size))
    ready_target = max(1, min(int(ready_target), pool_size))
    candidate_limit = max(pool_size, int(candidate_limit))
    min_working_mbps = float(min_working_mbps)
    preferred_mbps = float(preferred_mbps)
    failure_threshold = max(1, int(failure_threshold))
    backup_attempt_limit = max(1, int(backup_attempt_limit))
    recovery_search_limit = max(pool_size, int(recovery_search_limit))
    emergency_attempt_limit = max(backup_attempt_limit, int(emergency_attempt_limit))
    backup_retry_cooldown = max(0, int(backup_retry_cooldown))
    refresh_pool_cooldown = max(0, int(refresh_pool_cooldown))
    idle_refresh_after_seconds = max(0, int(idle_refresh_after_seconds))
    idle_refresh_user_idle_seconds = max(0, int(idle_refresh_user_idle_seconds))
    idle_refresh_activity_threshold_bytes = max(0, int(idle_refresh_activity_threshold_bytes))
    idle_refresh_batch_size = max(1, int(idle_refresh_batch_size))
    nightly_full_scan_cooldown_seconds = max(0, int(nightly_full_scan_cooldown_seconds))
    nightly_full_scan_max_seconds = max(0, int(nightly_full_scan_max_seconds))
    nightly_full_scan_better_min_delta_mbps = max(0.0, float(nightly_full_scan_better_min_delta_mbps))
    nightly_full_scan_better_min_ratio = max(1.0, float(nightly_full_scan_better_min_ratio))
    connecting_grace_seconds = max(0, int(connecting_grace_seconds))
    daemon_retry_cooldown = max(0, int(daemon_retry_cooldown))

    guard_config = dict(config)
    guard_config["health_mode"] = str(guard_config.get("health_mode", "adaptive")).lower()
    guard_config["min_mbps"] = float(guard_config.get("min_mbps", min_working_mbps) or min_working_mbps)
    guard_config["speed_check_every_seconds"] = int(guard_config.get("speed_check_every_seconds", 600) or 600)
    guard_config["quick_connect"] = bool(guard_config.get("quick_connect", True))
    guard_config["strict_hostname"] = bool(guard_config.get("strict_hostname", False))
    guard_config["accept_any_connected_fallback"] = True

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_GUARD_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    anti_censorship_mode = guard_config.get("anti_censorship_mode", "")
    anti_censorship_check_seconds = max(0, int(guard_config.get("anti_censorship_check_seconds", 600) or 0))

    scheduled_full_scan_enabled = bool(guard_config.get("scheduled_full_scan_enabled", True))
    scheduled_full_scan_hour = max(0, min(23, int(guard_config.get("scheduled_full_scan_hour", 4))))
    scheduled_full_scan_minute = max(0, min(59, int(guard_config.get("scheduled_full_scan_minute", 0))))
    scheduled_full_scan_window_minutes = max(1, int(guard_config.get("scheduled_full_scan_window_minutes", 180)))
    try:
        changed = guard.ensure_anti_censorship_mode(anti_censorship_mode)
        if changed:
            print(f"Anti-censorship: {changed}", flush=True)
        elif anti_censorship_mode:
            print(f"Anti-censorship: mode already {anti_censorship_mode}", flush=True)
    except Exception as exc:
        print(f"Anti-censorship enforcement failed at startup: {exc}", flush=True)

    last_speed_check_at = time.monotonic() - guard_config["speed_check_every_seconds"]
    last_anti_censorship_check_at = time.monotonic()
    last_fast_rank_at = 0.0
    last_fast_rank_defer_log_at = 0.0
    last_idle_refresh_at = 0.0
    last_idle_refresh_defer_log_at = 0.0
    last_pool_refresh_at = 0.0
    last_emergency_pool_refresh_at = 0.0
    last_control_lock_log_at = 0.0
    last_daemon_nudge_at = 0.0
    connecting_first_seen_at: Optional[float] = None
    daemon_first_seen_at: Optional[float] = None
    consecutive_failures = 0
    recent_backup_failures: Dict[str, float] = {}
    idle_monitor = (
        TrafficIdleMonitor(
            idle_after_seconds=idle_refresh_after_seconds,
            activity_threshold_bytes=idle_refresh_activity_threshold_bytes,
        )
        if idle_refresh_enabled
        else None
    )
    print(
        f"Auto guard started interval={interval}s pool_size={pool_size} ready_target={ready_target} "
        f"min_working_mbps={min_working_mbps} failure_threshold={failure_threshold} "
        f"preferred_mbps={preferred_mbps} "
        f"backup_attempt_limit={backup_attempt_limit} backup_retry_cooldown={backup_retry_cooldown}s "
        f"recovery_search_limit={recovery_search_limit} emergency_attempt_limit={emergency_attempt_limit} "
        f"refresh_pool_on_failure={refresh_pool_on_failure} "
        f"refresh_pool_cooldown={refresh_pool_cooldown}s "
        f"idle_refresh_enabled={idle_refresh_enabled} idle_refresh_after={idle_refresh_after_seconds}s "
        f"idle_refresh_batch_size={idle_refresh_batch_size} "
        f"nightly_full_scan_enabled={nightly_full_scan_enabled} "
        f"nightly_full_scan_cooldown={nightly_full_scan_cooldown_seconds}s "
        f"nightly_full_scan_max={nightly_full_scan_max_seconds}s "
        f"connecting_grace={connecting_grace_seconds}s daemon_retry_cooldown={daemon_retry_cooldown}s",
        flush=True,
    )

    while True:
        now_mono = time.monotonic()
        control_lock = auto_guard_control_lock()
        if control_lock:
            if now_mono - last_control_lock_log_at >= 60:
                reason = control_lock.get("reason", "manual maintenance")
                print(f"Auto guard alive; action paused by control lock: {reason}", flush=True)
                last_control_lock_log_at = now_mono
            time.sleep(interval)
            continue

        if (
            anti_censorship_mode
            and anti_censorship_check_seconds > 0
            and now_mono - last_anti_censorship_check_at >= anti_censorship_check_seconds
        ):
            try:
                changed = guard.ensure_anti_censorship_mode(anti_censorship_mode)
                if changed:
                    print(f"Anti-censorship re-asserted: {changed}", flush=True)
            except Exception as exc:
                print(f"Anti-censorship re-assert failed: {exc}", flush=True)
            last_anti_censorship_check_at = time.monotonic()

        if scheduled_full_scan_enabled:
            local = time.localtime()
            today = time.strftime("%Y-%m-%d", local)
            minutes_now = local.tm_hour * 60 + local.tm_min
            minutes_start = scheduled_full_scan_hour * 60 + scheduled_full_scan_minute
            in_window = 0 <= (minutes_now - minutes_start) <= scheduled_full_scan_window_minutes
            try:
                with connect_db() as conn:
                    last_scan_date = meta_value(conn, META_LAST_SCHEDULED_FULL_SCAN_DATE)
            except Exception:
                last_scan_date = None
            if in_window and last_scan_date != today:
                if scheduled_full_scan_disabled():
                    print(
                        f"Scheduled full scan window open for {today} but disabled via float widget/flag; skipping today.",
                        flush=True,
                    )
                    try:
                        with connect_db() as conn:
                            set_meta_value(conn, META_LAST_SCHEDULED_FULL_SCAN_DATE, today)
                    except Exception:
                        pass
                else:
                    print(
                        f"Scheduled full scan starting for {today} "
                        f"({scheduled_full_scan_hour:02d}:{scheduled_full_scan_minute:02d}); testing all nodes.",
                        flush=True,
                    )
                    scan_monitor = idle_monitor or TrafficIdleMonitor(
                        idle_after_seconds=0,
                        activity_threshold_bytes=idle_refresh_activity_threshold_bytes,
                    )
                    try:
                        data = nightly_full_scan(
                            guard_config,
                            scan_monitor,
                            min_working_mbps=min_working_mbps,
                            abandon_after=abandon_after,
                            user_idle_seconds_required=0,
                            max_duration_seconds=nightly_full_scan_max_seconds,
                            better_min_delta_mbps=nightly_full_scan_better_min_delta_mbps,
                            better_min_ratio=nightly_full_scan_better_min_ratio,
                            enforce_activity_gate=False,
                        )
                        best = data.get("best") if isinstance(data.get("best"), dict) else None
                        decision = data.get("decision") if isinstance(data.get("decision"), dict) else None
                        print(
                            f"Scheduled full scan finished: completed={data.get('completed')} "
                            f"tested={data.get('tested_count')}/{data.get('total')} "
                            f"best={best.get('hostname') if best else None} "
                            f"decision={decision.get('action') if decision else None} "
                            f"stopped={data.get('stopped_reason')}",
                            flush=True,
                        )
                    except Exception as exc:
                        print(f"Scheduled full scan failed: {exc}", flush=True)
                    try:
                        with connect_db() as conn:
                            set_meta_value(conn, META_LAST_SCHEDULED_FULL_SCAN_DATE, today)
                    except Exception:
                        pass
                    last_speed_check_at = time.monotonic()
                    last_fast_rank_at = time.monotonic()
                    continue

        fast_rank_due = fast_rank_interval > 0 and now_mono - last_fast_rank_at >= fast_rank_interval
        if fast_rank_due and not idle_monitor:
            print("Refreshing fast candidate ranking without switching VPN.", flush=True)
            try:
                data = fast_rank_all(
                    workers=fast_workers,
                    timeout=fast_timeout,
                    port=fast_port,
                )
                print(
                    f"Fast rank complete tested={data['tested']} reachable={data['reachable']} "
                    f"elapsed={data['elapsed_seconds']}s",
                    flush=True,
                )
            except Exception as exc:
                print(f"Fast rank failed: {exc}", flush=True)
            last_fast_rank_at = time.monotonic()

        try:
            sample, last_speed_check_at = guard.health_check(guard_config, last_speed_check_at)
        except Exception as exc:
            sample = guard.HealthSample(False, f"health check exception: {exc}")
        probe = " speed-test" if sample.ran_speed_test else ""
        print(f"{guard.utc_now()} current={'ok' if sample.ok else 'bad'}{probe} {sample.reason}", flush=True)
        if sample.ok:
            connecting_first_seen_at = None
            daemon_first_seen_at = None
            if sample.ran_speed_test:
                try:
                    record_health_check(guard.current_relay_hostname(), sample)
                except Exception as exc:
                    print(f"Could not record current relay health: {exc}", flush=True)
            consecutive_failures = 0
            if idle_monitor:
                idle_state = idle_monitor.observe()
                user_ok, user_idle = user_idle_ready(idle_refresh_user_idle_seconds)
                idle_ready, idle_reason = maintenance_idle_ready(idle_state, user_ok, user_idle)
                if idle_ready and user_ok:
                    if fast_rank_due:
                        print(
                            f"Idle window open; refreshing fast candidate ranking. {idle_reason}",
                            flush=True,
                        )
                        try:
                            data = fast_rank_all(
                                workers=fast_workers,
                                timeout=fast_timeout,
                                port=fast_port,
                            )
                            print(
                                f"Fast rank complete tested={data['tested']} reachable={data['reachable']} "
                                f"elapsed={data['elapsed_seconds']}s",
                                flush=True,
                            )
                        except Exception as exc:
                            print(f"Fast rank failed: {exc}", flush=True)
                        last_fast_rank_at = time.monotonic()
                        idle_monitor.reset_baseline(preserve_idle=True)
                    nightly_due = (
                        nightly_full_scan_enabled
                        and nightly_full_scan_due(nightly_full_scan_cooldown_seconds).get("due")
                    )
                    if nightly_due:
                        data = nightly_full_scan(
                            guard_config,
                            idle_monitor,
                            min_working_mbps=min_working_mbps,
                            abandon_after=abandon_after,
                            user_idle_seconds_required=idle_refresh_user_idle_seconds,
                            max_duration_seconds=nightly_full_scan_max_seconds,
                            better_min_delta_mbps=nightly_full_scan_better_min_delta_mbps,
                            better_min_ratio=nightly_full_scan_better_min_ratio,
                        )
                        if data.get("started"):
                            last_idle_refresh_at = time.monotonic()
                            print(
                                f"Nightly full scan completed={data.get('completed')} "
                                f"tested={data.get('tested_count')}/{data.get('total')} "
                                f"best={(data.get('best') or {}).get('hostname') if isinstance(data.get('best'), dict) else None} "
                                f"decision={(data.get('decision') or {}).get('action') if isinstance(data.get('decision'), dict) else None} "
                                f"reason={data.get('stopped_reason')}",
                                flush=True,
                            )
                            idle_monitor.reset_baseline(preserve_idle=True)
                            time.sleep(interval)
                            continue
                        print(f"Nightly full scan deferred: {data.get('reason')}", flush=True)
                        time.sleep(interval)
                        continue
                    idle_refresh_due = (
                        idle_refresh_after_seconds <= 0
                        or last_idle_refresh_at <= 0
                        or now_mono - last_idle_refresh_at >= idle_refresh_after_seconds
                    )
                    if idle_refresh_due:
                        data = idle_refresh_batch(
                            guard_config,
                            idle_monitor,
                            batch_size=idle_refresh_batch_size,
                            min_working_mbps=min_working_mbps,
                            abandon_after=abandon_after,
                            user_idle_seconds_required=idle_refresh_user_idle_seconds,
                        )
                        if data.get("started"):
                            last_idle_refresh_at = time.monotonic()
                            print(
                                f"Idle refresh {data.get('reason')} tested={','.join(data.get('tested', []))}",
                                flush=True,
                            )
                            idle_monitor.reset_baseline(preserve_idle=True)
                    elif time.monotonic() - last_idle_refresh_defer_log_at >= 300:
                        remaining = int(idle_refresh_after_seconds - (now_mono - last_idle_refresh_at))
                        print(
                            f"Idle refresh deferred; next maintenance batch in {max(0, remaining)}s.",
                            flush=True,
                        )
                        last_idle_refresh_defer_log_at = time.monotonic()
                elif fast_rank_due and time.monotonic() - last_fast_rank_defer_log_at >= 300:
                    if not idle_ready:
                        print(f"Fast rank deferred until idle window: {idle_reason}", flush=True)
                    elif not user_ok:
                        print(f"Fast rank deferred; user idle {user_idle:.1f}s", flush=True)
                    last_fast_rank_defer_log_at = time.monotonic()
            time.sleep(interval)
            continue

        reason_lower = sample.reason.lower()
        if guard.mullvad_rpc_unavailable_text(sample.reason):
            now_mono = time.monotonic()
            if daemon_first_seen_at is None:
                daemon_first_seen_at = now_mono
            consecutive_failures = 0
            elapsed = int(now_mono - daemon_first_seen_at)
            if (
                not last_daemon_nudge_at
                or daemon_retry_cooldown <= 0
                or now_mono - last_daemon_nudge_at >= daemon_retry_cooldown
            ):
                error = guard.nudge_mullvad_app()
                last_daemon_nudge_at = now_mono
                if error:
                    print(
                        f"Mullvad management interface unavailable for {elapsed}s; "
                        f"not switching relays. App nudge failed: {error}",
                        flush=True,
                    )
                else:
                    print(
                        f"Mullvad management interface unavailable for {elapsed}s; "
                        "not switching relays. Nudged Mullvad VPN app and will wait.",
                        flush=True,
                    )
            else:
                print(
                    f"Mullvad management interface unavailable for {elapsed}s; "
                    "not switching relays until the local daemon responds.",
                    flush=True,
                )
            time.sleep(interval)
            continue

        daemon_first_seen_at = None
        if reason_lower.startswith("vpn state is connecting"):
            now_mono = time.monotonic()
            if connecting_first_seen_at is None:
                connecting_first_seen_at = now_mono
            elapsed = now_mono - connecting_first_seen_at
            if elapsed < connecting_grace_seconds:
                consecutive_failures = 0
                remaining = int(connecting_grace_seconds - elapsed)
                print(
                    f"VPN is still Connecting; waiting through startup grace "
                    f"({max(0, remaining)}s remaining) before trying recovery.",
                    flush=True,
                )
                time.sleep(interval)
                continue
            print(
                f"VPN has been Connecting for {int(elapsed)}s; startup grace elapsed, recovery allowed.",
                flush=True,
            )
        else:
            connecting_first_seen_at = None

        consecutive_failures += 1
        disconnected_failure = sample.reason.lower().startswith("vpn state")
        functional_failure = functional_outage(sample, guard_config)
        policy_failure = sample.reason.lower().startswith("blocked relay country")
        emergency_recovery = disconnected_failure or functional_failure or policy_failure
        active_threshold = 1 if emergency_recovery else failure_threshold
        if consecutive_failures < active_threshold:
            print(
                f"Bad sample {consecutive_failures}/{active_threshold}; waiting for confirmation before switching.",
                flush=True,
            )
            time.sleep(interval)
            continue

        try:
            current = guard.current_relay_hostname()
        except Exception as exc:
            print(f"Could not read current relay before recovery: {exc}", flush=True)
            current = None
        record_health_check(current, sample)
        if functional_failure:
            print("Functional outage detected; using emergency recovery search.", flush=True)
        backup_limit = recovery_search_limit if emergency_recovery else pool_size
        backups = backup_relays_for_current_pool(
            pool_size=backup_limit,
            min_mbps=min_working_mbps,
            preferred_mbps=preferred_mbps,
        )
        switched = False
        emergency_fallback_hostname: Optional[str] = None
        if backups:
            print(f"Trying {len(backups)} verified backup relay(s).", flush=True)
        attempted_backups = 0
        attempted_backup_hostnames: List[str] = []
        skipped_cooldown = 0
        attempt_limit = min(emergency_attempt_limit, backup_attempt_limit) if emergency_recovery else backup_attempt_limit
        for row in backups:
            hostname = str(row["hostname"])
            if hostname == current:
                continue
            if attempted_backups >= attempt_limit:
                break
            failed_at = recent_backup_failures.get(hostname)
            cooldown_active = failed_at and backup_retry_cooldown and time.monotonic() - failed_at < backup_retry_cooldown
            if cooldown_active and not disconnected_failure:
                skipped_cooldown += 1
                continue
            attempted_backups += 1
            attempted_backup_hostnames.append(hostname)
            try:
                print(f"Connecting verified backup: {hostname}", flush=True)
                connect_result = connect_relay(hostname, guard_config)
                actual = connect_result.get("actual_hostname") or connect_result.get("hostname") or hostname
                actual_state, _ = guard.mullvad_status(timeout=5)
                if emergency_recovery and actual_state.lower().startswith("connected") and not emergency_fallback_hostname:
                    emergency_fallback_hostname = str(actual)
                speed_config = dict(guard_config)
                post_switch = full_health_check(speed_config)
                last_speed_check_at = time.monotonic()
                record_health_check(actual, post_switch)
                print(
                    f"Backup check {hostname}->{actual}: {'ok' if post_switch.ok else 'bad'} {post_switch.reason}",
                    flush=True,
                )
                if post_switch.ok:
                    switched = True
                    consecutive_failures = 0
                    recent_backup_failures.pop(hostname, None)
                    break
                recent_backup_failures[hostname] = time.monotonic()
            except Exception as exc:
                recent_backup_failures[hostname] = time.monotonic()
                print(f"Backup {hostname} failed: {exc}", flush=True)
        if skipped_cooldown:
            print(f"Skipped {skipped_cooldown} recently failed backup relay(s).", flush=True)

        if switched:
            time.sleep(interval)
            continue

        now_mono = time.monotonic()
        rescue_retest = emergency_recovery or attempted_backups > 0 or skipped_cooldown > 0 or not backups
        if rescue_retest:
            rescue_label = "Emergency" if emergency_recovery else "Recovery"
            print(
                f"{rescue_label}: whitelist/verified backups have no usable speed; "
                f"refreshing fast rank and true-testing all known rescue candidates until one passes full health.",
                flush=True,
            )
            rescue_finished_at = time.monotonic()
            try:
                result = emergency_rescue_true_test(
                    guard_config,
                    batch_size=max(pool_size, 5),
                    candidate_limit=0,
                    min_working_mbps=min_working_mbps,
                    abandon_after=abandon_after,
                    fast_workers=fast_workers,
                    fast_timeout=fast_timeout,
                    fast_port=fast_port,
                    exclude_hostnames=attempted_backup_hostnames,
                )
                print(
                    f"{rescue_label} rescue tested={result['tested_count']}/{result['candidate_count']} "
                    f"connected={result.get('connected')} fallback={result.get('fallback_connected')} "
                    f"exhausted={result.get('exhausted')}",
                    flush=True,
                )
                rescue_fallback = result.get("fallback_connected")
                if result.get("connected"):
                    if emergency_recovery:
                        last_emergency_pool_refresh_at = 0.0
                    else:
                        last_pool_refresh_at = 0.0
                    consecutive_failures = 0
                    time.sleep(interval)
                    continue
                rescue_finished_at = time.monotonic()
                if emergency_recovery:
                    last_emergency_pool_refresh_at = 0.0
                else:
                    last_pool_refresh_at = 0.0
            except Exception as exc:
                rescue_fallback = None
                rescue_finished_at = time.monotonic()
                if emergency_recovery:
                    last_emergency_pool_refresh_at = 0.0
                else:
                    last_pool_refresh_at = 0.0
                print(f"{rescue_label} rescue failed: {exc}", flush=True)

            if disconnected_failure and emergency_fallback_hostname and not rescue_fallback:
                try:
                    print(
                        f"Emergency recovery: rescue did not find a fully healthy relay; "
                        f"keeping connected fallback {emergency_fallback_hostname}.",
                        flush=True,
                    )
                    connect_relay(emergency_fallback_hostname, guard_config)
                    consecutive_failures = 0
                    time.sleep(interval)
                    continue
                except Exception as exc:
                    print(f"Emergency fallback reconnect failed: {exc}", flush=True)
            print(f"{rescue_label} rescue did not find a healthy relay; will retry after the next guard interval.", flush=True)
            time.sleep(interval)
            continue

        if not refresh_pool_on_failure:
            print(
                "Verified backups unavailable or slow; automatic true-test pool is disabled to avoid repeated disconnects.",
                flush=True,
            )
            time.sleep(interval)
            continue

        if last_pool_refresh_at and refresh_pool_cooldown and now_mono - last_pool_refresh_at < refresh_pool_cooldown:
            remaining = int(refresh_pool_cooldown - (now_mono - last_pool_refresh_at))
            print(
                f"Verified backups unavailable or slow; pool refresh cooldown active for {max(0, remaining)}s.",
                flush=True,
            )
            time.sleep(interval)
            continue

        print("Verified backups unavailable or slow; refreshing all fast candidates and true-testing pool.", flush=True)
        last_pool_refresh_at = time.monotonic()
        try:
            fast_rank_all(workers=fast_workers, timeout=fast_timeout, port=fast_port)
            result = verify_candidate_pool(
                guard_config,
                pool_size=pool_size,
                ready_target=ready_target,
                candidate_limit=candidate_limit,
                min_working_mbps=min_working_mbps,
                abandon_after=abandon_after,
                fast_workers=fast_workers,
                fast_timeout=fast_timeout,
                fast_port=fast_port,
                connect_best=True,
                restore=False,
            )
            print(
                f"Pool refresh ready={result['ready_count']}/{ready_target} "
                f"tested={result['tested_count']} connected={result.get('connected')}",
                flush=True,
            )
        except Exception as exc:
            print(f"Pool refresh failed: {exc}", flush=True)

        time.sleep(interval)


def user_idle_seconds() -> Optional[float]:
    try:
        proc = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    for line in proc.stdout.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.split("=")[-1].strip()) / 1_000_000_000
            except ValueError:
                return None
    return None


def inventory_daemon(
    config: Dict[str, Any],
    active_interval: int,
    full_interval: int,
    idle_seconds: int,
    active_limit: int,
    full_limit: int,
    min_working_mbps: float,
    abandon_after: int,
) -> int:
    sync_relays(update=True)
    last_full_at = 0.0
    print(
        f"Inventory daemon started active_interval={active_interval}s full_interval={full_interval}s idle_seconds={idle_seconds}s",
        flush=True,
    )
    while True:
        current_idle = user_idle_seconds()
        should_full = time.monotonic() - last_full_at >= full_interval
        is_idle = idle_seconds <= 0 or current_idle is None or current_idle >= idle_seconds
        if should_full and is_idle:
            print(f"Starting full inventory scan. user_idle_seconds={current_idle}", flush=True)
            scan_inventory(
                config,
                scope="full",
                limit=full_limit,
                include_abandoned=True,
                restore=True,
                connect_best=False,
                min_working_mbps=min_working_mbps,
                abandon_after=abandon_after,
            )
            last_full_at = time.monotonic()
        else:
            active = relays_for_scope("active", limit=active_limit)
            if active:
                print(f"Starting active inventory scan for {len(active)} known-good relay(s).", flush=True)
                scan_inventory(
                    config,
                    scope="active",
                    limit=active_limit,
                    include_abandoned=False,
                    restore=True,
                    connect_best=False,
                    min_working_mbps=min_working_mbps,
                    abandon_after=abandon_after,
                )
            else:
                print("No known-good relays yet; running a small full inventory seed scan.", flush=True)
                scan_inventory(
                    config,
                    scope="full",
                    limit=active_limit or 20,
                    include_abandoned=True,
                    restore=True,
                    connect_best=False,
                    min_working_mbps=min_working_mbps,
                    abandon_after=abandon_after,
                )
        time.sleep(active_interval)


def connect_relay(hostname: str, config: Dict[str, Any]) -> Dict[str, Any]:
    requested = hostname.strip().lower()
    if not requested:
        raise ValueError("Relay hostname is required.")
    if guard.hostname_is_blocked(requested, config):
        code = guard.hostname_country_code(requested) or "unknown"
        raise ValueError(f"Relay {requested} is blocked by country policy ({code}).")
    sync_relays(update=False)
    row = relay_row(requested)
    if not row:
        raise ValueError(f"Unknown relay hostname: {requested}")
    if country_is_blocked(row["country"], config):
        raise ValueError(f"Relay {requested} is blocked by country policy ({row['country']}).")

    state, status_text = guard.mullvad_status()
    current = guard.current_relay_hostname()
    if state.lower().startswith("connected") and current == requested:
        return {
            "hostname": requested,
            "requested_hostname": requested,
            "actual_hostname": current,
            "changed": False,
            "exact_match": True,
            "accepted_fallback": False,
            "status": status_text,
        }

    timeout = int(config.get("connect_timeout_seconds", 45))
    quick = bool(config.get("quick_connect", False))
    strict_hostname = bool(config.get("strict_hostname", True))
    quick_timeout = int(config.get("quick_connect_timeout_seconds", 12))
    disconnect_timeout = int(config.get("disconnect_wait_timeout_seconds", 5 if quick else 20))
    disconnect_note: Optional[str] = None
    try:
        if quick:
            guard.disconnect_fast(timeout=disconnect_timeout)
        else:
            guard.disconnect(timeout=disconnect_timeout)
    except Exception as exc:
        disconnect_note = str(exc)
    guard.set_relay(requested, timeout=timeout)
    if quick:
        guard.connect_quick_then_wait(quick_timeout=quick_timeout, wait_timeout=timeout)
    else:
        guard.connect(timeout=timeout)
    new_state, new_status = guard.mullvad_status()
    actual = guard.current_relay_hostname()
    if relay_hostname_blocked(actual, config=config):
        raise RuntimeError(
            f"Mullvad connected to blocked relay {actual or 'unknown'} while requesting {requested}. "
            f"Status: {new_status}"
        )
    exact_match = actual == requested
    accepted_fallback = False
    fallback_reason: Optional[str] = None
    if not exact_match:
        if strict_hostname:
            raise RuntimeError(
                f"Mullvad connected to {actual or 'unknown'} instead of requested {requested}. Status: {new_status}"
            )
        if actual and same_location(requested, actual):
            accepted_fallback = True
            fallback_reason = "same-location"
        elif actual and bool(config.get("accept_any_connected_fallback", False)) and new_state.lower().startswith("connected"):
            accepted_fallback = True
            fallback_reason = "any-connected"
        else:
            raise RuntimeError(
                f"Mullvad connected to {actual or 'unknown'} instead of requested {requested}; fallback not accepted. "
                f"Status: {new_status}"
            )
    return {
        "hostname": actual or requested,
        "requested_hostname": requested,
        "actual_hostname": actual,
        "changed": True,
        "exact_match": exact_match,
        "accepted_fallback": accepted_fallback,
        "fallback_reason": fallback_reason,
        "disconnect_note": disconnect_note,
        "status": new_status,
    }


def cli(args: argparse.Namespace, config: Dict[str, Any]) -> int:
    if args.inventory_command == "refresh":
        result = sync_relays(update=not args.no_update)
        print(json.dumps(result, ensure_ascii=False) if args.json else result)
        return 0
    if args.inventory_command == "nightly-toggle":
        state = getattr(args, "state", "status")
        if state == "on":
            set_scheduled_full_scan(True)
        elif state == "off":
            set_scheduled_full_scan(False)
        enabled = not scheduled_full_scan_disabled()
        with connect_db() as conn:
            last_date = meta_value(conn, META_LAST_SCHEDULED_FULL_SCAN_DATE)
        payload = {"enabled": enabled, "last_scan_date": last_date}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"Daily 04:00 full-node scan: {'ON' if enabled else 'OFF'} (last run: {last_date or 'never'})")
        return 0
    if args.inventory_command == "top":
        rows = top_relays(limit=args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                loss = row["last_loss_pct"] if "last_loss_pct" in row.keys() else None
                loss_txt = "n/a" if loss is None else f"{loss:.0f}%"
                print(
                    f"{row['hostname']:18} {row['country']}/{row['city']} "
                    f"last={row['last_mbps']} best={row['best_mbps']} loss={loss_txt} "
                    f"tested_at={row['last_test_at']} score={row['score']}"
                )
        return 0
    if args.inventory_command == "whitelist":
        rows = whitelist_relays(
            limit=args.limit,
            min_mbps=args.min_mbps,
            preferred_mbps=args.preferred_mbps,
            current_hour=args.hour,
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                hour = f"h{row['current_hour']:02d}"
                hour_speed = row["hour_avg_mbps"] if row["hour_avg_mbps"] is not None else "-"
                print(
                    f"{row['hostname']:18} {row['country']}/{row['city']} score={row['whitelist_score']:.0f} "
                    f"avg={row['avg_mbps']}Mbps hour_avg={hour_speed} {hour} "
                    f"success={row['success_rate']} hour_success={row['hour_success_rate']} "
                    f"tested_at={row['last_success_at']}"
                )
        return 0
    if args.inventory_command == "fast-rank":
        data = fast_rank_all(
            workers=args.workers,
            timeout=args.timeout,
            port=args.port,
            limit=args.limit,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data)
        return 0
    if args.inventory_command == "verify-pool":
        data = verify_candidate_pool(
            config,
            pool_size=args.pool_size,
            ready_target=args.ready_target,
            candidate_limit=args.candidate_limit,
            min_working_mbps=args.min_working_mbps,
            abandon_after=args.abandon_after,
            fast_workers=args.fast_workers,
            fast_timeout=args.fast_timeout,
            fast_port=args.fast_port,
            connect_best=args.connect_best,
            restore=not args.no_restore,
            keep_current_if_no_better=args.keep_current_if_no_better,
            better_min_delta_mbps=args.better_min_delta_mbps,
            better_min_ratio=args.better_min_ratio,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data)
        return 0
    if args.inventory_command == "summary":
        data = summary()
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data)
        return 0
    if args.inventory_command == "scan":
        scan_inventory(
            config,
            scope=args.scope,
            limit=args.limit,
            include_abandoned=args.include_abandoned,
            restore=not args.no_restore,
            connect_best=args.connect_best,
            dry_run=args.dry_run,
            min_working_mbps=args.min_working_mbps,
            abandon_after=args.abandon_after,
            hostnames=[part for part in (args.hostnames or "").split(",") if part.strip()],
        )
        return 0
    if args.inventory_command == "daemon":
        return inventory_daemon(
            config,
            active_interval=args.active_interval,
            full_interval=args.full_interval,
            idle_seconds=args.idle_seconds,
            active_limit=args.active_limit,
            full_limit=args.full_limit,
            min_working_mbps=args.min_working_mbps,
            abandon_after=args.abandon_after,
        )
    if args.inventory_command == "auto-guard":
        config["health_mode"] = args.health_mode
        config["watch_interval_seconds"] = args.interval
        config["speed_check_every_seconds"] = args.speed_check_every
        config["min_mbps"] = args.min_mbps
        config["preferred_mbps"] = args.preferred_mbps
        config["max_latency_ms"] = args.max_latency_ms
        config["quick_connect"] = True
        config["quick_connect_timeout_seconds"] = args.quick_connect_timeout
        config["disconnect_wait_timeout_seconds"] = args.disconnect_wait_timeout
        config["strict_hostname"] = args.strict_hostname
        config["url_check_timeout_seconds"] = args.url_check_timeout
        config["passive_probe_period_seconds"] = args.passive_period
        config["passive_min_down_mbps"] = args.passive_min_down_mbps
        config["passive_activity_threshold_bytes"] = args.passive_activity_threshold_bytes
        config["active_speed_when_passive_idle"] = not args.no_active_speed_when_passive_idle
        config["passive_low_speed_can_fail"] = args.passive_low_speed_can_fail
        config["idle_refresh_enabled"] = not args.no_idle_refresh
        config["idle_refresh_after_seconds"] = args.idle_refresh_after
        config["idle_refresh_user_idle_seconds"] = args.idle_refresh_user_idle_seconds
        config["idle_refresh_activity_threshold_bytes"] = args.idle_refresh_activity_threshold_bytes
        config["idle_refresh_batch_size"] = args.idle_refresh_batch_size
        config["nightly_full_scan_enabled"] = not args.no_nightly_full_scan
        config["nightly_full_scan_cooldown_seconds"] = args.nightly_full_scan_cooldown
        config["nightly_full_scan_max_seconds"] = args.nightly_full_scan_max_seconds
        config["nightly_full_scan_better_min_delta_mbps"] = args.nightly_full_scan_better_min_delta_mbps
        config["nightly_full_scan_better_min_ratio"] = args.nightly_full_scan_better_min_ratio
        config["connecting_grace_seconds"] = args.connecting_grace
        config["daemon_retry_cooldown_seconds"] = args.daemon_retry_cooldown
        config["url_emergency_min_failed"] = args.url_emergency_min_failed
        if args.url_checks:
            config["url_checks"] = [part.strip() for part in args.url_checks.split(",") if part.strip()]
        return auto_guard(
            config,
            interval=args.interval,
            pool_size=args.pool_size,
            ready_target=args.ready_target,
            candidate_limit=args.candidate_limit,
            min_working_mbps=args.min_working_mbps,
            abandon_after=args.abandon_after,
            fast_rank_interval=args.fast_rank_interval,
            fast_workers=args.fast_workers,
            fast_timeout=args.fast_timeout,
            fast_port=args.fast_port,
            failure_threshold=args.failure_threshold,
            backup_attempt_limit=args.backup_attempt_limit,
            recovery_search_limit=args.recovery_search_limit,
            emergency_attempt_limit=args.emergency_attempt_limit,
            backup_retry_cooldown=args.backup_retry_cooldown,
            refresh_pool_on_failure=args.refresh_pool_on_failure,
            refresh_pool_cooldown=args.refresh_pool_cooldown,
            preferred_mbps=args.preferred_mbps,
            idle_refresh_enabled=not args.no_idle_refresh,
            idle_refresh_after_seconds=args.idle_refresh_after,
            idle_refresh_user_idle_seconds=args.idle_refresh_user_idle_seconds,
            idle_refresh_activity_threshold_bytes=args.idle_refresh_activity_threshold_bytes,
            idle_refresh_batch_size=args.idle_refresh_batch_size,
            nightly_full_scan_enabled=not args.no_nightly_full_scan,
            nightly_full_scan_cooldown_seconds=args.nightly_full_scan_cooldown,
            nightly_full_scan_max_seconds=args.nightly_full_scan_max_seconds,
            nightly_full_scan_better_min_delta_mbps=args.nightly_full_scan_better_min_delta_mbps,
            nightly_full_scan_better_min_ratio=args.nightly_full_scan_better_min_ratio,
            connecting_grace_seconds=args.connecting_grace,
            daemon_retry_cooldown=args.daemon_retry_cooldown,
        )
    if args.inventory_command == "connect":
        result = connect_relay(args.hostname, config)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["status"])
        return 0
    raise ValueError(f"Unknown inventory command: {args.inventory_command}")
