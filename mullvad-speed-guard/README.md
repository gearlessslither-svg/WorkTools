# Mullvad Speed Guard / Mullvad 测速守护

本工具会使用本机的 `mullvad` CLI 监测 VPN 状态、测试候选节点，并在断线、延迟异常或抽样测速低于阈值时重新选择最快节点连接。它不保存账号、密码、真实 IP、浏览记录或个人资产信息。
This tool uses the local `mullvad` CLI to monitor VPN status, test candidate relays, and reconnect to the fastest node when the link drops, latency spikes, or a sampled speed test falls below threshold. It stores no account, password, real IP, browsing history, or personal asset information.

## 适用边界 / Scope and limits

- 支持：Mullvad 节点列表解析、候选节点筛选、逐节点连接测速、轻量心跳监测、低速/断线监测、自动切到最佳节点。
  - Supported: parsing the Mullvad relay list, filtering candidates, per-node connect-and-speed-test, lightweight heartbeat monitoring, low-speed/disconnect detection, and auto-switching to the best node.
- 不支持：创建免费 VPN 基础设施、绕过账号限制、修改 Mullvad 客户端内部文件、保证任何地区/网络下稳定可用。
  - Not supported: creating free VPN infrastructure, bypassing account limits, modifying Mullvad client internals, or guaranteeing stable availability in any region/network.
- 注意：全节点逐个连接测速会打断当前 VPN 连接，并可能耗时很久。默认禁止使用香港节点，因为 ChatGPT / Codex 在香港地区不可用；建议先测日本、新加坡、韩国、台湾、美国西海岸等候选区域。
  - Note: testing every node one by one interrupts the current VPN connection and can take a long time. Hong Kong relays are blocked by default because ChatGPT / Codex is unavailable there; test candidate regions such as Japan, Singapore, Korea, Taiwan, US West Coast, etc.

## 快速使用 / Quick start

### 桌面 GUI 推荐流程 / Recommended desktop GUI flow

双击桌面的 `Mullvad Speed Guard.app` 后，推荐按这个顺序使用：
After double-clicking `Mullvad Speed Guard.app` on the desktop, use it in this order:

1. `Fast Rank All`: 快速探测所有 Mullvad relay 的 TCP 可达性和延迟，不切换 VPN，通常几秒完成，并刷新 `Fast Candidates` 候选。
   - Quickly probe TCP reachability and latency of all Mullvad relays without switching the VPN; usually finishes in seconds and refreshes the `Fast Candidates` list.
2. `True Test Pool`: 真实切到快排候选做下载测速。默认先测 5 个；只要节点能跑出基本速度就会进入历史白名单，越快、越稳定、同一时段越可靠的节点会排得越前。测完会恢复你原来的节点。
   - Actually switch to fast-ranked candidates and run download speed tests. Tests 5 by default; any node that hits a basic speed enters the history whitelist, with faster, more stable, time-of-day-reliable nodes ranked higher. It restores your original node afterward.
3. `Test + Switch`: 当你觉得当前网速太慢时，手动触发一次真实测速、判断候选池、并把 Mullvad 留在当前最好的已验证节点。如果候选没有明显快过当前节点，默认保持当前节点不切换。
   - When the current VPN feels slow, manually trigger one real speed test cycle, evaluate the candidate pool, and leave Mullvad on the best verified relay. If no candidate is clearly faster than the current relay, it keeps the current relay.
4. 手动点候选行里的 `Connect`: 精确连接你选中的 hostname。连接时会写入短维护锁；Auto Guard 保持运行，但暂停切节点动作，连接结束后自动恢复检查。
   - Click `Connect` on a candidate row to connect to the exact hostname you chose. This writes a short maintenance lock; Auto Guard keeps running but pauses node-switching, then resumes checks afterward.
5. `Start Auto Guard`: 等同于启用 LaunchAgent 托管，打开后会在后台持续运行并由 macOS 拉起重启。`Stop Auto Guard` 只用于你明确表示此刻不想使用 VPN；它会停止 LaunchAgent 并断开 Mullvad。
   - Equivalent to enabling LaunchAgent supervision; once on, it runs continuously in the background and is relaunched by macOS. Use `Stop Auto Guard` only when you explicitly don't want the VPN right now; it stops the LaunchAgent and disconnects Mullvad.

