# Mullvad Speed Guard

本工具会使用本机的 `mullvad` CLI 监测 VPN 状态、测试候选节点，并在断线、延迟异常或抽样测速低于阈值时重新选择最快节点连接。它不保存账号、密码、真实 IP、浏览记录或个人资产信息。

## 适用边界

- 支持：Mullvad 节点列表解析、候选节点筛选、逐节点连接测速、轻量心跳监测、低速/断线监测、自动切到最佳节点。
- 不支持：创建免费 VPN 基础设施、绕过账号限制、修改 Mullvad 客户端内部文件、保证任何地区/网络下稳定可用。
- 注意：全节点逐个连接测速会打断当前 VPN 连接，并可能耗时很久。建议先测香港、日本、新加坡、韩国、台湾、美国西海岸等候选区域。

## 快速使用

### 桌面 GUI 推荐流程

双击桌面的 `Mullvad Speed Guard.app` 后，推荐按这个顺序使用：

1. `Fast Rank All`: 快速探测所有 Mullvad relay 的 TCP 可达性和延迟，不切换 VPN，通常几秒完成，并刷新 `Fast Candidates` 候选。
2. `True Test Pool`: 真实切到快排候选做下载测速。默认先测 5 个；只要节点能跑出基本速度就会进入历史白名单，越快、越稳定、同一时段越可靠的节点会排得越前。测完会恢复你原来的节点。
3. 手动点候选行里的 `Connect`: 精确连接你选中的 hostname。连接时会写入短维护锁；Auto Guard 保持运行，但暂停切节点动作，连接结束后自动恢复检查。
4. `Start Auto Guard`: 等同于启用 LaunchAgent 托管，打开后会在后台持续运行并由 macOS 拉起重启。`Stop Auto Guard` 只用于你明确表示此刻不想使用 VPN；它会停止 LaunchAgent 并断开 Mullvad。

顶部 `VPN Traffic` 的大数字显示本工具持久维护的累计 Download / Upload 字节数；切换 relay、重建 tunnel interface、刷新网页或重启面板都不会归零。提示行会显示当前接口原始计数和估算速率。它只读取系统接口计数，不记录你访问了什么。

安装或重装 LaunchAgent 时，本地源码目录和 `~/Library/Application Support/MullvadSpeedGuard` 运行目录里的累计流量文件会按更大的 download/upload 总数合并，避免因为文件时间戳或运行目录切换导致累计值回退。

控制面板顶部会醒目显示 Auto Guard 状态：`RUNNING + LaunchAgent` 表示正在托管运行；`RUNNING + Unsupervised` 表示有守护进程但没有 LaunchAgent 监管，需要点 `Start Auto Guard` 迁回托管；`PAUSED` 表示已经暂停，且 VPN 不再由 Auto Guard 维持。

`Fast Candidates` 会同时显示 `Fast Tested`、`True Tested`、`Risk` 和 `Known Mbps`：前者是不切 VPN 的快排探针时间，后者是真实切到该节点后下载测速的时间；`Risk` 会把节点分成 `verified`、`untested`、`previously verified`、`failed true-test`、`avoid`，避免低延迟但真测速失败的节点排在前面。`Verified Backups` 里的 `True Tested` 用来判断速度数据是否新鲜。

重要限制：不切换 VPN 的快排只能判断“relay IP 是否容易连上、延迟大概如何”；真实网速必须切到对应节点才能测出来。任何工具都不能保证外部网络和 VPN 服务永远不掉线，但 Auto Guard 可以把检测、确认、切换、进程重启做成无人值守闭环。

### 无人值守稳定模式

推荐长期运行结构：

