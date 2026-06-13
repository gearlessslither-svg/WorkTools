--[[
  Reaper_SpaceItems_Gap.lua
  ------------------------------------------------------------
  功能：把【选中的多个 item】按顺序重新排列，让每相邻两个之间
        都保持你填写的固定间隔（空隙）。

  使用方法：
    1. 选中多个 item（通常是紧挨着的一排）
    2. 运行本脚本
    3. 在弹窗里填写：
         间隔             相邻两个 item 之间的空隙
                          正数=留空隙；0=首尾相接；负数=互相重叠
         单位             s=秒  ms=毫秒  b=拍(beat)
    4. 第一个 item 位置不动，后面的依次往后排，间隔统一为你填的值
       （可一次 Ctrl+Z 撤销）

  作者：EF Audio Tools
]]--

local itemCount = reaper.CountSelectedMediaItems(0)
if itemCount < 2 then
  reaper.MB("请至少选中 2 个 item 再运行本脚本。", "等间隔排列", 0)
  return
end

-- 弹窗收集参数
local ok, csv = reaper.GetUserInputs(
  "等间隔排列 items",
  2,
  "间隔(正=空隙 负=重叠),单位 s/ms/b",
  "0.5,s"
)
if not ok then return end

local fields = {}
for field in (csv .. ","):gmatch("([^,]*),") do
  fields[#fields + 1] = field
end

local gapVal = tonumber(fields[1])
local unit   = (fields[2] or "s"):lower()

if gapVal == nil then
  reaper.MB("“间隔”必须是一个数字。", "等间隔排列", 0)
  return
end

------------------------------------------------------------
-- 收集并按位置排序选中的 item
------------------------------------------------------------
local items = {}
for i = 0, itemCount - 1 do
  local item = reaper.GetSelectedMediaItem(0, i)
  local pos  = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
  items[#items + 1] = { item = item, pos = pos }
end
table.sort(items, function(a, b) return a.pos < b.pos end)

------------------------------------------------------------
-- 把间隔换算成秒（拍单位需逐个按所在位置的速度换算）
------------------------------------------------------------
local function gapInSeconds(atPosSec)
  if unit == "ms" then
    return gapVal / 1000.0
  elseif unit == "b" then
    -- 拍 -> 秒：用该位置的实际速度换算
    local qnNow  = reaper.TimeMap2_timeToQN(0, atPosSec)
    local secEnd = reaper.TimeMap2_QNToTime(0, qnNow + gapVal)
    return secEnd - atPosSec
  else
    return gapVal -- 秒
  end
end

------------------------------------------------------------
-- 执行：第一个不动，后面依次接排
------------------------------------------------------------
reaper.Undo_BeginBlock()
reaper.PreventUIRefresh(1)

-- 链式光标：从第一个 item 的结尾开始
local firstItem = items[1].item
local cursor = items[1].pos
            + reaper.GetMediaItemInfo_Value(firstItem, "D_LENGTH")

for i = 2, #items do
  local entry = items[i]
  local item  = entry.item
  local len   = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")

  local thisGap = gapInSeconds(cursor)
  local newPos  = cursor + thisGap
  if newPos < 0 then newPos = 0 end

  reaper.SetMediaItemInfo_Value(item, "D_POSITION", newPos)
  cursor = newPos + len
end

reaper.PreventUIRefresh(-1)
reaper.UpdateArrange()
reaper.Undo_EndBlock("等间隔排列 items（间隔 " .. gapVal .. unit .. "）", -1)
