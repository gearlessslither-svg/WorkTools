-- Codex OBS/Reaper bridge helper.
-- Creates a visible OBS capture routing marker and adds ReaStream to the
-- master track so OBS can receive REAPER's ASIO playback through ReaStream.

local TRACK_NAME = "OBS Capture - ReaStream Send"
local FX_NAME = "ReaStream (Cockos)"
local IDENTIFIER = "Codex_OBS"

local function native_color(r, g, b)
  return reaper.ColorToNative(r, g, b) | 0x1000000
end

local function find_track_by_name(name)
  local count = reaper.CountTracks(0)
  for i = 0, count - 1 do
    local track = reaper.GetTrack(0, i)
    local ok, existing = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
    if ok and existing == name then
      return track
    end
  end
  return nil
end

local function ensure_marker_track()
  local track = find_track_by_name(TRACK_NAME)
  if not track then
    local index = reaper.CountTracks(0)
    reaper.InsertTrackAtIndex(index, true)
    track = reaper.GetTrack(0, index)
    reaper.GetSetMediaTrackInfo_String(track, "P_NAME", TRACK_NAME, true)
    reaper.SetMediaTrackInfo_Value(track, "I_CUSTOMCOLOR", native_color(75, 148, 214))
    reaper.SetMediaTrackInfo_Value(track, "B_MUTE", 1)
  end

  reaper.GetSetMediaTrackInfo_String(
    track,
    "P_EXT:codex_obs_bridge",
    "Marker only. Actual OBS capture send is on the Master track via ReaStream identifier " .. IDENTIFIER .. ".",
    true
  )
  return track
end

local function find_master_reastream(master)
  local fx_count = reaper.TrackFX_GetCount(master)
  for i = 0, fx_count - 1 do
    local ok, name = reaper.TrackFX_GetFXName(master, i, "")
    if ok and name:lower():find("reastream", 1, true) then
      return i
    end
  end
  return -1
end

local function ensure_master_reastream()
  local master = reaper.GetMasterTrack(0)
  local fx = find_master_reastream(master)
  if fx < 0 then
    fx = reaper.TrackFX_AddByName(master, FX_NAME, false, -1)
  end
  if fx >= 0 then
    reaper.TrackFX_SetEnabled(master, fx, true)
    reaper.TrackFX_Show(master, fx, 3)
  end
  return fx
end

reaper.Undo_BeginBlock()
reaper.PreventUIRefresh(1)

ensure_marker_track()
local fx_index = ensure_master_reastream()

reaper.PreventUIRefresh(-1)
reaper.TrackList_AdjustWindows(false)
reaper.UpdateArrange()
reaper.Undo_EndBlock("Codex: Setup OBS ReaStream capture send", -1)

local fx_status = fx_index >= 0 and "已在 Master FX 上找到/添加 ReaStream。" or "没有成功添加 ReaStream，请确认 ReaStream 插件可用。"
local message = fx_status .. "\n\n" ..
  "一次性设置建议：\n" ..
  "1. 在刚打开的 Master ReaStream 窗口选择 Send audio/MIDI。\n" ..
  "2. Identifier 使用 " .. IDENTIFIER .. "（如果 OBS 端已经用 default，也可以两边都用 default）。\n" ..
  "3. 发送目标选择 * local broadcast / local broadcast。\n" ..
  "4. OBS 端 ReaStream VST filter 选择 Receive audio/MIDI，并使用同一个 Identifier。\n\n" ..
  "之后就可以用 Codex OBS/Reaper Bridge 的“一键抓取”来启动 OBS 录制。"

reaper.ShowMessageBox(message, "Codex OBS/Reaper Bridge", 0)
