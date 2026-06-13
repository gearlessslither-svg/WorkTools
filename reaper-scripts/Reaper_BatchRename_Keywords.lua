--[[
  Reaper_BatchRename_Keywords.lua
  ------------------------------------------------------------
  功能：对【选中轨道】里的关键词进行批量查找/替换改名。
        例如：把所有 "In" 改成 "Out"。

  使用方法：
    1. 在 REAPER 里选中一条或多条轨道
    2. 运行本脚本（Actions -> ReaScript: Run, 或绑定快捷键）
    3. 在弹窗里填写：
         查找内容        要被替换掉的关键词，如 In
         替换为          新的关键词，如 Out（留空 = 删除该关键词）
         范围            1=只改轨道名  2=只改 item/take 名  3=两者都改
         区分大小写      y = 区分大小写  n = 不区分
    4. 脚本会替换所有匹配项，并显示一共改了多少处（可一次 Ctrl+Z 撤销）

  作者：EF Audio Tools
]]--

------------------------------------------------------------
-- 纯文本（非正则）查找替换，支持是否区分大小写
------------------------------------------------------------
local function plainReplace(text, find, replace, caseSensitive)
  if find == "" then return text, 0 end

  local count = 0
  local result = {}
  local searchText = text
  local searchFind = find

  -- 不区分大小写时，用小写副本定位，再从原文截取，保证替换后大小写正确
  if not caseSensitive then
    searchText = text:lower()
    searchFind = find:lower()
  end

  local findLen = #searchFind
  local pos = 1
  while true do
    -- plain=true 表示按纯文本查找，不当作正则
    local s, e = searchText:find(searchFind, pos, true)
    if not s then
      result[#result + 1] = text:sub(pos)
      break
    end
    result[#result + 1] = text:sub(pos, s - 1)
    result[#result + 1] = replace
    count = count + 1
    pos = e + 1
  end

  return table.concat(result), count
end

------------------------------------------------------------
-- 主流程
------------------------------------------------------------
local trackCount = reaper.CountSelectedTracks(0)
if trackCount == 0 then
  reaper.MB("请先选中至少一条轨道，再运行本脚本。", "批量改名", 0)
  return
end

-- 弹窗收集参数
local ok, csv = reaper.GetUserInputs(
  "批量改名（关键词查找替换）",
  4,
  "查找内容,替换为,范围 1轨道名/2item名/3两者,区分大小写 y/n,extrawidth=120",
  "In,Out,3,n"
)
if not ok then return end

-- 解析输入（用 \n 分隔，避免内容里有逗号被误切）
local fields = {}
for field in (csv .. ","):gmatch("([^,]*),") do
  fields[#fields + 1] = field
end

local findStr   = fields[1] or ""
local replaceStr = fields[2] or ""
local scope     = tonumber(fields[3] or "3") or 3
local caseSens  = ((fields[4] or "n"):lower() == "y")

if findStr == "" then
  reaper.MB("“查找内容”不能为空。", "批量改名", 0)
  return
end

local doTracks = (scope == 1 or scope == 3)
local doItems  = (scope == 2 or scope == 3)

------------------------------------------------------------
-- 执行替换
------------------------------------------------------------
reaper.Undo_BeginBlock()
reaper.PreventUIRefresh(1)

local totalRepl = 0      -- 总替换处数
local namesChanged = 0   -- 改了多少个名字

for i = 0, trackCount - 1 do
  local track = reaper.GetSelectedTrack(0, i)

  -- 轨道名
  if doTracks then
    local _, name = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
    local newName, n = plainReplace(name, findStr, replaceStr, caseSens)
    if n > 0 then
      reaper.GetSetMediaTrackInfo_String(track, "P_NAME", newName, true)
      totalRepl = totalRepl + n
      namesChanged = namesChanged + 1
    end
  end

  -- 轨道内每个 item 的当前 take 名
  if doItems then
    local itemCount = reaper.CountTrackMediaItems(track)
    for j = 0, itemCount - 1 do
      local item = reaper.GetTrackMediaItem(track, j)
      local take = reaper.GetActiveTake(item)
      if take then
        local takeName = reaper.GetTakeName(take)
        local newName, n = plainReplace(takeName, findStr, replaceStr, caseSens)
        if n > 0 then
          reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", newName, true)
          totalRepl = totalRepl + n
          namesChanged = namesChanged + 1
        end
      end
    end
  end
end

reaper.PreventUIRefresh(-1)
reaper.UpdateArrange()
reaper.TrackList_AdjustWindows(false)
reaper.Undo_EndBlock("批量改名：" .. findStr .. " -> " .. replaceStr, -1)

------------------------------------------------------------
-- 结果反馈
------------------------------------------------------------
reaper.MB(
  string.format(
    "完成！\n\n查找：%s\n替换：%s\n\n共修改 %d 个名称，替换 %d 处。",
    findStr,
    (replaceStr == "" and "（删除）" or replaceStr),
    namesChanged,
    totalRepl
  ),
  "批量改名",
  0
)