顶部 `VPN Traffic` 的大数字显示本工具持久维护的累计 Download / Upload 字节数；切换 relay、重建 tunnel interface、刷新网页或重启面板都不会归零。提示行会显示当前接口原始计数和估算速率。它只读取系统接口计数，不记录你访问了什么。
The large `VPN Traffic` numbers at the top show the persistently maintained cumulative Download / Upload bytes; switching relays, rebuilding the tunnel interface, refreshing the page, or restarting the panel never resets them. The hint line shows the current interface raw counters and estimated rate. It only reads system interface counters and does not record what you visit.

安装或重装 LaunchAgent 时，本地源码目录和 `~/Library/Application Support/MullvadSpeedGuard` 运行目录里的累计流量文件会按更大的 download/upload 总数合并，避免因为文件时间戳或运行目录切换导致累计值回退。
When installing or reinstalling the LaunchAgent, the cumulative-traffic files in the local source directory and the `~/Library/Application Support/MullvadSpeedGuard` runtime directory are merged by taking the larger download/upload totals, so the totals never regress due to file timestamps or a runtime-directory switch.

控制面板顶部会醒目显示 Auto Guard 状态：`RUNNING + LaunchAgent` 表示正在托管运行；`RUNNING + Unsupervised` 表示有守护进程但没有 LaunchAgent 监管，需要点 `Start Auto Guard` 迁回托管；`PAUSED` 表示已经暂停，且 VPN 不再由 Auto Guard 维持。
The panel header prominently shows Auto Guard status: `RUNNING + LaunchAgent` means supervised; `RUNNING + Unsupervised` means a daemon exists but is not LaunchAgent-supervised — click `Start Auto Guard` to migrate back; `PAUSED` means paused, and the VPN is no longer maintained by Auto Guard.

`Fast Candidates` 会同时显示 `Fast Tested`、`True Tested`、`Risk` 和 `Known Mbps`：前者是不切 VPN 的快排探针时间，后者是真实切到该节点后下载测速的时间；`Risk` 会把节点分成 `verified`、`untested`、`previously verified`、`failed true-test`、`avoid`，避免低延迟但真测速失败的节点排在前面。`Verified Backups` 里的 `True Tested` 用来判断速度数据是否新鲜。
`Fast Candidates` shows `Fast Tested`, `True Tested`, `Risk`, and `Known Mbps`: the former is the no-switch fast-probe time, the latter is the time of an actual switch-and-download test; `Risk` classifies nodes as `verified`, `untested`, `previously verified`, `failed true-test`, or `avoid`, so low-latency but failed-true-test nodes don't rank first. `True Tested` in `Verified Backups` indicates whether the speed data is fresh.

Relay 名册会缓存在本地 SQLite：普通查询和选点在 6 小时内复用上次同步结果，避免每次都运行 `mullvad relay list` 并重写整张 relay 表。手动刷新时选择 update、首次空库、或缓存过期时仍会重新读取 Mullvad relay list。
The relay roster is cached in local SQLite: normal queries and relay selection reuse the last sync for 6 hours, avoiding a `mullvad relay list` subprocess and full relay-table upsert on every path. Manual refresh with update, an empty first-run DB, or an expired cache still reads the Mullvad relay list again.

重要限制：不切换 VPN 的快排只能判断"relay IP 是否容易连上、延迟大概如何"；真实网速必须切到对应节点才能测出来。任何工具都不能保证外部网络和 VPN 服务永远不掉线，但 Auto Guard 可以把检测、确认、切换、进程重启做成无人值守闭环。
Important limit: the no-switch fast rank only judges "whether the relay IP connects easily and roughly what the latency is"; real throughput can only be measured by switching to the node. No tool can guarantee external networks and VPN services never drop, but Auto Guard can make detection, confirmation, switching, and process restart an unattended loop.