- `URL probe`: 默认并行检查 `https://chatgpt.com/`、`https://chatgpt.com/backend-api/codex/responses` 和 `https://www.youtube.com/generate_204` 是否能通过当前 VPN 访问，用来发现“VPN 显示已连接但关键目标站不可用”的情况。每个探针都有硬超时，避免单个目标站卡住整轮判断。单个 URL 或少量 URL 失败只走连续失败确认，默认只有全部 URL 同时失败才进入紧急恢复，避免目标站短暂抖动触发换节点。
- `Online floor Mbps`: 默认 `0.5`。这是“保住能上网”的低速确认线，不是舒适线；2 Mbps 节点会被保留并优先于不可用节点。
- `Preferred Mbps`: 默认 `8`。只用于白名单择优排序，不作为淘汰门槛。
- `Whitelist floor Mbps`: 默认 `0.05`。只要真实测速能跑出一点速度，就先记入历史，避免全网都慢时没有备选。
- `Failure threshold`: 默认连续 3 次失败才切换，避免一次抖动就换节点。
- `Backup attempts`: 默认每轮尝试 3 个已验证热备。某个热备失败后会进入 900 秒冷却，避免反复卡在同一个坏节点。
- `Emergency recovery`: 如果 Mullvad 已经断开，或所有关键 URL 同时失败，Auto Guard 不再等满连续失败阈值，会进入恢复；默认最多查看 50 个历史候选、每轮尝试热备，并跳过普通冷却限制，先把链路拉回可用状态。如果历史候选全失败，会用救援池刷新 fast rank 并真测候选。
- `Connecting grace`: Mullvad 刚启动或正在切换时会出现 `Connecting`。Auto Guard 默认先等 45 秒，不会立刻抢着切热备，避免“正在恢复时被守护进程又打断一次”。
- `Daemon unavailable`: 如果 `mullvad status` 返回 Management RPC / Connection refused，说明本地 Mullvad 后台服务不可用，不是某个节点坏。Auto Guard 会停止换节点动作，定期唤起 Mullvad VPN app 并等待管理接口恢复。
- `Quick connect`: 切热备时不用长时间卡在 `--wait`，默认断开最多等 5 秒、连接最多等 12 秒。
- `Fallback`: Auto Guard 默认允许 Mullvad 在同一城市内回落到另一个 relay；故障恢复时如果 Mullvad 已经回落到任意可连接 relay，也会先做健康检查再决定是否接受。它比死盯一个 hostname 更适合无人值守。
- `Idle refresh`: 默认 30 分钟 VPN 隧道近乎无流量且键鼠也空闲，才允许后台补测节点。补测时每次只真测 1 个候选；测前/测后发现流量或用户活动就停止后续补测并恢复原节点。
- `Control lock`: 面板里的真测、扫描、手动连接，以及夜间维护任务都不会自行关闭 Auto Guard；它们只写临时维护锁，让 Auto Guard 存活但暂停动作。
- `Refresh cooldown`: 后台守护允许故障时刷新候选池，但默认 30 分钟最多做一次真测刷新，避免坏网络下不断断开。
- `LaunchAgent`: 安装后 macOS 登录即启动 Auto Guard；进程崩溃或异常退出后会自动拉起。控制面板也可以作为独立 LaunchAgent 常驻在 `localhost:18790`，不依赖 VPN 是否连上。

安装后台守护：

```bash
cd path/to/WorkTools/mullvad-speed-guard
./install_auto_guard.sh
```

安装本地控制面板守护：

```bash
cd path/to/WorkTools/mullvad-speed-guard
./install_panel.sh
```

`install_panel.sh` 会同时安装一个原生 macOS 浮动流量小窗：它显示累计 Download / Upload 总量和当前下载速度，速度单位为 `MB/s`，窗口可拖拽并记忆位置，双击可打开完整控制面板。颜色含义：红色表示断链或面板不可达，黄色表示当前下载速度低于 5 Mbps（约 0.63 MB/s），绿色表示当前下载速度达到或超过 5 Mbps。浮窗由 Swift/AppKit 构建，需要本机可用的 `swiftc`，安装脚本会自动编译。

只单独安装或移除浮窗：

```bash
./install_float_widget.sh
./uninstall_float_widget.sh
```

`overnight_goal_runner.py` 只用于夜间节点盘点和候选节点整理。历史版本里曾把可选 Ollama 本地模型准备也放进这个 runner；当前默认已经关闭，`MSG_ENABLE_OLLAMA_SETUP=0` 时不会安装 Ollama 或下载任何大模型。

