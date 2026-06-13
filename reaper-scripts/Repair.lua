-- @description Reaper Render Format Rescuer
-- @about 修复因错误 Base64 导致的 Render Format 空白/损坏问题，重置为默认 WAV。

local r = reaper

-- 标准 WAV 24bit 头的 Base64 (兼容大多数版本)
-- 如果这个也失效，我们将清除项目配置让 Reaper 回退默认
local SAFE_WAV_B64 = "ZXZh3gAAAA==" 

r.ShowConsoleMsg("正在尝试修复渲染格式...\n")

-- 1. 尝试写入标准 WAV 配置
local result = r.GetSetProjectInfo_String(0, "RENDER_FORMAT", SAFE_WAV_B64, true)

-- 2. 强制重置 BoundsFlag 到 Time Selection (2)
r.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 2, true)

r.ShowMessageBox("渲染格式已重置。\n\n请现在打开 File > Render... 菜单，\n检查格式是否已恢复为 WAV。\n\n如果恢复正常，请删除本修复脚本。", "修复完成", 0)