### 无人值守稳定模式 / Unattended stable mode

推荐长期运行结构 / Recommended long-running setup:

- `URL probe`: 默认并行检查 `https://chatgpt.com/`、`https://chatgpt.com/backend-api/codex/responses` 和 `https://www.youtube.com/generate_204` 是否能通过当前 VPN 访问，用来发现"VPN 显示已连接但关键目标站不可用"的情况。每个探针都有硬超时，避免单个目标站卡住整轮判断。单个 URL 或少量 URL 失败只走连续失败确认，默认只有全部 URL 同时失败才进入紧急恢复，避免目标站短暂抖动触发换节点。
  - `URL probe`: by default checks `https://chatgpt.com/`, `https://chatgpt.com/backend-api/codex/responses`, and `https://www.youtube.com/generate_204` in parallel over the current VPN, to catch "VPN shows connected but key targets are unreachable". Each probe has a hard timeout so one target can't stall the whole round. A single or few URL failures only go through consecutive-failure confirmation; by default only all URLs failing at once triggers emergency recovery, avoiding node switches on brief target hiccups.
- `Blocked countries`: 默认 `hk`。普通扫描、`--all-countries`、库存白名单、救援扫描、空闲补测和夜间全量真测都会跳过香港节点；如果当前已经在香港节点，Auto Guard 会把它视为不可用并切走。
  - `Blocked countries`: default `hk`. Normal scans, `--all-countries`, inventory whitelists, rescue sweeps, idle refresh, and nightly full true-tests all skip Hong Kong relays; if the current relay is already in Hong Kong, Auto Guard treats it as unusable and switches away.
- `Online floor Mbps`: 默认 `0.5`。这是"保住能上网"的低速确认线，不是舒适线；2 Mbps 节点会被保留并优先于不可用节点。
  - `Online floor Mbps`: default `0.5`. This is the "stay online" low-speed confirmation line, not a comfort line; a 2 Mbps node is kept and preferred over an unusable one.
- `Preferred Mbps`: 默认 `8`。只用于白名单择优排序，不作为淘汰门槛。
  - `Preferred Mbps`: default `8`. Used only for whitelist ranking, not as an elimination threshold.
- `Whitelist floor Mbps`: 默认 `0.05`。只要真实测速能跑出一点速度，就先记入历史，避免全网都慢时没有备选。
  - `Whitelist floor Mbps`: default `0.05`. Any node that records even a little real speed enters history, so there's still a backup when the whole network is slow.
- `Failure threshold`: 默认连续 3 次失败才切换，避免一次抖动就换节点。
  - `Failure threshold`: default 3 consecutive failures before switching, to avoid switching on a single hiccup.
- `Backup attempts`: 默认每轮尝试 3 个已验证热备。某个热备失败后会进入 900 秒冷却，避免反复卡在同一个坏节点。
  - `Backup attempts`: default 3 verified hot backups per round. A failed backup enters a 900-second cooldown to avoid getting stuck on the same bad node.
- `Emergency recovery`: 如果 Mullvad 已经断开，或所有关键 URL 同时失败，Auto Guard 不再等满连续失败阈值，会进入恢复；默认最多查看 50 个历史候选、每轮尝试热备，并跳过普通冷却限制，先把链路拉回可用状态。如果历史候选全失败，会用救援池刷新 fast rank 并真测候选。
  - `Emergency recovery`: if Mullvad is already disconnected or all key URLs fail at once, Auto Guard skips waiting for the full failure threshold and enters recovery; by default it reviews up to 50 history candidates, tries hot backups each round, and skips normal cooldowns to bring the link back first. If all history candidates fail, it refreshes the fast rank from a rescue pool and true-tests candidates.
- `Connecting grace`: Mullvad 刚启动或正在切换时会出现 `Connecting`。Auto Guard 默认先等 45 秒，不会立刻抢着切热备，避免"正在恢复时被守护进程又打断一次"。
  - `Connecting grace`: Mullvad shows `Connecting` right after start or during a switch. Auto Guard waits 45 seconds by default before jumping to a backup, avoiding "interrupting a recovery in progress".