查看本地控制面板：

```bash
curl http://localhost:18790/api/ping
open http://localhost:18790/
```

查看后台状态和日志：

```bash
launchctl print gui/$(id -u)/com.story.mullvad-speed-guard.auto-guard
tail -f "$HOME/Library/Application Support/MullvadSpeedGuard/results/auto_guard_launchagent.log"
```

停止并移除后台守护：

```bash
cd path/to/WorkTools/mullvad-speed-guard
./uninstall_auto_guard.sh
```

查看当前状态：

```bash
cd path/to/WorkTools/mullvad-speed-guard
python3 mullvad_speed_guard.py status
```

断联或面板异常后，先跑自诊断：

```bash
python3 mullvad_speed_guard.py doctor
```

`doctor` 不会切换节点，也不会测速；它只检查 Mullvad CLI / 管理接口、LaunchAgent、控制面板端口、本地 inventory 数据库、累计流量文件和日志体积。需要给脚本或记录系统读取时可加 `--json`。

列出默认候选节点：

```bash
python3 mullvad_speed_guard.py list --limit 40
```

只预览将要测速的节点，不改变连接：

```bash
python3 mullvad_speed_guard.py scan --dry-run --countries hk,jp,sg,us --max-candidates 20
```

测试候选节点，最后连接最快节点：

```bash
python3 mullvad_speed_guard.py scan --connect-best --countries hk,jp,sg,us --max-candidates 20
```

持续监控：默认使用 `adaptive` 模式。平时只做本地状态读取和轻量 TCP 延迟心跳；每 15 分钟一个周期，如果这个周期内的真实使用流量已经证明速度够好，就不主动测速；如果没有足够流量，才做一次小下载抽样。连续失败后，优先连接历史白名单里当前时段排序最高的可用节点。

```bash
python3 mullvad_speed_guard.py watch --min-mbps 0.5 --interval 60 --countries hk,jp,sg,us --max-candidates 20
```

完全不做周期性下载测速，只检查 Mullvad 状态和 TCP 心跳：

```bash
python3 mullvad_speed_guard.py watch --health-mode adaptive --speed-check-every 0 --interval 60 --countries hk,jp,sg,us --max-candidates 20
```

只读取 Mullvad 本地状态，几乎零流量，但无法判断“连上了但外网不通”：

```bash
python3 mullvad_speed_guard.py watch --health-mode status --interval 30
```

只做 TCP 延迟心跳，流量极低，适合长期后台运行：

```bash
python3 mullvad_speed_guard.py watch --health-mode latency --max-latency-ms 2500 --interval 60
```

每次都下载测速，判断速度最直接，但会持续消耗带宽，不建议长期后台常开：

```bash
python3 mullvad_speed_guard.py watch --health-mode speed --min-mbps 0.5 --interval 300
```

全节点扫描可以这样运行，但不建议第一次就这么做：

```bash
python3 mullvad_speed_guard.py scan --connect-best --all-countries
```

## 配置

默认配置在：

```text
path/to/WorkTools/mullvad-speed-guard/config.example.json
```

常改项：

