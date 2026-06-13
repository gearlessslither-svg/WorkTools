#!/usr/bin/env python3
"""Local web control panel for Mullvad Speed Guard."""

from __future__ import annotations

import argparse
import html
import json
import os
import plistlib
import re
import signal
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import relay_inventory


APP_DIR = Path(__file__).resolve().parent
GUARD_SCRIPT = APP_DIR / "mullvad_speed_guard.py"
README_PATH = APP_DIR / "README.md"
RESULTS_DIR = APP_DIR / "results"
WATCH_LOG_PATH = RESULTS_DIR / "watch_web.log"
INVENTORY_LOG_PATH = RESULTS_DIR / "inventory_scan.log"
FAST_RANK_LOG_PATH = RESULTS_DIR / "fast_rank.log"
INVENTORY_DAEMON_LOG_PATH = RESULTS_DIR / "inventory_daemon.log"
AUTO_GUARD_LOG_PATH = RESULTS_DIR / "auto_guard.log"
SERVER_LOG_PATH = RESULTS_DIR / "panel_server.log"
TRAFFIC_TOTALS_PATH = RESULTS_DIR / "traffic_totals.json"
PID_PATH = RESULTS_DIR / "watch.pid"
SCAN_PID_PATH = RESULTS_DIR / "inventory_scan.pid"
FAST_RANK_PID_PATH = RESULTS_DIR / "fast_rank.pid"
DAEMON_PID_PATH = RESULTS_DIR / "inventory_daemon.pid"
AUTO_GUARD_PID_PATH = RESULTS_DIR / "auto_guard.pid"
AUTO_GUARD_CONTROL_LOCK_PATH = RESULTS_DIR / "auto_guard_control.lock"
LAUNCH_RUNTIME_DIR = Path.home() / "Library" / "Application Support" / "MullvadSpeedGuard"
LAUNCH_CONTROL_LOCK_PATH = LAUNCH_RUNTIME_DIR / "results" / "auto_guard_control.lock"
LAUNCH_AUTO_GUARD_PID_PATH = LAUNCH_RUNTIME_DIR / "results" / "auto_guard.pid"
LAUNCH_AUTO_GUARD_LOG_PATH = LAUNCH_RUNTIME_DIR / "results" / "auto_guard_launchagent.log"
LAUNCH_AUTO_GUARD_LABEL = "com.story.mullvad-speed-guard.auto-guard"
LAUNCH_AUTO_GUARD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AUTO_GUARD_LABEL}.plist"
PYTHON = "/usr/bin/python3"
APP_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DEFAULT_PORT = 18790
PANEL_VERSION = "2026-06-13-cumulative-traffic"
LAST_INVENTORY_STATE: Dict[str, Any] = {}
LAST_LATENCY_STATE: Dict[str, Any] = {}
LAST_MULLVAD_STATUS_TEXT = ""
LAST_TRAFFIC_STATE: Dict[str, Any] = {}
TRAFFIC_TOTAL_STATE: Dict[str, Any] = {}
TRAFFIC_TOTAL_LOCK = threading.Lock()
BACKGROUND_REFRESH_STARTED = False


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mullvad Speed Guard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --line: #d9dee3;
      --text: #172026;
      --muted: #65727e;
      --accent: #23715a;
      --danger: #a23d36;
      --code: #111820;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }
    main {
      max-width: 1080px;
      margin: 0 auto;
      padding: 18px;
    }
    section {
      margin: 0 0 16px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    h2 {
      margin: 0 0 12px;
      font-size: 15px;
      font-weight: 700;
    }
    .status {
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #9aa5ad;
    }
    .dot.running { background: var(--accent); }
    .dot.warning { background: #b57620; }
    .dot.stopped { background: var(--danger); }
    .guard-banner {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) repeat(3, minmax(130px, .75fr));
      gap: 12px;
      align-items: stretch;
      border-width: 2px;
    }
    .guard-banner.running {
      border-color: #8fc1ae;
      background: #f8fcfa;
    }
    .guard-banner.warning {
      border-color: #d2a35f;
      background: #fffaf0;
    }
    .guard-banner.paused {
      border-color: #d7aaa6;
      background: #fff8f7;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 12px;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      margin: 0 0 5px;
    }
    input, select {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      color: var(--text);
      background: #fff;
      font: inherit;
    }
    .wide { grid-column: span 2; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    button, a.button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 12px;
      color: var(--text);
      background: #fff;
      font: inherit;
      text-decoration: none;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 650;
    }
    button.danger {
      border-color: var(--danger);
      color: var(--danger);
    }
    button:disabled {
      opacity: .55;
      cursor: wait;
    }
    pre {
      min-height: 180px;
      max-height: 360px;
      overflow: auto;
      margin: 0;
      border-radius: 6px;
      padding: 12px;
      background: var(--code);
      color: #eef3f6;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
    }
    .traffic {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfc;
    }
    .metric .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    .metric .value {
      font-size: 30px;
      font-weight: 760;
      letter-spacing: 0;
    }
    .metric .value.compact {
      font-size: 19px;
      line-height: 1.2;
      word-break: break-word;
    }
    .metric .hint {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }
    .muted { color: var(--muted); }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 6px;
      text-align: left;
      vertical-align: middle;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    td button {
      min-height: 30px;
      padding: 5px 9px;
    }
    .summary {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: #fff;
    }
    @media (max-width: 760px) {
      header { align-items: flex-start; flex-direction: column; gap: 8px; }
      .grid, .split, .guard-banner { grid-template-columns: 1fr; }
      .wide { grid-column: span 1; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Mullvad Speed Guard</h1>
    <div class="status"><span id="dot" class="dot"></span><span id="statusText">Loading</span></div>
  </header>
  <main>
    <section id="guardBanner" class="guard-banner paused">
      <div class="metric">
        <div class="label">Auto Guard</div>
        <div id="guardState" class="value compact">Loading</div>
        <div id="guardHint" class="hint">Checking LaunchAgent.</div>
      </div>
      <div class="metric">
        <div class="label">Supervisor</div>
        <div id="guardSupervisor" class="value compact">--</div>
        <div id="guardSupervisorHint" class="hint">LaunchAgent is required for unattended recovery.</div>
      </div>
      <div class="metric">
        <div class="label">PID</div>
        <div id="guardPid" class="value compact">--</div>
        <div id="guardPidHint" class="hint">Waiting.</div>
      </div>
      <div class="metric">
        <div class="label">VPN Intent</div>
        <div id="guardIntent" class="value compact">--</div>
        <div id="guardIntentHint" class="hint">Start keeps VPN protected; Stop disconnects VPN.</div>
      </div>
    </section>
    <section>
      <h2>VPN Traffic</h2>
      <div class="traffic">
        <div class="metric">
          <div class="label">Download</div>
          <div id="trafficDown" class="value">--</div>
          <div id="trafficDownHint" class="hint">Waiting for VPN interface.</div>
        </div>
        <div class="metric">
          <div class="label">Upload</div>
          <div id="trafficUp" class="value">--</div>
          <div id="trafficUpHint" class="hint">Waiting for VPN interface.</div>
        </div>
        <div class="metric">
          <div class="label">Relay</div>
          <div id="relayNode" class="value compact">--</div>
          <div id="relayHint" class="hint">Waiting for Mullvad status.</div>
        </div>
        <div class="metric">
          <div class="label">Latency</div>
          <div id="latencyValue" class="value">--</div>
          <div id="latencyHint" class="hint">Waiting for probe.</div>
        </div>
      </div>
    </section>
    <section>
      <h2>Monitor</h2>
      <div class="grid">
        <div>
          <label for="healthMode">Mode</label>
          <select id="healthMode">
            <option value="adaptive">adaptive</option>
            <option value="latency">latency</option>
            <option value="status">status</option>
            <option value="speed">speed</option>
          </select>
        </div>
        <div>
          <label for="interval">Interval seconds</label>
          <input id="interval" type="number" min="10" step="1" value="30">
        </div>
        <div>
          <label for="speedEvery">Speed sample seconds</label>
          <input id="speedEvery" type="number" min="0" step="1" value="600">
        </div>
        <div>
          <label for="minMbps">Online floor Mbps</label>
          <input id="minMbps" type="number" min="0" step="0.1" value="0.5">
        </div>
        <div>
          <label for="preferredMbps">Preferred Mbps</label>
          <input id="preferredMbps" type="number" min="0" step="0.1" value="8">
        </div>
        <div>
          <label for="maxLatency">Max latency ms</label>
          <input id="maxLatency" type="number" min="0" step="10" value="2500">
        </div>
        <div>
          <label for="maxCandidates">Candidates</label>
          <input id="maxCandidates" type="number" min="1" step="1" value="20">
        </div>
        <div class="wide">
          <label for="countries">Countries filter</label>
          <input id="countries" value="" placeholder="empty = all countries">
        </div>
        <div>
          <label for="poolSize">Pool size</label>
          <input id="poolSize" type="number" min="1" step="1" value="5">
        </div>
        <div>
          <label for="readyTarget">Ready target</label>
          <input id="readyTarget" type="number" min="1" step="1" value="3">
        </div>
        <div>
          <label for="candidateLimit">Candidate limit</label>
          <input id="candidateLimit" type="number" min="5" step="1" value="25">
        </div>
        <div>
          <label for="fastRankEvery">Fast rank seconds</label>
          <input id="fastRankEvery" type="number" min="0" step="60" value="3600">
        </div>
        <div>
          <label for="minWorkingMbps">Whitelist floor Mbps</label>
          <input id="minWorkingMbps" type="number" min="0" step="0.01" value="0.05">
        </div>
        <div>
          <label for="abandonAfter">Abandon after</label>
          <input id="abandonAfter" type="number" min="1" step="1" value="3">
        </div>
        <div>
          <label for="failureThreshold">Failure threshold</label>
          <input id="failureThreshold" type="number" min="1" step="1" value="3">
        </div>
        <div>
          <label for="backupAttemptLimit">Backup attempts</label>
          <input id="backupAttemptLimit" type="number" min="1" step="1" value="3">
        </div>
        <div>
          <label for="quickConnectTimeout">Quick connect seconds</label>
          <input id="quickConnectTimeout" type="number" min="3" step="1" value="12">
        </div>
        <div>
          <label for="disconnectWaitTimeout">Disconnect seconds</label>
          <input id="disconnectWaitTimeout" type="number" min="1" step="1" value="5">
        </div>
        <div>
          <label for="urlCheckTimeout">URL timeout seconds</label>
          <input id="urlCheckTimeout" type="number" min="3" step="1" value="10">
        </div>
        <div>
          <label for="urlEmergencyMinFailed">URL emergency count</label>
          <input id="urlEmergencyMinFailed" type="number" min="0" step="1" value="0">
        </div>
        <div>
          <label for="connectingGrace">Connecting grace</label>
          <input id="connectingGrace" type="number" min="0" step="5" value="45">
        </div>
        <div>
          <label for="daemonRetryCooldown">Daemon retry cooldown</label>
          <input id="daemonRetryCooldown" type="number" min="0" step="30" value="180">
        </div>
        <div>
          <label for="refreshPoolCooldown">Refresh cooldown</label>
          <input id="refreshPoolCooldown" type="number" min="0" step="60" value="1800">
        </div>
        <div>
          <label for="backupRetryCooldown">Backup retry cooldown</label>
          <input id="backupRetryCooldown" type="number" min="0" step="60" value="900">
        </div>
        <div>
          <label for="passivePeriod">Passive period</label>
          <input id="passivePeriod" type="number" min="60" step="60" value="900">
        </div>
        <div>
          <label for="passiveMinDown">Passive Mbps</label>
          <input id="passiveMinDown" type="number" min="0" step="0.1" value="8">
        </div>
        <div>
          <label for="idleRefreshAfter">Idle refresh seconds</label>
          <input id="idleRefreshAfter" type="number" min="0" step="60" value="1800">
        </div>
        <div>
          <label for="idleRefreshUserIdle">User idle seconds</label>
          <input id="idleRefreshUserIdle" type="number" min="0" step="60" value="1800">
        </div>
        <div>
          <label for="idleRefreshTraffic">Idle traffic bytes</label>
          <input id="idleRefreshTraffic" type="number" min="0" step="1024" value="262144">
        </div>
        <div>
          <label for="idleRefreshBatch">Idle batch</label>
          <input id="idleRefreshBatch" type="number" min="1" step="1" value="1">
        </div>
        <div class="wide">
          <label for="urlChecks">URL probe</label>
          <input id="urlChecks" value="https://chatgpt.com/,https://chatgpt.com/backend-api/codex/responses,https://www.youtube.com/generate_204" placeholder="comma-separated URLs">
        </div>
        <div>
          <label for="refreshPoolOnFailure">Refresh on failure</label>
          <input id="refreshPoolOnFailure" type="checkbox">
        </div>
        <div>
          <label for="disableIdleRefresh">Disable idle refresh</label>
          <input id="disableIdleRefresh" type="checkbox">
        </div>
      </div>
      <div class="actions">
        <button id="refreshBtn">Refresh</button>
        <button id="previewBtn">Preview Relays</button>
        <button id="refreshInventoryBtn">Refresh Inventory</button>
        <button id="fastRankBtn" class="primary">Fast Rank All</button>
        <button id="stopFastRankBtn" class="danger">Stop Fast Rank</button>
        <button id="verifyPoolBtn" class="primary">True Test Pool</button>
        <button id="stopScanBtn" class="danger">Stop True Test</button>
        <button id="startAutoGuardBtn" class="primary">Start Auto Guard</button>
        <button id="stopAutoGuardBtn" class="danger">Stop Auto Guard</button>
        <button id="stopAllBtn" class="danger">Stop All Tasks</button>
        <a class="button" href="/readme" target="_blank">README</a>
      </div>
    </section>

    <section>
      <h2>Relay Pool</h2>
      <div id="inventorySummary" class="summary"></div>
      <h2>Fast Candidates</h2>
      <table>
        <thead>
          <tr>
            <th>Relay</th>
            <th>Location</th>
            <th>Fast ms</th>
            <th>Fast Tested</th>
            <th>True Tested</th>
            <th>Risk</th>
            <th>Known Mbps</th>
            <th>Reachable</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="fastFive">
          <tr><td colspan="9" class="muted">Run Fast Rank All.</td></tr>
        </tbody>
      </table>
      <h2 style="margin-top: 16px;">Verified Backups</h2>
      <table>
        <thead>
          <tr>
            <th>Relay</th>
            <th>Location</th>
            <th>Last Mbps</th>
            <th>Best Mbps</th>
            <th>Latency</th>
            <th>True Tested</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="topFive">
          <tr><td colspan="8" class="muted">No speed history yet. Run an inventory scan.</td></tr>
        </tbody>
      </table>
    </section>

    <div class="split">
      <section>
        <h2>Mullvad Status</h2>
        <pre id="mullvadStatus">Loading...</pre>
      </section>
      <section>
        <h2>Relay Preview</h2>
        <pre id="preview">Click Preview Relays.</pre>
      </section>
    </div>

    <section>
      <h2>Auto Guard Log</h2>
      <pre id="log">Loading...</pre>
    </section>
    <section>
      <h2>Inventory Log</h2>
      <pre id="inventoryLog">Loading...</pre>
    </section>
  </main>
  <script>
    const ids = [
      "healthMode", "interval", "speedEvery", "minMbps", "maxLatency", "maxCandidates", "countries",
      "poolSize", "readyTarget", "candidateLimit", "fastRankEvery", "minWorkingMbps", "abandonAfter",
      "failureThreshold", "backupAttemptLimit", "quickConnectTimeout", "disconnectWaitTimeout",
      "preferredMbps", "urlCheckTimeout", "urlEmergencyMinFailed", "connectingGrace", "daemonRetryCooldown",
      "refreshPoolCooldown", "backupRetryCooldown", "passivePeriod", "passiveMinDown",
      "idleRefreshAfter", "idleRefreshUserIdle", "idleRefreshTraffic", "idleRefreshBatch",
      "urlChecks", "refreshPoolOnFailure", "disableIdleRefresh"
    ];
    const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
    const dot = document.getElementById("dot");
    const statusText = document.getElementById("statusText");
    const mullvadStatus = document.getElementById("mullvadStatus");
    const log = document.getElementById("log");
    const inventoryLog = document.getElementById("inventoryLog");
    const inventorySummary = document.getElementById("inventorySummary");
    const trafficDown = document.getElementById("trafficDown");
    const trafficUp = document.getElementById("trafficUp");
    const trafficDownHint = document.getElementById("trafficDownHint");
    const trafficUpHint = document.getElementById("trafficUpHint");
    const relayNode = document.getElementById("relayNode");
    const relayHint = document.getElementById("relayHint");
    const latencyValue = document.getElementById("latencyValue");
    const latencyHint = document.getElementById("latencyHint");
    const guardBanner = document.getElementById("guardBanner");
    const guardState = document.getElementById("guardState");
    const guardHint = document.getElementById("guardHint");
    const guardSupervisor = document.getElementById("guardSupervisor");
    const guardSupervisorHint = document.getElementById("guardSupervisorHint");
    const guardPid = document.getElementById("guardPid");
    const guardPidHint = document.getElementById("guardPidHint");
    const guardIntent = document.getElementById("guardIntent");
    const guardIntentHint = document.getElementById("guardIntentHint");
    const topFive = document.getElementById("topFive");
    const fastFive = document.getElementById("fastFive");
    const preview = document.getElementById("preview");
    const refreshBtn = document.getElementById("refreshBtn");
    const previewBtn = document.getElementById("previewBtn");
    const refreshInventoryBtn = document.getElementById("refreshInventoryBtn");
    const fastRankBtn = document.getElementById("fastRankBtn");
    const stopFastRankBtn = document.getElementById("stopFastRankBtn");
    const verifyPoolBtn = document.getElementById("verifyPoolBtn");
    const stopScanBtn = document.getElementById("stopScanBtn");
    const startAutoGuardBtn = document.getElementById("startAutoGuardBtn");
    const stopAutoGuardBtn = document.getElementById("stopAutoGuardBtn");
    const stopAllBtn = document.getElementById("stopAllBtn");

    function settings() {
      return {
        health_mode: el.healthMode.value,
        interval: el.interval.value,
        speed_check_every: el.speedEvery.value,
        min_mbps: el.minMbps.value,
        preferred_mbps: el.preferredMbps.value,
        max_latency_ms: el.maxLatency.value,
        countries: el.countries.value,
        max_candidates: el.maxCandidates.value,
        pool_size: el.poolSize.value,
        ready_target: el.readyTarget.value,
        candidate_limit: el.candidateLimit.value,
        fast_rank_interval: el.fastRankEvery.value,
        include_abandoned: true,
        min_working_mbps: el.minWorkingMbps.value,
        abandon_after: el.abandonAfter.value,
        failure_threshold: el.failureThreshold.value,
        backup_attempt_limit: el.backupAttemptLimit.value,
        backup_retry_cooldown: el.backupRetryCooldown.value,
        quick_connect_timeout: el.quickConnectTimeout.value,
        disconnect_wait_timeout: el.disconnectWaitTimeout.value,
        url_check_timeout: el.urlCheckTimeout.value,
        url_emergency_min_failed: el.urlEmergencyMinFailed.value,
        connecting_grace: el.connectingGrace.value,
        daemon_retry_cooldown: el.daemonRetryCooldown.value,
        refresh_pool_cooldown: el.refreshPoolCooldown.value,
        passive_period: el.passivePeriod.value,
        passive_min_down_mbps: el.passiveMinDown.value,
        idle_refresh_after: el.idleRefreshAfter.value,
        idle_refresh_user_idle_seconds: el.idleRefreshUserIdle.value,
        idle_refresh_activity_threshold_bytes: el.idleRefreshTraffic.value,
        idle_refresh_batch_size: el.idleRefreshBatch.value,
        no_idle_refresh: el.disableIdleRefresh.checked,
        url_checks: el.urlChecks.value,
        refresh_pool_on_failure: el.refreshPoolOnFailure.checked
      };
    }

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      })[ch]);
    }

    function fmtBytes(bytes) {
      const value = Number(bytes);
      if (!Number.isFinite(value) || value < 0) return "--";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let n = value;
      let unit = 0;
      while (n >= 1024 && unit < units.length - 1) {
        n /= 1024;
        unit += 1;
      }
      const digits = unit === 0 ? 0 : n >= 100 ? 0 : n >= 10 ? 1 : 2;
      return `${n.toFixed(digits)} ${units[unit]}`;
    }

    function fmtMbps(value) {
      if (!Number.isFinite(value) || value < 0) return "--";
      return `${value.toFixed(value >= 10 ? 1 : 2)} Mbps`;
    }

    function fmtTime(value) {
      if (!value) return "--";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      const pad = n => String(n).padStart(2, "0");
      return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
    }

    function candidateRisk(row) {
      if (row?.candidate_risk) return row.candidate_risk;
      if (row?.status === "working" && Number(row?.success_count || 0) > 0) return "verified";
      if (row?.status !== "working" && Number(row?.success_count || 0) > 0) return "previously verified";
      if ((row?.status === "no_speed" || Number(row?.consecutive_failures || 0) > 0) && row?.last_test_at) return "failed true-test";
      if (row?.status === "abandoned") return "avoid";
      if (!row?.last_test_at) return "untested";
      return "unverified";
    }

    let lastTrafficSample = null;
    function renderTraffic(traffic) {
      const now = performance.now();
      const down = Number(traffic?.download_bytes);
      const up = Number(traffic?.upload_bytes);
      const rawDown = Number(traffic?.interface_download_bytes);
      const rawUp = Number(traffic?.interface_upload_bytes);
      let downRate = null;
      let upRate = null;
      if (
        lastTrafficSample &&
        Number.isFinite(down) &&
        Number.isFinite(up)
      ) {
        const elapsed = (now - lastTrafficSample.time) / 1000;
        if (elapsed > 0.5 && down >= lastTrafficSample.down && up >= lastTrafficSample.up) {
          downRate = ((down - lastTrafficSample.down) * 8) / elapsed / 1_000_000;
          upRate = ((up - lastTrafficSample.up) * 8) / elapsed / 1_000_000;
        }
      }
      if (Number.isFinite(down) && Number.isFinite(up)) {
        lastTrafficSample = { time: now, down, up };
      }
      trafficDown.textContent = Number.isFinite(down) ? fmtBytes(down) : "--";
      trafficUp.textContent = Number.isFinite(up) ? fmtBytes(up) : "--";
      const iface = traffic?.interface ? `Interface ${traffic.interface}` : "VPN interface not detected";
      const at = traffic?.sampled_at ? ` at ${traffic.sampled_at}` : "";
      const rawDownText = Number.isFinite(rawDown) ? `; interface ${fmtBytes(rawDown)}` : "";
      const rawUpText = Number.isFinite(rawUp) ? `; interface ${fmtBytes(rawUp)}` : "";
      const downRateText = downRate === null ? "rate --" : `rate ${fmtMbps(downRate)}`;
      const upRateText = upRate === null ? "rate --" : `rate ${fmtMbps(upRate)}`;
      trafficDownHint.textContent = `${iface}; cumulative${rawDownText}; ${downRateText}${at}`;
      trafficUpHint.textContent = `${iface}; cumulative${rawUpText}; ${upRateText}${at}`;
    }

    function renderConnection(connection, latency, currentHistory) {
      relayNode.textContent = connection?.relay || "--";
      const historyBits = [];
      if (currentHistory?.last_test_at) historyBits.push(`true ${fmtTime(currentHistory.last_test_at)}`);
      if (currentHistory?.fast_probe_at) historyBits.push(`fast ${fmtTime(currentHistory.fast_probe_at)}`);
      const history = historyBits.length ? `; ${historyBits.join("; ")}` : "; not true-tested yet";
      relayHint.textContent = `${connection?.visible_location || connection?.state || "No relay detected."}${history}`;
      renderLatency(latency);
    }

    function renderLatency(latency) {
      if (latency?.latency_ms !== null && latency?.latency_ms !== undefined) {
        latencyValue.textContent = `${Number(latency.latency_ms).toFixed(0)} ms`;
      } else {
        latencyValue.textContent = "--";
      }
      latencyHint.textContent = latency?.reason || latency?.sampled_at || "Latency probe pending.";
    }

    function renderAutoGuard(inventory) {
      const status = inventory?.auto_guard_supervision || {};
      const controlLock = inventory?.auto_guard_control_lock || null;
      const mode = status.mode || (inventory?.auto_guard_running ? "unsupervised" : "paused");
      const running = Boolean(status.running || inventory?.auto_guard_running);
      const pid = status.launch_pid || status.local_pid || inventory?.auto_guard_pid || "--";
      const actionPaused = running && Boolean(controlLock);
      guardBanner.className = "guard-banner " + (
        actionPaused ? "warning" : mode === "launchagent" ? "running" : running ? "warning" : "paused"
      );
      guardState.textContent = actionPaused ? "PAUSED" : running ? "RUNNING" : "PAUSED";
      guardSupervisor.textContent = mode === "launchagent" ? "LaunchAgent" : running ? "Unsupervised" : "Stopped";
      guardPid.textContent = String(pid || "--");
      guardIntent.textContent = actionPaused ? "Maintenance" : running ? "Protect VPN" : "VPN Off";
      if (actionPaused) {
        const reason = controlLock.reason || "manual maintenance";
        guardHint.textContent = `Action paused by maintenance lock: ${reason}.`;
        guardSupervisorHint.textContent = mode === "launchagent" ? (status.launch_state || "running") : "not supervised";
        guardIntentHint.textContent = "The process stays alive and resumes when the task exits or the lock expires.";
      } else if (mode === "launchagent") {
        guardHint.textContent = "Background recovery is supervised by macOS.";
        guardSupervisorHint.textContent = status.launch_state || "running";
        guardIntentHint.textContent = "It will keep VPN connected and switch by whitelist when needed.";
      } else if (running) {
        guardHint.textContent = "Running, but not under LaunchAgent. Press Start Auto Guard to make it persistent.";
        guardSupervisorHint.textContent = "This mode will not auto-restart after a crash.";
        guardIntentHint.textContent = "Protection is partial until LaunchAgent takes over.";
      } else {
        guardHint.textContent = "Paused by user or not started.";
        guardSupervisorHint.textContent = status.error || "No LaunchAgent process.";
        guardIntentHint.textContent = "Stop means VPN should be disconnected.";
      }
      guardPidHint.textContent = status.launch_loaded ? "Loaded in launchctl." : "Not loaded in launchctl.";
    }

    function renderInventory(inventory) {
      const counts = inventory?.counts || {};
      inventorySummary.innerHTML = [
        ["total", counts.total], ["working", counts.working], ["no speed", counts.no_speed],
        ["abandoned", counts.abandoned], ["unknown", counts.unknown],
        ["fast rank", inventory?.fast_running ? "running" : "stopped"],
        ["true test", inventory?.scan_running ? "running" : "stopped"],
        ["auto guard", inventory?.auto_guard_supervision?.mode || (inventory?.auto_guard_running ? "running" : "stopped")]
      ].map(([k, v]) => `<span class="pill">${esc(k)}: ${esc(v ?? 0)}</span>`).join("");

      const rows = inventory?.top5 || [];
      const fastRows = inventory?.fast_top5 || [];
      if (!fastRows.length) {
        fastFive.innerHTML = '<tr><td colspan="9" class="muted">Run Fast Rank All.</td></tr>';
      } else {
        fastFive.innerHTML = fastRows.map(row => `
          <tr>
            <td>${esc(row.hostname)}</td>
            <td>${esc(row.country)}/${esc(row.city)}</td>
            <td>${esc(row.fast_latency_ms ?? "")}</td>
            <td>${esc(fmtTime(row.fast_probe_at))}</td>
            <td>${esc(fmtTime(row.last_test_at))}</td>
            <td>${esc(candidateRisk(row))}</td>
            <td>${esc(row.best_mbps ?? row.last_mbps ?? "")}</td>
            <td>${row.fast_reachable ? "yes" : "no"}</td>
            <td><button onclick="connectRelay('${esc(row.hostname)}')">Connect</button></td>
          </tr>
        `).join("");
      }
      if (!rows.length) {
        topFive.innerHTML = '<tr><td colspan="8" class="muted">No speed history yet. Run an inventory scan.</td></tr>';
      } else {
        topFive.innerHTML = rows.map(row => `
          <tr>
            <td>${esc(row.hostname)}</td>
            <td>${esc(row.country)}/${esc(row.city)}</td>
            <td>${esc(row.last_mbps ?? "")}</td>
            <td>${esc(row.best_mbps ?? "")}</td>
            <td>${esc(row.last_latency_ms ?? "")}</td>
            <td>${esc(fmtTime(row.last_test_at))}</td>
            <td>${esc(row.status)}</td>
            <td><button onclick="connectRelay('${esc(row.hostname)}')">Connect</button></td>
          </tr>
        `).join("");
      }
      inventoryLog.textContent = [
        inventory?.auto_guard_log_tail || "",
        inventory?.fast_log_tail || "",
        inventory?.scan_log_tail || "",
        inventory?.daemon_log_tail || ""
      ].filter(Boolean).join("\\n") || "No inventory log yet.";
    }

    async function sleep(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function api(path, options = {}, retries = 2) {
      let lastError;
      for (let attempt = 0; attempt <= retries; attempt++) {
        try {
          const res = await fetch(path, Object.assign({ cache: "no-store" }, options));
          const data = await res.json();
          if (!res.ok || data.ok === false) throw new Error(data.error || "Request failed");
          return data;
        } catch (err) {
          lastError = err;
          if (attempt < retries) await sleep(500 + attempt * 700);
        }
      }
      throw lastError;
    }

    let refreshInFlight = false;
    async function refresh() {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        const data = await api("/api/state");
        const guardMode = data.inventory?.auto_guard_supervision?.mode;
        const autoRunning = data.inventory?.auto_guard_running;
        const guardLocked = Boolean(data.inventory?.auto_guard_control_lock);
        dot.className = "dot " + (guardLocked ? "warning" : guardMode === "launchagent" ? "running" : autoRunning ? "warning" : "stopped");
        statusText.textContent = guardLocked
          ? "Auto Guard paused by maintenance lock"
          : guardMode === "launchagent"
            ? "Auto Guard running under LaunchAgent"
            : autoRunning
              ? "Auto Guard running without LaunchAgent"
              : "Auto Guard paused";
        mullvadStatus.textContent = data.mullvad_status || "No status.";
        renderTraffic(data.traffic || {});
        renderConnection(data.connection || {}, data.latency || {}, data.current_relay_history || {});
        api("/api/latency", {}, 0)
          .then(latencyData => renderLatency(latencyData.latency || latencyData || {}))
          .catch(() => {});
        log.textContent = data.inventory?.auto_guard_log_tail || "No Auto Guard log yet.";
        renderAutoGuard(data.inventory || {});
        renderInventory(data.inventory || {});
      } catch (err) {
        dot.className = "dot stopped";
        statusText.textContent = "Panel service reconnecting: " + err.message;
      } finally {
        refreshInFlight = false;
      }
    }

    async function withBusy(button, fn) {
      button.disabled = true;
      try { await fn(); }
      catch (err) { alert(err.message); }
      finally { button.disabled = false; }
    }

    refreshBtn.addEventListener("click", refresh);

    previewBtn.addEventListener("click", () => withBusy(previewBtn, async () => {
      preview.textContent = "Loading...";
      const data = await api("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings())
      });
      preview.textContent = data.output || "No matching relays.";
    }));

    refreshInventoryBtn.addEventListener("click", () => withBusy(refreshInventoryBtn, async () => {
      await api("/api/inventory/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ update: false })
      });
      await refresh();
    }));

    fastRankBtn.addEventListener("click", () => withBusy(fastRankBtn, async () => {
      await api("/api/inventory/fast-rank", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings())
      });
      await refresh();
    }));

    stopFastRankBtn.addEventListener("click", () => withBusy(stopFastRankBtn, async () => {
      await api("/api/inventory/stop-fast-rank", { method: "POST" });
      await refresh();
    }));

    verifyPoolBtn.addEventListener("click", () => withBusy(verifyPoolBtn, async () => {
      if (!confirm("True Test Pool will switch Mullvad through fast candidates, measure real speed, then restore the previous relay. Continue?")) return;
      await api("/api/inventory/verify-pool", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings())
      });
      await refresh();
    }));

    stopScanBtn.addEventListener("click", () => withBusy(stopScanBtn, async () => {
      await api("/api/inventory/stop-scan", { method: "POST" });
      await refresh();
    }));

    startAutoGuardBtn.addEventListener("click", () => withBusy(startAutoGuardBtn, async () => {
      if (!confirm("Auto Guard will monitor the current relay and may switch to verified backups when it becomes slow or unusable. Continue?")) return;
      await api("/api/inventory/start-auto-guard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings())
      });
      await refresh();
    }));

    stopAutoGuardBtn.addEventListener("click", () => withBusy(stopAutoGuardBtn, async () => {
      if (!confirm("Stop Auto Guard means you do not want VPN protection now. It will stop the LaunchAgent and disconnect Mullvad. Continue?")) return;
      await api("/api/inventory/stop-auto-guard", { method: "POST" });
      await refresh();
    }));

    stopAllBtn.addEventListener("click", () => withBusy(stopAllBtn, async () => {
      if (!confirm("Stop All Tasks will also stop Auto Guard and disconnect Mullvad. Continue?")) return;
      await api("/api/inventory/stop-all", { method: "POST" });
      await refresh();
    }));

    window.connectRelay = async function(hostname) {
      if (!confirm(`Connect Mullvad to ${hostname}?`)) return;
      await api("/api/inventory/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hostname })
      }, 0);
      await refresh();
    };

    refresh();
    setInterval(refresh, 4000);
  </script>