- `Daemon unavailable`: 如果 `mullvad status` 返回 Management RPC / Connection refused，说明本地 Mullvad 后台服务不可用，不是某个节点坏。Auto Guard 会停止换节点动作，定期唤起 Mullvad VPN app 并等待管理接口恢复。
  - `Daemon unavailable`: if `mullvad status` returns Management RPC / Connection refused, the local Mullvad service is down — not a bad node. Auto Guard stops switching, periodically wakes the Mullvad VPN app, and waits for the management interface to recover.
- `Quick connect`: 切热备时不用长时间卡在 `--wait`，默认断开最多等 5 秒、连接最多等 12 秒。
  - `Quick connect`: when switching to a backup it doesn't hang on `--wait`; by default it waits at most 5 seconds to disconnect and 12 seconds to connect.
- `Fallback`: Auto Guard 默认允许 Mullvad 在同一城市内回落到另一个 relay；故障恢复时如果 Mullvad 已经回落到任意可连接 relay，也会先做健康检查再决定是否接受。它比死盯一个 hostname 更适合无人值守。
  - `Fallback`: Auto Guard by default lets Mullvad fall back to another relay within the same city; during recovery, if Mullvad already fell back to any connectable relay, it runs a health check before deciding whether to accept it. This suits unattended use better than fixating on one hostname.
- `Nightly full scan`: 默认 30 分钟 VPN 隧道近乎无流量且键鼠也空闲，且距离上次夜间全量真测超过 24 小时，才允许后台逐个真测所有非 retired 节点。测前、节点之间都会检查流量/键鼠/控制锁；一旦有活动就停止。如果已测出的 best 明显快过当前基线（默认至少 +0.5 Mbps 且 1.15x），就切到 best；否则恢复原节点。没被打断时会测完整个列表后同样按这个规则选择。
  - `Nightly full scan`: after ~30 minutes of near-zero VPN tunnel traffic and idle keyboard/mouse, and at least 24 hours since the previous nightly full true-test, Auto Guard may true-test every non-retired relay. It checks traffic, HID idle, and control locks before and between relays, stopping as soon as activity returns. If the best tested relay is clearly faster than the current baseline (default +0.5 Mbps and 1.15x), it switches to that best relay; otherwise it restores the original relay. If uninterrupted, it completes the full list and applies the same decision rule.
- `Idle refresh fallback`: 如果夜间全量真测还在冷却中，空闲维护仍可按小批量逻辑补测候选；默认每轮只测 1 个。
  - `Idle refresh fallback`: when the nightly full scan is still in cooldown, idle maintenance can still refresh a tiny candidate batch; default batch size is 1.
- `Control lock`: 面板里的真测、扫描、手动连接，以及夜间维护任务都不会自行关闭 Auto Guard；它们只写临时维护锁，让 Auto Guard 存活但暂停动作。
  - `Control lock`: panel true-tests, scans, manual connects, and nightly maintenance never shut down Auto Guard; they only write a temporary maintenance lock so Auto Guard stays alive but pauses actions.
- `Refresh cooldown`: 后台守护允许故障时刷新候选池，但默认 30 分钟最多做一次真测刷新，避免坏网络下不断断开。
  - `Refresh cooldown`: the daemon may refresh the candidate pool on failure, but by default does at most one true-test refresh per 30 minutes, to avoid constant disconnects on a bad network.
- `LaunchAgent`: 安装后 macOS 登录即启动 Auto Guard；进程崩溃或异常退出后会自动拉起。控制面板也可以作为独立 LaunchAgent 常驻在 `localhost:18790`，不依赖 VPN 是否连上。
  - `LaunchAgent`: after install, Auto Guard starts at macOS login and is relaunched on crash or abnormal exit. The control panel can also run as a standalone LaunchAgent on `localhost:18790`, independent of whether the VPN is connected.

安装后台守护 / Install the background guard:

```bash
cd path/to/WorkTools/mullvad-speed-guard
./install_auto_guard.sh
```