- `candidate_countries`: 候选国家/地区代码。设为空数组并使用 `--all-countries` 才会全扫。
- `candidate_cities`: 限定城市代码，例如 `["hkg", "tyo", "osa"]`。
- `candidate_hostnames`: 只测指定 relay，例如 `["hk-hkg-wg-301", "jp-tyo-wg-201"]`。
- `max_candidates`: 限制最多测试多少个节点，`0` 表示不限制。
- `health_mode`: `adaptive`、`latency`、`status` 或 `speed`。
- `max_latency_ms`: 延迟心跳阈值。超过后认为当前节点不健康。
- `speed_check_every_seconds`: `adaptive` 模式下小下载测速的间隔。设为 `0` 表示不做周期性下载测速。
- `url_checks`: 必须能访问的 URL 列表。默认包含 `https://chatgpt.com/` 和 Codex backend endpoint。
- `url_check_timeout_seconds`: URL 探针超时。
- `health_download_bytes`: 后台健康检查的小下载测速字节数。默认 1 MB。
- `min_mbps`: 在线保命线。默认 `0.5`，低于它才认为“速度层面不可用”。它不是越高越好，设太高会把还能用的节点误判为坏节点。
- `preferred_mbps`: 舒适速度目标。默认 `8`，只影响白名单排序，达到它会加分，不达到也不会被排除。
- `inventory_min_working_mbps`: 白名单收录线。默认 `0.05`，尽量先记录所有能出网的节点，再从快到慢择优。
- `idle_refresh_after_seconds`: 空闲维护窗口。默认 `1800` 秒，即 30 分钟近乎无 VPN 流量后才开始补测。
- `idle_refresh_user_idle_seconds`: 键鼠空闲要求。默认 `1800` 秒；如果系统无法读取键鼠空闲时间，就只使用 VPN 流量判断。
- `idle_refresh_activity_threshold_bytes`: 空闲流量阈值。默认 `262144` 字节，用来忽略守护自己的心跳小流量；阈值设太低会导致永远进不了空闲窗口。
- `idle_refresh_batch_size`: 空闲时每轮真测候选数量。默认 `1`，把可能打扰用户的窗口压到最短。
- `connecting_grace_seconds`: Mullvad 处于 `Connecting` 时等待多久再进入恢复。默认 `45` 秒。
- `daemon_retry_cooldown_seconds`: Mullvad 管理接口不可用时，多久尝试唤起一次 Mullvad VPN app。默认 `180` 秒。
- `url_emergency_min_failed`: 多少个 URL 探针同时失败才进入紧急恢复。默认 `0` 表示所有配置的 URL 都失败才紧急恢复；少量 URL 失败会按 `failure_threshold` 连续确认。
- `failure_threshold`: 连续多少次健康检查失败才重扫。
- `backup_attempt_limit`: 故障恢复时每轮最多尝试多少个已验证热备。默认 `3`。
- `backup_retry_cooldown_seconds`: 某个热备失败后的临时跳过时间。默认 `900` 秒。
- `rescan_cooldown_seconds`: 重扫冷却，避免网络抖动时反复切换。
- `download_bytes`: 每个下载测速源最多读取多少字节。越大越准，也越费流量和时间。
- `quick_connect_timeout_seconds`: Auto Guard 切热备时等待连接成功的短超时。
- `disconnect_wait_timeout_seconds`: Auto Guard 切热备前等待断开的短超时。
- `strict_hostname`: 手动连接默认严格匹配 hostname；Auto Guard 默认关闭，允许同城 fallback。

## 监控模式怎么选

- `status`: 只读 `mullvad status`。最省流量，但只能知道客户端是否认为自己连上。
- `latency`: 连接几个公共 TCP 目标，例如 `1.1.1.1:443`。流量极低，可以确认当前隧道基本能出网。
- `adaptive`: 推荐默认值。每次做状态 + 延迟心跳，偶尔做小下载抽样。兼顾稳定和带宽占用。
- `speed`: 每次都下载测速。只有它能比较准确判断低于某个 Mbps，但长期运行会影响网速。

严格来说，不下载数据就无法精确知道“当前节点最大速度”。轻量心跳能判断“能不能用、是不是明显卡死、延迟是否异常”，真正的吞吐量只能通过被动接口计数或低频抽样测速来估计。

## 后台运行

长期后台运行请优先使用上面的 `./install_auto_guard.sh`。旧的 `watch` 命令适合临时测试；真正无人值守需要 LaunchAgent 负责开机启动和异常重启。

## 关于“免费稳定高速 VPN”

没有服务器资源的情况下，自己做一个长期稳定、快速、免费的 VPN 基本不现实。VPN 的稳定性来自可持续的出口服务器、带宽、IP 信誉、维护、抗封锁能力和安全运维；免费公共节点通常慢、不稳定，也有隐私和安全风险。更现实的方案是继续使用可信 VPN 服务，并用这个工具把可用节点自动筛出来。
