# REAPER Scripts

游戏音频工作流相关的 REAPER ReaScript（Lua / Python），均为自制 / AI 辅助生成。
第三方下载包（Cockos、ReaTeam、LKC、Nabla、Audiokinetic 等）不在此仓库内。

安装：复制到 `%APPDATA%\REAPER\Scripts\`，然后在 REAPER
`Actions → Show action list → New action ▾ → Load ReaScript…` 加载。

## 脚本一览

| 脚本 | 功能 |
|------|------|
| `Reaper_BatchRename_Keywords.lua` | 对选中轨道的轨道名 / item 名做关键词批量查找替换 |
| `Reaper_SpaceItems_Gap.lua` | 把选中的多个 item 按固定间隔（秒/毫秒/拍）重新排列 |
| `RegionHacker Ver1.0/2.0.lua` | 按时间/图层智能分组，为选中 item 批量创建 Region |
| `Wwise Bridge Ver1.0~2.0.lua` | REAPER → Wwise 导出桥接（多版本迭代） |
| `Codex_Visual slicer *.lua` | 基于源文件的选中 item 可视化切片工具 + 预设 |
| `Codex_OBS ReaStream capture setup.lua` | OBS ReaStream 采集配置 |
| `Codex_Enable auto-crossfade *.lua` | 编辑时自动交叉淡化 |
| `Codex/codex_visual_event_analyzer.py` | 可视化事件分析（Python） |
| `Repair.lua` | 修复损坏的 Render Format 配置，重置为默认 WAV |
| `ReaIMTest.lua` | 检测 ReaImGui 是否正常加载 |