安装本地控制面板守护 / Install the local control-panel daemon:

```bash
cd path/to/WorkTools/mullvad-speed-guard
./install_panel.sh
```

`install_panel.sh` 会同时安装一个原生 macOS 浮动流量小窗：它显示累计 Download / Upload 总量和当前下载速度，速度单位为 `MB/s`，窗口可拖拽并记忆位置，双击可打开完整控制面板。颜色含义：红色表示断链或面板不可达，黄色表示当前下载速度低于 5 Mbps（约 0.63 MB/s），绿色表示当前下载速度达到或超过 5 Mbps。浮窗由 Swift/AppKit 构建，需要本机可用的 `swiftc`，安装脚本会自动编译。
`install_panel.sh` also installs a native macOS floating traffic widget: it shows cumulative Download / Upload totals and current download speed in `MB/s`, is draggable, remembers its position, and opens the full panel on double-click. Color meaning: red = link down or panel unreachable; yellow = current download below 5 Mbps (~0.63 MB/s); green = current download at or above 5 Mbps. The widget is built with Swift/AppKit and needs a working local `swiftc`; the install script compiles it automatically.

只单独安装或移除浮窗 / Install or remove only the floating widget:

```bash
./install_float_widget.sh
./uninstall_float_widget.sh
```

`overnight_goal_runner.py` 只用于夜间节点盘点和候选节点整理。历史版本里曾把可选 Ollama 本地模型准备也放进这个 runner；当前默认已经关闭，`MSG_ENABLE_OLLAMA_SETUP=0` 时不会安装 Ollama 或下载任何大模型。
`overnight_goal_runner.py` is only for nightly node inventory and candidate cleanup. Older versions also bundled optional Ollama local-model setup into this runner; it is now off by default — with `MSG_ENABLE_OLLAMA_SETUP=0`, it installs no Ollama and downloads no large models.

查看本地控制面板 / View the local control panel:

```bash
curl http://localhost:18790/api/ping
open http://localhost:18790/
```

查看后台状态和日志 / Check background status and logs:

```bash
launchctl print gui/$(id -u)/com.story.mullvad-speed-guard.auto-guard
tail -f "$HOME/Library/Application Support/MullvadSpeedGuard/results/auto_guard_launchagent.log"
```

停止并移除后台守护 / Stop and remove the background guard:

```bash
cd path/to/WorkTools/mullvad-speed-guard
./uninstall_auto_guard.sh
```

查看当前状态 / Check current status:

```bash
cd path/to/WorkTools/mullvad-speed-guard
python3 mullvad_speed_guard.py status
```

断联或面板异常后，先跑自诊断 / After a disconnect or panel issue, run self-diagnosis first:

```bash
python3 mullvad_speed_guard.py doctor
```

`doctor` 不会切换节点，也不会测速；它只检查 Mullvad CLI / 管理接口、LaunchAgent、控制面板端口、本地 inventory 数据库、累计流量文件和日志体积。需要给脚本或记录系统读取时可加 `--json`。
`doctor` does not switch nodes or run speed tests; it only checks the Mullvad CLI / management interface, LaunchAgent, control-panel port, local inventory DB, cumulative-traffic files, and log size. Add `--json` for scripts or logging systems.

列出默认候选节点 / List default candidate nodes:

```bash
python3 mullvad_speed_guard.py list --limit 40
```

只预览将要测速的节点，不改变连接 / Preview the nodes to be tested without changing the connection:

```bash
python3 mullvad_speed_guard.py scan --dry-run --countries jp,sg,us --max-candidates 20
```

测试候选节点，最后连接最快节点 / Test candidates and finally connect the fastest:

```bash
python3 mullvad_speed_guard.py scan --connect-best --countries jp,sg,us --max-candidates 20
```