</body>
</html>
"""


def command_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = APP_PATH
    env["PYTHONUNBUFFERED"] = "1"
    return env


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        handler.close_connection = True


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str) -> None:
    data = body.encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        handler.close_connection = True


def tail(path: Path, max_bytes: int = 12000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        return fh.read().decode("utf-8", errors="replace")


def read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


def read_pid(path: Path = PID_PATH) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def pid_running(pid: Optional[int], needle: str) -> bool:
    if not pid:
        return False
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=", "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.35,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    line = proc.stdout.strip()
    if not line or line.startswith("Z"):
        return False
    return needle in line


def pid_running_any(pid: Optional[int], needles: List[str]) -> bool:
    return any(pid_running(pid, needle) for needle in needles)


def launchctl_service_info(label: str) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=0.7,
        )
    except Exception as exc:
        return {"loaded": False, "running": False, "pid": None, "state": "unknown", "error": str(exc)}
    if proc.returncode != 0:
        return {
            "loaded": False,
            "running": False,
            "pid": None,
            "state": "not loaded",
            "error": proc.stdout.strip(),
        }
    state_match = re.search(r"^\s*state = (.+)$", proc.stdout, re.MULTILINE)
    pid_match = re.search(r"^\s*pid = (\d+)$", proc.stdout, re.MULTILINE)
    state = state_match.group(1).strip() if state_match else "loaded"
    pid = int(pid_match.group(1)) if pid_match else None
    running = bool(pid_running(pid, "inventory auto-guard")) or state == "running"
    return {
        "loaded": True,
        "running": running,
        "pid": pid,
        "state": state,
        "error": None,
    }


def auto_guard_supervision() -> Dict[str, Any]:
    launch = launchctl_service_info(LAUNCH_AUTO_GUARD_LABEL)
    local_pid = read_pid(AUTO_GUARD_PID_PATH)
    local_running = pid_running(local_pid, "inventory auto-guard")
    launch_pid = launch.get("pid")
    launch_running = bool(launch.get("running"))
    mode = "launchagent" if launch_running else "unsupervised" if local_running else "paused"
    return {
        "mode": mode,
        "running": launch_running or local_running,
        "launch_loaded": bool(launch.get("loaded")),
        "launch_running": launch_running,
        "launch_pid": launch_pid,
        "launch_state": launch.get("state"),
        "local_running": local_running,
        "local_pid": local_pid if local_running else None,
        "error": launch.get("error"),
    }


def auto_guard_supervision_snapshot() -> Dict[str, Any]:
    launch_pid = read_pid(LAUNCH_AUTO_GUARD_PID_PATH)
    local_pid = read_pid(AUTO_GUARD_PID_PATH)
    launch_running = pid_alive(launch_pid)
    local_running = pid_alive(local_pid)
    mode = "launchagent" if launch_running else "unsupervised" if local_running else "paused"
    return {
        "mode": mode,
        "running": launch_running or local_running,
        "launch_loaded": LAUNCH_AUTO_GUARD_PLIST.exists(),
        "launch_running": launch_running,
        "launch_pid": launch_pid if launch_running else None,
        "launch_state": "running" if launch_running else "unknown",
        "local_running": local_running,
        "local_pid": local_pid if local_running else None,
        "error": None,
    }


def monitor_running() -> bool:
    running = pid_running(read_pid(PID_PATH), "mullvad_speed_guard.py watch")
    if not running:
        PID_PATH.unlink(missing_ok=True)
    return running


def scan_running() -> bool:
    running = pid_running_any(read_pid(SCAN_PID_PATH), ["inventory scan", "inventory verify-pool"])
    if not running:
        SCAN_PID_PATH.unlink(missing_ok=True)
    return running


def fast_running() -> bool:
    running = pid_running(read_pid(FAST_RANK_PID_PATH), "inventory fast-rank")
    if not running:
        FAST_RANK_PID_PATH.unlink(missing_ok=True)
    return running


def daemon_running() -> bool:
    running = pid_running(read_pid(DAEMON_PID_PATH), "inventory daemon")
    if not running:
        DAEMON_PID_PATH.unlink(missing_ok=True)
    return running


def auto_guard_running() -> bool:
    status = auto_guard_supervision()
    running = bool(status.get("running"))
    if not running:
        AUTO_GUARD_PID_PATH.unlink(missing_ok=True)
    if not status.get("launch_running"):
        LAUNCH_AUTO_GUARD_PID_PATH.unlink(missing_ok=True)
    return running


def stop_process(pid_path: Path, needle: str) -> bool:
    pid = read_pid(pid_path)
    if not pid_running(pid, needle):
        pid_path.unlink(missing_ok=True)
        return False
    assert pid is not None
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    pid_path.unlink(missing_ok=True)
    return True


def disconnect_mullvad() -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            ["mullvad", "disconnect", "--wait"],
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        proc = subprocess.run(
            ["mullvad", "disconnect"],
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
    return {
        "returncode": proc.returncode,
        "output": proc.stdout.strip(),
        "status": mullvad_status(),
    }


def stop_launch_auto_guard(disconnect_vpn: bool = False) -> Dict[str, Any]:
    stopped = False
    if LAUNCH_AUTO_GUARD_PLIST.exists():
        try:
            proc = subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(LAUNCH_AUTO_GUARD_PLIST)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            stopped = proc.returncode == 0
        except Exception:
            stopped = False
        LAUNCH_AUTO_GUARD_PLIST.unlink(missing_ok=True)
    pid = read_pid(LAUNCH_AUTO_GUARD_PID_PATH)
    if pid_running(pid, "inventory auto-guard"):
        try:
            assert pid is not None
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            pass
    LAUNCH_AUTO_GUARD_PID_PATH.unlink(missing_ok=True)
    local_pid = read_pid(AUTO_GUARD_PID_PATH)
    if pid_running(local_pid, "inventory auto-guard"):
        stop_process(AUTO_GUARD_PID_PATH, "inventory auto-guard")
        stopped = True
    disconnect_result = disconnect_mullvad() if disconnect_vpn else None
    return {"stopped": stopped, "disconnect": disconnect_result, "supervision": auto_guard_supervision()}


def pkill_guard_patterns(patterns: List[str]) -> None:
    for pattern in patterns:
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except Exception:
            pass


def mullvad_status() -> str:
    try:
        proc = subprocess.run(
            ["mullvad", "status", "-v"],
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=0.8,
        )
        return proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return "Mullvad status timed out. The panel is still running."
    except Exception as exc:
        return f"Status failed: {exc}"


def mullvad_status_snapshot(wait_timeout: float = 0.0) -> str:
    """Return cached Mullvad status; request handlers must not shell out."""
    return LAST_MULLVAD_STATUS_TEXT or "Mullvad status pending. The panel is still running."


def load_traffic_total_state() -> Dict[str, Any]:
    global TRAFFIC_TOTAL_STATE
    if TRAFFIC_TOTAL_STATE:
        return TRAFFIC_TOTAL_STATE
    try:
        payload = json.loads(TRAFFIC_TOTALS_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            TRAFFIC_TOTAL_STATE = payload
    except FileNotFoundError:
        TRAFFIC_TOTAL_STATE = {}
    except Exception:
        TRAFFIC_TOTAL_STATE = {}
    return TRAFFIC_TOTAL_STATE


def save_traffic_total_state(state: Dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TRAFFIC_TOTALS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TRAFFIC_TOTALS_PATH)


def traffic_totals_fallback(error: str, interface: Optional[str] = None) -> Dict[str, Any]:
    with TRAFFIC_TOTAL_LOCK:
        state = dict(load_traffic_total_state())
    return {
        "ok": False,
        "interface": interface,
        "download_bytes": int(state.get("total_download_bytes") or 0),
        "upload_bytes": int(state.get("total_upload_bytes") or 0),
        "interface_download_bytes": None,
        "interface_upload_bytes": None,
        "sampled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counter_scope": "persistent_cumulative",
        "error": error,
    }


def cumulative_traffic_stats(interface: str, raw_download: int, raw_upload: int) -> Dict[str, Any]:
    sampled_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with TRAFFIC_TOTAL_LOCK:
        state = dict(load_traffic_total_state())
        previous_interface = state.get("last_interface")
        previous_down = state.get("last_interface_download_bytes")
        previous_up = state.get("last_interface_upload_bytes")
        total_down = int(state.get("total_download_bytes") or 0)
        total_up = int(state.get("total_upload_bytes") or 0)
        initialized = bool(state.get("initialized"))

        if not initialized:
            delta_down = max(0, raw_download)
            delta_up = max(0, raw_upload)
            reset_reason = "initialized from current interface counters"
        elif previous_interface == interface and previous_down is not None and previous_up is not None:
            if raw_download >= int(previous_down) and raw_upload >= int(previous_up):
                delta_down = raw_download - int(previous_down)
                delta_up = raw_upload - int(previous_up)
                reset_reason = None
            else:
                delta_down = max(0, raw_download)
                delta_up = max(0, raw_upload)
                reset_reason = "interface counters reset"
        else:
            delta_down = max(0, raw_download)
            delta_up = max(0, raw_upload)
            reset_reason = "interface changed"

        total_down += delta_down
        total_up += delta_up
        state.update(
            {
                "initialized": True,
                "total_download_bytes": total_down,
                "total_upload_bytes": total_up,
                "last_interface": interface,
                "last_interface_download_bytes": raw_download,
                "last_interface_upload_bytes": raw_upload,
                "last_sampled_at": sampled_at,
                "last_delta_download_bytes": delta_down,
                "last_delta_upload_bytes": delta_up,
                "last_reset_reason": reset_reason,
            }
        )
        TRAFFIC_TOTAL_STATE.clear()
        TRAFFIC_TOTAL_STATE.update(state)
        save_traffic_total_state(state)

    return {
        "ok": True,
        "interface": interface,
        "download_bytes": total_down,
        "upload_bytes": total_up,
        "interface_download_bytes": raw_download,
        "interface_upload_bytes": raw_upload,
        "last_delta_download_bytes": delta_down,
        "last_delta_upload_bytes": delta_up,
        "counter_scope": "persistent_cumulative",
        "reset_reason": reset_reason,
        "sampled_at": sampled_at,
    }


def vpn_traffic_stats(status_text: Optional[str] = None) -> Dict[str, Any]:
    status_text = status_text if status_text is not None else mullvad_status()
    interface_match = re.search(r"Tunnel interface:\s+([A-Za-z0-9_.-]+)", status_text)
    interface = interface_match.group(1) if interface_match else None
    if not interface:
        return traffic_totals_fallback("VPN tunnel interface not detected.")

    try:
        netstat_proc = subprocess.run(
            ["netstat", "-ibn"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=0.5,
        )
    except Exception as exc:
        return traffic_totals_fallback(f"netstat failed: {exc}", interface=interface)

    for line in netstat_proc.stdout.splitlines():
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
        return cumulative_traffic_stats(interface, download_bytes, upload_bytes)

    return traffic_totals_fallback("Interface byte counters not found.", interface=interface)


def traffic_snapshot() -> Dict[str, Any]:
    if LAST_TRAFFIC_STATE:
        return dict(LAST_TRAFFIC_STATE)
    return {
        "ok": False,
        "interface": None,
        "download_bytes": 0,
        "upload_bytes": 0,
        "interface_download_bytes": None,
        "interface_upload_bytes": None,
        "sampled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counter_scope": "persistent_cumulative",
        "error": "traffic cache pending",
    }


def refresh_mullvad_cache_loop() -> None:
    global LAST_MULLVAD_STATUS_TEXT, LAST_TRAFFIC_STATE
    while True:
        try:
            status_text = mullvad_status()
            if status_text and "timed out" not in status_text.lower() and not status_text.startswith("Status failed:"):
                LAST_MULLVAD_STATUS_TEXT = status_text
                LAST_TRAFFIC_STATE = vpn_traffic_stats(status_text)
            elif status_text and not LAST_MULLVAD_STATUS_TEXT:
                LAST_MULLVAD_STATUS_TEXT = status_text
        except Exception:
            pass
        time.sleep(2)


def connection_summary(status_text: str) -> Dict[str, Any]:
    lines = status_text.splitlines()
    state = lines[0].strip() if lines else "Unknown"
    relay_match = re.search(r"Relay:\s+([a-z0-9-]+)", status_text)
    visible_match = re.search(r"Visible location:\s+(.+)", status_text)
    interface_match = re.search(r"Tunnel interface:\s+([A-Za-z0-9_.-]+)", status_text)
    return {
        "state": state,
        "relay": relay_match.group(1) if relay_match else None,
        "visible_location": visible_match.group(1).strip() if visible_match else None,
        "interface": interface_match.group(1) if interface_match else None,
    }


def relay_history(hostname: Optional[str]) -> Optional[Dict[str, Any]]:
    if not hostname:
        return None
    try:
        conn = sqlite3.connect(str(relay_inventory.DB_PATH), timeout=0.05)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM relays WHERE hostname=?", (hostname,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    return {
        "hostname": row["hostname"],
        "status": row["status"],
        "last_test_at": row["last_test_at"],
        "fast_probe_at": row["fast_probe_at"],
        "last_mbps": row["last_mbps"],
        "best_mbps": row["best_mbps"],
    }


def compact_relay_row(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "hostname",
        "country",
        "city",
        "status",
        "last_mbps",
        "best_mbps",
        "last_latency_ms",
        "last_test_at",
        "fast_latency_ms",
        "fast_probe_at",
        "fast_reachable",
        "success_count",
        "consecutive_failures",
    ]
    return {key: row.get(key) for key in keys if key in row}


def compact_rows(rows: Any, limit: int) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [compact_relay_row(row) for row in rows[:limit] if isinstance(row, dict)]


def latency_snapshot(probe: bool = False) -> Dict[str, Any]:
    global LAST_LATENCY_STATE
    now_mono = time.monotonic()
    if (
        LAST_LATENCY_STATE
        and now_mono - float(LAST_LATENCY_STATE.get("sampled_monotonic", 0)) < 60
        and (not probe or LAST_LATENCY_STATE.get("reason") != "latency probe deferred")
    ):
        return dict(LAST_LATENCY_STATE)

    sample: Dict[str, Any] = {
        "ok": False,
        "latency_ms": None,
        "reason": "latency probe deferred",
        "sampled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sampled_monotonic": now_mono,
    }
    if not probe:
        LAST_LATENCY_STATE = sample
        return dict(sample)
    try:
        start = time.monotonic()
        with socket.create_connection(("1.1.1.1", 443), timeout=0.8):
            latency = (time.monotonic() - start) * 1000
            sample["ok"] = True
            sample["latency_ms"] = round(latency, 1)
            sample["reason"] = f"cached at {sample['sampled_at']}"
    except socket.timeout:
        sample["reason"] = "latency probe timed out"
    except Exception as exc:
        sample["reason"] = f"latency probe error: {exc}"
    LAST_LATENCY_STATE = sample
    return dict(sample)


def clean_number(value: Any, default: str, allow_float: bool = False) -> str:
    text = str(value if value is not None else default).strip()
    try:
        number = float(text) if allow_float else int(float(text))
    except ValueError:
        return default
    if number < 0:
        return default
    return str(number if allow_float else int(number))


def clean_csv(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).lower()
    parts = []
    for part in text.split(","):
        item = part.strip()
        if item and all(ch.isalnum() or ch == "-" for ch in item):
            parts.append(item)
    return ",".join(parts) or default


def clean_url_csv(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    parts = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        if re.match(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$", item):
            parts.append(item)
    return ",".join(parts) or default


def watch_args(settings: Dict[str, Any]) -> List[str]:
    mode = str(settings.get("health_mode", "adaptive")).strip().lower()
    if mode not in {"adaptive", "latency", "status", "speed"}:
        mode = "adaptive"
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "watch",
        "--health-mode",
        mode,
        "--speed-check-every",
        clean_number(settings.get("speed_check_every"), "0"),
        "--interval",
        clean_number(settings.get("interval"), "30"),
        "--min-mbps",
        clean_number(settings.get("min_mbps"), "0.5", allow_float=True),
        "--preferred-mbps",
        clean_number(settings.get("preferred_mbps"), "8", allow_float=True),
        "--max-latency-ms",
        clean_number(settings.get("max_latency_ms"), "2500", allow_float=True),
        "--max-candidates",
        clean_number(settings.get("max_candidates"), "20"),
    ]
    countries = clean_csv(settings.get("countries"), "")
    if countries:
        args.extend(["--countries", countries])
    else:
        args.append("--all-countries")
    return args


def preview_args(settings: Dict[str, Any]) -> List[str]:
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "scan",
        "--dry-run",
        "--no-update",
        "--max-candidates",
        clean_number(settings.get("max_candidates"), "20"),
    ]
    countries = clean_csv(settings.get("countries"), "")
    if countries:
        args.extend(["--countries", countries])
    else:
        args.append("--all-countries")
    return args


def start_monitor(settings: Dict[str, Any]) -> int:
    if monitor_running():
        return read_pid() or 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with WATCH_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting monitor\n")
        proc = subprocess.Popen(
            watch_args(settings),
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def stop_monitor() -> bool:
    pid = read_pid(PID_PATH)
    if not pid_running(pid, "mullvad_speed_guard.py watch"):
        PID_PATH.unlink(missing_ok=True)
        return False
    assert pid is not None
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    PID_PATH.unlink(missing_ok=True)
    return True


def clean_scope(value: Any) -> str:
    scope = str(value if value is not None else "full").strip().lower()
    return scope if scope in {"full", "active", "unknown", "untested", "abandoned", "top", "fast"} else "full"


def bool_setting(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def write_auto_guard_control_lock(reason: str, ttl_seconds: int = 900, pid: Optional[int] = None) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    expires_at = time.time() + max(30, int(ttl_seconds))
    payload = {
        "reason": reason,
        "pid": pid or os.getpid(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "expires_at_epoch": expires_at,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    for path in {AUTO_GUARD_CONTROL_LOCK_PATH, LAUNCH_CONTROL_LOCK_PATH}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")


def clear_auto_guard_control_lock() -> None:
    AUTO_GUARD_CONTROL_LOCK_PATH.unlink(missing_ok=True)
    LAUNCH_CONTROL_LOCK_PATH.unlink(missing_ok=True)


def read_auto_guard_control_lock() -> Optional[Dict[str, Any]]:
    for path in [LAUNCH_CONTROL_LOCK_PATH, AUTO_GUARD_CONTROL_LOCK_PATH]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except Exception:
            path.unlink(missing_ok=True)
            continue
        pid = payload.get("pid")
        if pid:
            try:
                os.kill(int(pid), 0)
            except (ProcessLookupError, ValueError):
                path.unlink(missing_ok=True)
                continue
            except PermissionError:
                pass
        expires_at = float(payload.get("expires_at_epoch") or 0)
        if expires_at and time.time() > expires_at:
            path.unlink(missing_ok=True)
            continue
        return payload
    return None


def inventory_state() -> Dict[str, Any]:
    source = dict(LAST_INVENTORY_STATE) if LAST_INVENTORY_STATE else {
        "counts": {"total": 0, "working": 0, "no_speed": 0, "abandoned": 0, "unknown": 0},
        "top5": [],
        "fast_top5": [],
        "ready_top5": [],
        "whitelist_top5": [],
        "recent": [],
        "error": "inventory summary pending",
    }
    data = {
        "counts": source.get("counts", {}),
        "top5": compact_rows(source.get("top5"), 5),
        "fast_top5": compact_rows(source.get("fast_top5"), 10),
        "ready_top5": compact_rows(source.get("ready_top5"), 5),
        "whitelist_top5": compact_rows(source.get("whitelist_top5"), 5),
        "recent": source.get("recent", [])[:10] if isinstance(source.get("recent"), list) else [],
    }
    if source.get("error"):
        data["error"] = source.get("error")
    data["scan_running"] = pid_alive(read_pid(SCAN_PID_PATH))
    data["scan_pid"] = read_pid(SCAN_PID_PATH)
    data["fast_running"] = pid_alive(read_pid(FAST_RANK_PID_PATH))
    data["fast_pid"] = read_pid(FAST_RANK_PID_PATH)
    data["daemon_running"] = pid_alive(read_pid(DAEMON_PID_PATH))
    data["daemon_pid"] = read_pid(DAEMON_PID_PATH)
    supervision = auto_guard_supervision_snapshot()
    data["auto_guard_supervision"] = supervision
    data["auto_guard_running"] = bool(supervision.get("running"))
    data["auto_guard_pid"] = supervision.get("launch_pid") or supervision.get("local_pid")
    data["auto_guard_control_lock"] = read_auto_guard_control_lock()
    data["fast_log_tail"] = tail(FAST_RANK_LOG_PATH, max_bytes=1200)
    data["scan_log_tail"] = tail(INVENTORY_LOG_PATH, max_bytes=1200)
    data["daemon_log_tail"] = tail(INVENTORY_DAEMON_LOG_PATH, max_bytes=1200)
    data["auto_guard_log_tail"] = "\n".join(
        part for part in [tail(AUTO_GUARD_LOG_PATH, max_bytes=1200), tail(LAUNCH_AUTO_GUARD_LOG_PATH, max_bytes=5000)] if part
    )
    return data


def refresh_inventory_cache_loop() -> None:
    global LAST_INVENTORY_STATE
    while True:
        try:
            LAST_INVENTORY_STATE = dict(relay_inventory.summary())
        except Exception:
            pass
        time.sleep(15)


def pool_args(settings: Dict[str, Any]) -> List[str]:
    return [
        "--pool-size",
        clean_number(settings.get("pool_size"), "5"),
        "--ready-target",
        clean_number(settings.get("ready_target"), "3"),
        "--candidate-limit",
        clean_number(settings.get("candidate_limit"), "25"),
        "--min-working-mbps",
        clean_number(settings.get("min_working_mbps"), "0.05", allow_float=True),
        "--abandon-after",
        clean_number(settings.get("abandon_after"), "3"),
        "--fast-workers",
        clean_number(settings.get("fast_workers"), "64"),
        "--fast-timeout",
        clean_number(settings.get("fast_timeout"), "1.2", allow_float=True),
        "--fast-port",
        clean_number(settings.get("fast_port"), "443"),
    ]


def start_fast_rank(settings: Dict[str, Any]) -> int:
    if fast_running():
        return read_pid(FAST_RANK_PID_PATH) or 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "inventory",
        "fast-rank",
        "--workers",
        clean_number(settings.get("fast_workers"), "64"),
        "--timeout",
        clean_number(settings.get("fast_timeout"), "1.2", allow_float=True),
        "--port",
        clean_number(settings.get("fast_port"), "443"),
        "--limit",
        clean_number(settings.get("fast_limit"), "0"),
        "--json",
    ]
    with FAST_RANK_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting fast rank\n")
        proc = subprocess.Popen(
            args,
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    FAST_RANK_PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def start_verify_pool(settings: Dict[str, Any]) -> int:
    if scan_running():
        return read_pid(SCAN_PID_PATH) or 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    write_auto_guard_control_lock("manual true-test pool", ttl_seconds=1800)
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "inventory",
        "verify-pool",
        *pool_args(settings),
        "--json",
    ]
    if bool_setting(settings.get("connect_best"), False):
        args.append("--connect-best")
    if bool_setting(settings.get("no_restore"), False):
        args.append("--no-restore")
    with INVENTORY_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting true-test pool\n")
        proc = subprocess.Popen(
            args,
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    SCAN_PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    write_auto_guard_control_lock("manual true-test pool", ttl_seconds=1800, pid=proc.pid)
    return proc.pid


def start_inventory_scan(settings: Dict[str, Any]) -> int:
    if scan_running():
        return read_pid(SCAN_PID_PATH) or 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    write_auto_guard_control_lock("manual inventory scan", ttl_seconds=3600)
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "inventory",
        "scan",
        "--scope",
        clean_scope(settings.get("scope")),
        "--limit",
        clean_number(settings.get("limit"), "0"),
        "--min-working-mbps",
        clean_number(settings.get("min_working_mbps"), "0.05", allow_float=True),
        "--abandon-after",
        clean_number(settings.get("abandon_after"), "3"),
    ]
    if bool_setting(settings.get("include_abandoned"), True):
        args.append("--include-abandoned")
    if bool_setting(settings.get("dry_run"), False):
        args.append("--dry-run")
    with INVENTORY_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting inventory scan\n")
        proc = subprocess.Popen(
            args,
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    SCAN_PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    write_auto_guard_control_lock("manual inventory scan", ttl_seconds=3600, pid=proc.pid)
    return proc.pid


def start_inventory_daemon(settings: Dict[str, Any]) -> int:
    if daemon_running():
        return read_pid(DAEMON_PID_PATH) or 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        PYTHON,
        str(GUARD_SCRIPT),
        "inventory",
        "daemon",
        "--active-interval",
        clean_number(settings.get("active_interval"), "1800"),
        "--full-interval",
        clean_number(settings.get("full_interval"), "21600"),
        "--idle-seconds",
        clean_number(settings.get("idle_seconds"), "900"),
        "--active-limit",
        clean_number(settings.get("active_limit"), "20"),
        "--full-limit",
        clean_number(settings.get("full_limit"), "0"),
        "--min-working-mbps",
        clean_number(settings.get("min_working_mbps"), "0.05", allow_float=True),
        "--abandon-after",
        clean_number(settings.get("abandon_after"), "3"),
    ]
    with INVENTORY_DAEMON_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting inventory daemon\n")
        proc = subprocess.Popen(
            args,
            cwd=str(APP_DIR),
            env=command_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    DAEMON_PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    write_auto_guard_control_lock("inventory daemon", ttl_seconds=86400, pid=proc.pid)
    return proc.pid


def auto_guard_args(settings: Dict[str, Any]) -> List[str]:
    mode = str(settings.get("health_mode", "adaptive")).strip().lower()
    if mode not in {"adaptive", "latency", "status", "speed"}:
        mode = "adaptive"
    args: List[str] = [
        PYTHON,
        str(GUARD_SCRIPT),
        "inventory",
        "auto-guard",
        "--interval",
        clean_number(settings.get("interval"), "30"),
        "--health-mode",
        mode,
        "--speed-check-every",
        clean_number(settings.get("speed_check_every"), "600"),
        "--min-mbps",
        clean_number(settings.get("min_mbps"), "0.5", allow_float=True),
        "--preferred-mbps",
        clean_number(settings.get("preferred_mbps"), "8", allow_float=True),
        "--max-latency-ms",
        clean_number(settings.get("max_latency_ms"), "2500", allow_float=True),
        "--fast-rank-interval",
        clean_number(settings.get("fast_rank_interval"), "3600"),
        "--failure-threshold",
        clean_number(settings.get("failure_threshold"), "3"),
        "--backup-attempt-limit",
        clean_number(settings.get("backup_attempt_limit"), "3"),
        "--backup-retry-cooldown",
        clean_number(settings.get("backup_retry_cooldown"), "900"),
        "--quick-connect-timeout",
        clean_number(settings.get("quick_connect_timeout"), "12"),
        "--disconnect-wait-timeout",
        clean_number(settings.get("disconnect_wait_timeout"), "5"),
        "--url-check-timeout",
        clean_number(settings.get("url_check_timeout"), "10"),
        "--url-emergency-min-failed",
        clean_number(settings.get("url_emergency_min_failed"), "0"),
        "--connecting-grace",
        clean_number(settings.get("connecting_grace"), "45"),
        "--daemon-retry-cooldown",
        clean_number(settings.get("daemon_retry_cooldown"), "180"),
        "--passive-period",
        clean_number(settings.get("passive_period"), "900"),
        "--passive-min-down-mbps",
        clean_number(settings.get("passive_min_down_mbps"), "8", allow_float=True),
        "--idle-refresh-after",
        clean_number(settings.get("idle_refresh_after"), "1800"),
        "--idle-refresh-user-idle-seconds",
        clean_number(settings.get("idle_refresh_user_idle_seconds"), "1800"),
        "--idle-refresh-activity-threshold-bytes",
        clean_number(settings.get("idle_refresh_activity_threshold_bytes"), "262144"),
        "--idle-refresh-batch-size",
        clean_number(settings.get("idle_refresh_batch_size"), "1"),
        *pool_args(settings),
    ]
    args.extend(["--recovery-search-limit", clean_number(settings.get("recovery_search_limit"), "50")])
    args.extend(["--emergency-attempt-limit", clean_number(settings.get("emergency_attempt_limit"), "12")])
    if bool_setting(settings.get("no_idle_refresh"), False):
        args.append("--no-idle-refresh")
    url_checks = clean_url_csv(settings.get("url_checks"), "")
    if url_checks:
        args.extend(["--url-checks", url_checks])
    if bool_setting(settings.get("refresh_pool_on_failure"), False):
        args.append("--refresh-pool-on-failure")
        args.extend(["--refresh-pool-cooldown", clean_number(settings.get("refresh_pool_cooldown"), "1800")])
    return args


def write_launch_auto_guard_plist(settings: Dict[str, Any]) -> None:
    LAUNCH_AUTO_GUARD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_AUTO_GUARD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LAUNCH_AUTO_GUARD_LABEL,
        "ProgramArguments": auto_guard_args(settings),
        "WorkingDirectory": str(APP_DIR),
        "EnvironmentVariables": {
            "PATH": APP_PATH,
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(LAUNCH_AUTO_GUARD_LOG_PATH),
        "StandardErrorPath": str(LAUNCH_AUTO_GUARD_LOG_PATH),
    }
    with LAUNCH_AUTO_GUARD_PLIST.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=False)


def launch_auto_guard_args() -> List[str]:
    try:
        with LAUNCH_AUTO_GUARD_PLIST.open("rb") as fh:
            payload = plistlib.load(fh)
        args = payload.get("ProgramArguments", [])
        return [str(item) for item in args] if isinstance(args, list) else []
    except Exception:
        return []


def start_auto_guard(settings: Dict[str, Any]) -> int:
    status = auto_guard_supervision()
    desired_args = auto_guard_args(settings)
    if status.get("mode") == "launchagent" and launch_auto_guard_args() == desired_args:
        return int(status.get("launch_pid") or read_pid(AUTO_GUARD_PID_PATH) or 0)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if status.get("local_running"):
        stop_process(AUTO_GUARD_PID_PATH, "inventory auto-guard")
    write_launch_auto_guard_plist(settings)
    with AUTO_GUARD_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting auto guard via LaunchAgent\n")
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(LAUNCH_AUTO_GUARD_PLIST)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LAUNCH_AUTO_GUARD_PLIST)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=True,
    )
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AUTO_GUARD_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    for _ in range(10):
        launched = auto_guard_supervision()
        if launched.get("launch_running"):
            return int(launched.get("launch_pid") or read_pid(AUTO_GUARD_PID_PATH) or 0)
        time.sleep(0.5)
    launched = auto_guard_supervision()
    if launched.get("running"):
        return int(launched.get("launch_pid") or launched.get("local_pid") or 0)
    raise RuntimeError(f"Auto Guard LaunchAgent did not start: {launched.get('error') or launched.get('launch_state')}")


def stop_all_switching_tasks(disconnect_vpn: bool = False) -> Dict[str, Any]:
    stop_monitor()
    stop_process(FAST_RANK_PID_PATH, "inventory fast-rank")
    stop_process(SCAN_PID_PATH, "inventory")
    stop_process(DAEMON_PID_PATH, "inventory daemon")
    stop_process(AUTO_GUARD_PID_PATH, "inventory auto-guard")
    guard_stop = stop_launch_auto_guard(disconnect_vpn=disconnect_vpn)
    script = str(GUARD_SCRIPT)
    pkill_guard_patterns(
        [
            f"{script} watch",
            f"{script} inventory verify-pool",
            f"{script} inventory scan",
            f"{script} inventory daemon",
            f"{script} inventory auto-guard",
        ]
    )
    for path in [PID_PATH, FAST_RANK_PID_PATH, SCAN_PID_PATH, DAEMON_PID_PATH, AUTO_GUARD_PID_PATH]:
        path.unlink(missing_ok=True)
    return {"guard": guard_stop}


def stop_manual_conflict_tasks() -> None:
    stop_monitor()
    stop_process(FAST_RANK_PID_PATH, "inventory fast-rank")
    stop_process(SCAN_PID_PATH, "inventory")
    stop_process(DAEMON_PID_PATH, "inventory daemon")
    script = str(GUARD_SCRIPT)
    pkill_guard_patterns(
        [
            f"{script} watch",
            f"{script} inventory verify-pool",
            f"{script} inventory scan",
            f"{script} inventory daemon",
        ]
    )
    for path in [PID_PATH, FAST_RANK_PID_PATH, SCAN_PID_PATH, DAEMON_PID_PATH]:
        path.unlink(missing_ok=True)


def safe_manual_connect(hostname: str) -> Dict[str, Any]:
    previous_relay = relay_inventory.guard.current_relay_hostname()
    previous_state, previous_status = relay_inventory.guard.mullvad_status()
    previous_connected = previous_state.lower().startswith("connected")
    config = {
        "connect_timeout_seconds": 45,
        "quick_connect": True,
        "quick_connect_timeout_seconds": 12,
        "disconnect_wait_timeout_seconds": 5,
        "strict_hostname": True,
    }
    try:
        result = relay_inventory.connect_relay(hostname, config)
        return {
            "requested": hostname,
            "previous_relay": previous_relay,
            "restored": False,
            "result": result,
        }
    except Exception as exc:
        restore_error = None
        restored = False
        if previous_connected and previous_relay:
            try:
                restore_config = dict(config)
                restore_config["strict_hostname"] = False
                relay_inventory.connect_relay(previous_relay, restore_config)
                restored = True
            except Exception as restore_exc:
                restore_error = str(restore_exc)
        raise RuntimeError(
            f"Manual connect to {hostname} failed: {exc}. "
            f"Previous relay={previous_relay or 'unknown'}; restored={restored}. "
            f"Restore error={restore_error or 'none'}. Previous status: {previous_status}"
        ) from exc


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        self.request.settimeout(3)
        super().setup()

    def log_message(self, fmt: str, *args: Any) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with SERVER_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write((fmt % args) + "\n")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            text_response(self, 200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/ping":
            json_response(self, 200, {"ok": True, "version": PANEL_VERSION})
        elif path == "/api/latency":
            json_response(self, 200, {"ok": True, "latency": latency_snapshot(probe=True)})
        elif path == "/api/state":
            status_text = mullvad_status_snapshot()
            connection = connection_summary(status_text)
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "version": PANEL_VERSION,
                    "monitor_running": pid_alive(read_pid(PID_PATH)),
                    "monitor_pid": read_pid(PID_PATH),
                    "mullvad_status": status_text,
                    "connection": connection,
                    "current_relay_history": relay_history(connection.get("relay")),
                    "latency": latency_snapshot(probe=False),
                    "traffic": traffic_snapshot(),
                    "log_tail": tail(WATCH_LOG_PATH),
                    "inventory": inventory_state(),
                },
            )
        elif path == "/readme":
            body = html.escape(README_PATH.read_text(encoding="utf-8"))
            text_response(self, 200, f"<pre>{body}</pre>", "text/html; charset=utf-8")
        else:
            json_response(self, 404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            settings = read_json_body(self)
            if path == "/api/start":
                pid = start_monitor(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/stop":
                stopped = stop_monitor()
                json_response(self, 200, {"ok": True, "stopped": stopped})
            elif path == "/api/preview":
                proc = subprocess.run(
                    preview_args(settings),
                    cwd=str(APP_DIR),
                    env=command_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60,
                )
                json_response(self, 200, {"ok": True, "output": proc.stdout.strip()})
            elif path == "/api/inventory/refresh":
                result = relay_inventory.sync_relays(update=bool_setting(settings.get("update"), False))
                json_response(self, 200, {"ok": True, "inventory": result})
            elif path == "/api/inventory/fast-rank":
                pid = start_fast_rank(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/inventory/stop-fast-rank":
                stopped = stop_process(FAST_RANK_PID_PATH, "inventory fast-rank")
                json_response(self, 200, {"ok": True, "stopped": stopped})
            elif path == "/api/inventory/verify-pool":
                pid = start_verify_pool(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/inventory/scan":
                pid = start_inventory_scan(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/inventory/stop-scan":
                stopped = stop_process(SCAN_PID_PATH, "inventory")
                clear_auto_guard_control_lock()
                json_response(self, 200, {"ok": True, "stopped": stopped})
            elif path == "/api/inventory/start-daemon":
                pid = start_inventory_daemon(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/inventory/stop-daemon":
                stopped = stop_process(DAEMON_PID_PATH, "inventory daemon")
                clear_auto_guard_control_lock()
                json_response(self, 200, {"ok": True, "stopped": stopped})
            elif path == "/api/inventory/start-auto-guard":
                pid = start_auto_guard(settings)
                json_response(self, 200, {"ok": True, "pid": pid})
            elif path == "/api/inventory/stop-auto-guard":
                stopped = stop_launch_auto_guard(disconnect_vpn=True)
                json_response(self, 200, {"ok": True, **stopped})
            elif path == "/api/inventory/stop-all":
                stopped = stop_all_switching_tasks(disconnect_vpn=True)
                json_response(self, 200, {"ok": True, "stopped": True, **stopped})
            elif path == "/api/inventory/connect":
                hostname = str(settings.get("hostname", "")).strip().lower()
                stop_manual_conflict_tasks()
                write_auto_guard_control_lock("manual relay connect", ttl_seconds=120)
                try:
                    result = safe_manual_connect(hostname)
                finally:
                    clear_auto_guard_control_lock()
                json_response(self, 200, {"ok": True, "result": result})
            else:
                json_response(self, 404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            json_response(self, 500, {"ok": False, "error": str(exc)})


class PanelServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 512


class PanelServerIPv6(PanelServer):
    address_family = socket.AF_INET6


def start_background_refreshers() -> None:
    global BACKGROUND_REFRESH_STARTED
    if BACKGROUND_REFRESH_STARTED:
        return
    BACKGROUND_REFRESH_STARTED = True
    threading.Thread(target=refresh_mullvad_cache_loop, name="mullvad-status-cache", daemon=True).start()
    threading.Thread(target=refresh_inventory_cache_loop, name="inventory-cache", daemon=True).start()


def run(port: int) -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    start_background_refreshers()
    try:
        ipv6_server = PanelServerIPv6(("::1", port), Handler)
        threading.Thread(
            target=ipv6_server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="panel-ipv6",
            daemon=True,
        ).start()
        print(f"Mullvad Speed Guard panel also listening at http://[::1]:{port}/", flush=True)
    except OSError as exc:
        print(f"IPv6 loopback panel listener unavailable: {exc}", flush=True)
    server = PanelServer(("127.0.0.1", port), Handler)
    print(f"Mullvad Speed Guard panel running at http://localhost:{port}/", flush=True)
    server.serve_forever(poll_interval=0.05)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    return run(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