持续监控：默认使用 `adaptive` 模式。平时只做本地状态读取和轻量 TCP 延迟心跳；每 15 分钟一个周期，如果这个周期内的真实使用流量已经证明速度够好，就不主动测速；如果没有足够流量，才做一次小下载抽样。连续失败后，优先连接历史白名单里当前时段排序最高的可用节点。
Continuous monitoring: defaults to `adaptive`. Normally it only reads local status and does a light TCP latency heartbeat; on a 15-minute cycle, if real usage traffic during the cycle already proves the speed is good, it does not actively test; only without enough traffic does it do a small download sample. After consecutive failures, it prefers the top-ranked available node for the current time slot from the history whitelist.

```bash
python3 mullvad_speed_guard.py watch --min-mbps 0.5 --interval 60 --countries jp,sg,us --max-candidates 20
```

完全不做周期性下载测速，只检查 Mullvad 状态和 TCP 心跳 / No periodic download tests at all; only check Mullvad status and TCP heartbeat:

```bash
python3 mullvad_speed_guard.py watch --health-mode adaptive --speed-check-every 0 --interval 60 --countries jp,sg,us --max-candidates 20
```

只读取 Mullvad 本地状态，几乎零流量，但无法判断"连上了但外网不通" / Read only the local Mullvad status (near-zero traffic), but can't tell "connected but no internet":

```bash
python3 mullvad_speed_guard.py watch --health-mode status --interval 30
```

只做 TCP 延迟心跳，流量极低，适合长期后台运行 / Only TCP latency heartbeat (very low traffic), good for long-term background use:

```bash
python3 mullvad_speed_guard.py watch --health-mode latency --max-latency-ms 2500 --interval 60
```

每次都下载测速，判断速度最直接，但会持续消耗带宽，不建议长期后台常开 / Download-test every cycle (most direct speed judgment) but constantly uses bandwidth; not recommended for long-term background use:

```bash
python3 mullvad_speed_guard.py watch --health-mode speed --min-mbps 0.5 --interval 300
```

全节点扫描可以这样运行，但不建议第一次就这么做 / A full-node scan can run like this, but don't do this on your first try:

```bash
python3 mullvad_speed_guard.py scan --connect-best --all-countries
```

`--all-countries` 仍会遵守 `blocked_countries=["hk"]`，不会测试或连接香港节点。
`--all-countries` still respects `blocked_countries=["hk"]`; it will not test or connect Hong Kong relays.

## 配置 / Configuration

默认配置在 / Default config at:

```text
path/to/WorkTools/mullvad-speed-guard/config.example.json
```

常改项 / Commonly changed options:

- `candidate_countries`: 候选国家/地区代码。设为空数组并使用 `--all-countries` 才会全扫。/ Candidate country/region codes. Use an empty array plus `--all-countries` to scan everything.
- `blocked_countries`: 硬排除国家/地区代码，默认 `["hk"]`。即使使用 `--all-countries` 或手动指定 hostname，也不会使用这些地区的节点。/ Hard-blocked country/region codes; default `["hk"]`. Even `--all-countries` or manually specified hostnames will not use these regions.
- `candidate_cities`: 限定城市代码，例如 `["tyo", "osa", "sg"]`。/ Restrict to city codes, e.g. `["tyo", "osa", "sg"]`.
- `candidate_hostnames`: 只测指定 relay，例如 `["jp-tyo-wg-201", "sg-sin-wg-101"]`。/ Test only specific relays, e.g. `["jp-tyo-wg-201", "sg-sin-wg-101"]`.
- `max_candidates`: 限制最多测试多少个节点，`0` 表示不限制。/ Max nodes to test; `0` means unlimited.
- `health_mode`: `adaptive`、`latency`、`status` 或 `speed`。/ One of `adaptive`, `latency`, `status`, or `speed`.
- `max_latency_ms`: 延迟心跳阈值。超过后认为当前节点不健康。/ Latency-heartbeat threshold; above it the current node is considered unhealthy.
- `speed_check_every_seconds`: `adaptive` 模式下小下载测速的间隔。设为 `0` 表示不做周期性下载测速。/ Interval of the small download test in `adaptive` mode; `0` disables periodic download tests.
- `url_checks`: 必须能访问的 URL 列表。默认包含 `https://chatgpt.com/` 和 Codex backend endpoint。/ List of URLs that must be reachable; defaults include `https://chatgpt.com/` and the Codex backend endpoint.
- `url_check_timeout_seconds`: URL 探针超时。/ URL-probe timeout.
- `health_download_bytes`: 后台健康检查的小下载测速字节数。默认 1 MB。/ Bytes for the background health-check download test; default 1 MB.
- `min_mbps`: 在线保命线。默认 `0.5`，低于它才认为"速度层面不可用"。它不是越高越好，设太高会把还能用的节点误判为坏节点。/ Online survival line; default `0.5`, below which a node is "speed-wise unusable". Higher is not better — too high will misjudge usable nodes as bad.
- `preferred_mbps`: 舒适速度目标。默认 `8`，只影响白名单排序，达到它会加分，不达到也不会被排除。/ Comfort-speed target; default `8`, affects only whitelist ranking — meeting it adds points, missing it does not exclude.
- `inventory_min_working_mbps`: 白名单收录线。默认 `0.05`，尽量先记录所有能出网的节点，再从快到慢择优。/ Whitelist-admission line; default `0.05`, recording all working nodes first, then ranking fast-to-slow.
- `idle_refresh_after_seconds`: 空闲维护窗口。默认 `1800` 秒，即 30 分钟近乎无 VPN 流量后才开始补测。/ Idle maintenance window; default `1800` s, i.e. start re-testing only after 30 minutes of near-zero VPN traffic.
- `idle_refresh_user_idle_seconds`: 键鼠空闲要求。默认 `1800` 秒；如果系统无法读取键鼠空闲时间，就只使用 VPN 流量判断。/ Keyboard/mouse idle requirement; default `1800` s; if the system can't read input idle time, it judges by VPN traffic only.
- `idle_refresh_activity_threshold_bytes`: 空闲流量阈值。默认 `262144` 字节，用来忽略守护自己的心跳小流量；阈值设太低会导致永远进不了空闲窗口。/ Idle-traffic threshold; default `262144` bytes, to ignore the daemon's own heartbeat traffic; too low and the idle window is never entered.
- `idle_refresh_batch_size`: 空闲时每轮真测候选数量。默认 `1`，把可能打扰用户的窗口压到最短。/ Candidates true-tested per idle round; default `1`, minimizing potential disruption.
- `nightly_full_scan_cooldown_seconds`: 夜间全量真测冷却。默认 `86400` 秒，即每天最多一轮。/ Nightly full-scan cooldown; default `86400` s, i.e. at most one full true-test sweep per day.
- `nightly_full_scan_max_seconds`: 单轮夜间全量真测最长运行时间。默认 `21600` 秒，即 6 小时。/ Max duration of one nightly full true-test sweep; default `21600` s, i.e. 6 hours.
- `nightly_full_scan_better_min_delta_mbps`: 夜间扫描后切换 best 的最低绝对速度增量。默认 `0.5` Mbps。/ Minimum absolute speed gain before switching to the nightly best relay; default `0.5` Mbps.
- `nightly_full_scan_better_min_ratio`: 夜间扫描后切换 best 的最低倍率。默认 `1.15`。/ Minimum speed ratio before switching to the nightly best relay; default `1.15`.
- `connecting_grace_seconds`: Mullvad 处于 `Connecting` 时等待多久再进入恢复。默认 `45` 秒。/ How long to wait while Mullvad is `Connecting` before entering recovery; default `45` s.
- `daemon_retry_cooldown_seconds`: Mullvad 管理接口不可用时，多久尝试唤起一次 Mullvad VPN app。默认 `180` 秒。/ When the management interface is unavailable, how often to try waking the Mullvad VPN app; default `180` s.
- `url_emergency_min_failed`: 多少个 URL 探针同时失败才进入紧急恢复。默认 `0` 表示所有配置的 URL 都失败才紧急恢复；少量 URL 失败会按 `failure_threshold` 连续确认。/ How many URL probes must fail at once to trigger emergency recovery; default `0` means all configured URLs must fail; a few failures use `failure_threshold` confirmation.
- `failure_threshold`: 连续多少次健康检查失败才重扫。/ Consecutive health-check failures before rescanning.
- `backup_attempt_limit`: 故障恢复时每轮最多尝试多少个已验证热备。默认 `3`。/ Max verified hot backups attempted per recovery round; default `3`.
- `backup_retry_cooldown_seconds`: 某个热备失败后的临时跳过时间。默认 `900` 秒。/ Temporary skip time after a backup fails; default `900` s.
- `rescan_cooldown_seconds`: 重扫冷却，避免网络抖动时反复切换。/ Rescan cooldown, to avoid repeated switching on network jitter.
- `download_bytes`: 每个下载测速源最多读取多少字节。越大越准，也越费流量和时间。/ Max bytes read per download-test source; larger is more accurate but costs more traffic and time.
- `quick_connect_timeout_seconds`: Auto Guard 切热备时等待连接成功的短超时。/ Short timeout for connect success when Auto Guard switches to a backup.
- `disconnect_wait_timeout_seconds`: Auto Guard 切热备前等待断开的短超时。/ Short timeout for disconnect before Auto Guard switches to a backup.
- `strict_hostname`: 手动连接默认严格匹配 hostname；Auto Guard 默认关闭，允许同城 fallback。/ Manual connect strictly matches hostname by default; Auto Guard turns it off by default, allowing same-city fallback.

## 监控模式怎么选 / Choosing a monitor mode

- `status`: 只读 `mullvad status`。最省流量，但只能知道客户端是否认为自己连上。/ Reads only `mullvad status`. Most traffic-frugal, but only knows whether the client thinks it's connected.
- `latency`: 连接几个公共 TCP 目标，例如 `1.1.1.1:443`。流量极低，可以确认当前隧道基本能出网。/ Connects to a few public TCP targets like `1.1.1.1:443`. Very low traffic; confirms the tunnel can basically reach the internet.
- `adaptive`: 推荐默认值。每次做状态 + 延迟心跳，偶尔做小下载抽样。兼顾稳定和带宽占用。/ Recommended default. Each cycle does status + latency heartbeat, occasionally a small download sample. Balances stability and bandwidth.
- `speed`: 每次都下载测速。只有它能比较准确判断低于某个 Mbps，但长期运行会影响网速。/ Download-test every cycle. Only this judges sub-Mbps speeds accurately, but long-term use affects your speed.

严格来说，不下载数据就无法精确知道"当前节点最大速度"。轻量心跳能判断"能不能用、是不是明显卡死、延迟是否异常"，真正的吞吐量只能通过被动接口计数或低频抽样测速来估计。
Strictly speaking, you can't know a node's max speed without downloading data. A light heartbeat tells you "usable / clearly stuck / abnormal latency"; true throughput can only be estimated via passive interface counters or low-frequency sampled tests.

## 后台运行 / Background operation

长期后台运行请优先使用上面的 `./install_auto_guard.sh`。旧的 `watch` 命令适合临时测试；真正无人值守需要 LaunchAgent 负责开机启动和异常重启。
For long-term background use, prefer `./install_auto_guard.sh` above. The old `watch` command suits temporary testing; true unattended use needs the LaunchAgent for boot startup and crash restart.

## 关于"免费稳定高速 VPN" / On "free, stable, fast VPN"

没有服务器资源的情况下，自己做一个长期稳定、快速、免费的 VPN 基本不现实。VPN 的稳定性来自可持续的出口服务器、带宽、IP 信誉、维护、抗封锁能力和安全运维；免费公共节点通常慢、不稳定，也有隐私和安全风险。更现实的方案是继续使用可信 VPN 服务，并用这个工具把可用节点自动筛出来。
Without server resources, building your own long-term stable, fast, free VPN is basically unrealistic. VPN stability comes from sustainable exit servers, bandwidth, IP reputation, maintenance, anti-blocking ability, and secure operations; free public nodes are usually slow, unstable, and carry privacy and security risks. The more realistic approach is to keep using a trusted VPN service and use this tool to automatically surface the usable nodes.
