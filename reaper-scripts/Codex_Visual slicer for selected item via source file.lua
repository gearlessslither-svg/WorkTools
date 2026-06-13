-- @description Codex - Visual slicer for selected item via source file
-- @version 0.2.0
-- @author Codex
-- @about
--   Select one audio item and run. The script calls codex_visual_event_analyzer.py
--   to inspect the source WAV like a visual peak/spectrogram strip, then splits
--   the selected item into detected blocks and deletes the dark gaps. It uses a
--   remembered preset, defaulting to Balanced visual blocks.

local SCRIPT_DIR = ({ reaper.get_action_context() })[2]:match("^(.*)[/\\]")
local ANALYZER = SCRIPT_DIR .. "/codex_visual_event_analyzer.py"
local EVENTS_TSV = "C:/Users/user1/Documents/Reaper/codex_visual_events.tsv"
local REPORT_PATH = "C:/Users/user1/Documents/Reaper/codex_visual_slice_report.txt"
local EXT_SECTION = "CodexVisualSlicer"

local PRESETS = {
  balanced = {
    label = "Balanced visual blocks",
    hop_ms = 5,
    smoothing_ms = 25,
    visible_below_peak_db = 54,
    floor_margin_db = 9,
    min_dark_gap_ms = 140,
    min_event_ms = 80,
    pre_pad_ms = 15,
    tail_pad_ms = 110,
  },
  tight = {
    label = "Tight short tails",
    hop_ms = 5,
    smoothing_ms = 20,
    visible_below_peak_db = 42,
    floor_margin_db = 9,
    min_dark_gap_ms = 100,
    min_event_ms = 60,
    pre_pad_ms = 6,
    tail_pad_ms = 45,
  },
  loose = {
    label = "Loose long tails",
    hop_ms = 5,
    smoothing_ms = 30,
    visible_below_peak_db = 66,
    floor_margin_db = 9,
    min_dark_gap_ms = 160,
    min_event_ms = 80,
    pre_pad_ms = 20,
    tail_pad_ms = 180,
  },
  sensitive = {
    label = "Sensitive quiet variations",
    hop_ms = 5,
    smoothing_ms = 25,
    visible_below_peak_db = 66,
    floor_margin_db = 6,
    min_dark_gap_ms = 120,
    min_event_ms = 45,
    pre_pad_ms = 20,
    tail_pad_ms = 140,
  },
  percussive = {
    label = "Percussive rapid hits",
    hop_ms = 2.5,
    smoothing_ms = 10,
    visible_below_peak_db = 36,
    floor_margin_db = 9,
    min_dark_gap_ms = 45,
    min_event_ms = 20,
    pre_pad_ms = 3,
    tail_pad_ms = 35,
  },
}

local function copy_preset(preset)
  local out = {}
  for key, value in pairs(preset) do
    out[key] = value
  end
  return out
end

local function read_active_params()
  local preset_name = reaper.GetExtState(EXT_SECTION, "preset")
  if preset_name == "" or not PRESETS[preset_name] then
    preset_name = "balanced"
  end

  local params = copy_preset(PRESETS[preset_name])
  params.preset_name = preset_name

  local delete_gaps = reaper.GetExtState(EXT_SECTION, "delete_gaps")
  params.delete_gaps = delete_gaps ~= "0"

  return params
end

local PARAMS = read_active_params()

local function q(s)
  return '"' .. tostring(s):gsub('"', '\\"') .. '"'
end

local function write_report(lines)
  local f = io.open(REPORT_PATH, "w")
  if f then
    f:write(table.concat(lines, "\n"))
    f:close()
  end
end

local function read_tsv(path)
  local rows = {}
  for line in io.lines(path) do
    local row = {}
    for value in string.gmatch(line .. "\t", "([^\t]*)\t") do
      row[#row + 1] = value
    end
    rows[#rows + 1] = row
  end
  return rows
end

local function contains_time(intervals, time)
  for _, interval in ipairs(intervals) do
    if time >= interval.start and time <= interval.finish then
      return true
    end
  end
  return false
end

local function split_by_events(item, events)
  local item_start = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
  local item_end = item_start + reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
  local eps = 0.000001
  local boundaries = {}

  for _, event in ipairs(events) do
    if event.start > item_start + eps and event.start < item_end - eps then
      boundaries[#boundaries + 1] = event.start
    end
    if event.finish > item_start + eps and event.finish < item_end - eps then
      boundaries[#boundaries + 1] = event.finish
    end
  end

  table.sort(boundaries, function(a, b) return a > b end)

  local fragments = {}
  local left = item
  for _, boundary in ipairs(boundaries) do
    local right = reaper.SplitMediaItem(left, boundary)
    if right then
      fragments[#fragments + 1] = right
    end
  end
  fragments[#fragments + 1] = left

  local kept = 0
  local deleted = 0
  for _, fragment in ipairs(fragments) do
    local s = reaper.GetMediaItemInfo_Value(fragment, "D_POSITION")
    local e = s + reaper.GetMediaItemInfo_Value(fragment, "D_LENGTH")
    local center = (s + e) * 0.5
    reaper.SetMediaItemSelected(fragment, false)
    if contains_time(events, center) then
      reaper.SetMediaItemSelected(fragment, true)
      kept = kept + 1
    elseif PARAMS.delete_gaps then
      local track = reaper.GetMediaItem_Track(fragment)
      reaper.DeleteTrackMediaItem(track, fragment)
      deleted = deleted + 1
    end
  end

  return kept, deleted
end

local item = reaper.GetSelectedMediaItem(0, 0)
if not item then
  write_report({ "No selected item. Nothing sliced." })
  return
end

local take = reaper.GetActiveTake(item)
if not take or reaper.TakeIsMIDI(take) then
  write_report({ "First selected item has no active audio take. Nothing sliced." })
  return
end

local source = reaper.GetMediaItemTake_Source(take)
local source_file = source and reaper.GetMediaSourceFileName(source, "") or ""
if source_file == "" then
  write_report({ "Could not resolve selected take source file. Nothing sliced." })
  return
end

local item_start = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
local item_len = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
local startoffs = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS")
local playrate = reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")
if not playrate or playrate == 0 then
  playrate = 1
end
local source_len = item_len * playrate

local cmd = table.concat({
  "python",
  q(ANALYZER),
  "--input", q(source_file),
  "--out", q(EVENTS_TSV),
  "--startoffs", tostring(startoffs),
  "--length", tostring(source_len),
  "--hop-ms", tostring(PARAMS.hop_ms),
  "--smoothing-ms", tostring(PARAMS.smoothing_ms),
  "--visible-below-peak-db", tostring(PARAMS.visible_below_peak_db),
  "--floor-margin-db", tostring(PARAMS.floor_margin_db),
  "--min-dark-gap-ms", tostring(PARAMS.min_dark_gap_ms),
  "--min-event-ms", tostring(PARAMS.min_event_ms),
  "--pre-pad-ms", tostring(PARAMS.pre_pad_ms),
  "--tail-pad-ms", tostring(PARAMS.tail_pad_ms),
}, " ") .. " 2>&1"

local pipe = io.popen(cmd)
local analyzer_output = pipe and pipe:read("*a") or ""
if pipe then
  pipe:close()
end

local rows = read_tsv(EVENTS_TSV)
local status = rows[1] and rows[1][2] or "ERROR"
if status ~= "OK" then
  write_report({
    "Analyzer failed. Nothing sliced.",
    "Command: " .. cmd,
    "Output: " .. analyzer_output,
  })
  return
end

local source_events = {}
local params = {}
local stats = {}
for _, row in ipairs(rows) do
  if row[1] == "EVENT" then
    source_events[#source_events + 1] = {
      source_start = tonumber(row[3]),
      source_finish = tonumber(row[4]),
      source_length = tonumber(row[5]),
    }
  elseif row[1] == "PARAM" then
    params[row[2]] = row[3]
  elseif row[1] == "STAT" then
    stats[row[2]] = row[3]
  end
end

if #source_events == 0 then
  write_report({
    "Analyzer detected 0 visual events. Nothing sliced.",
    "Source: " .. source_file,
    "Analyzer output: " .. analyzer_output,
  })
  return
end

local item_end = item_start + item_len
local project_events = {}
for _, event in ipairs(source_events) do
  local s = item_start + (event.source_start - startoffs) / playrate
  local e = item_start + (event.source_finish - startoffs) / playrate
  s = math.max(item_start, s)
  e = math.min(item_end, e)
  if e > s then
    project_events[#project_events + 1] = {
      start = s,
      finish = e,
      source_start = event.source_start,
      source_finish = event.source_finish,
    }
  end
end

reaper.Undo_BeginBlock()
reaper.PreventUIRefresh(1)
local kept, deleted = split_by_events(item, project_events)
reaper.PreventUIRefresh(-1)
reaper.UpdateArrange()
reaper.Undo_EndBlock("Codex: visual slice selected item via source file", -1)

local lines = {}
lines[#lines + 1] = "Codex visual slice report"
lines[#lines + 1] = "Source: " .. source_file
lines[#lines + 1] = "Preset: " .. tostring(PARAMS.preset_name) .. " - " .. tostring(PARAMS.label)
lines[#lines + 1] = "Detected events: " .. tostring(#project_events)
lines[#lines + 1] = "Kept items: " .. tostring(kept)
lines[#lines + 1] = "Deleted gap fragments: " .. tostring(deleted)
lines[#lines + 1] = ""
lines[#lines + 1] = "Parameters"
for _, key in ipairs({
  "hop_ms",
  "smoothing_ms",
  "visible_below_peak_db",
  "floor_margin_db",
  "min_dark_gap_ms",
  "min_event_ms",
  "pre_pad_ms",
  "tail_pad_ms",
}) do
  lines[#lines + 1] = key .. "=" .. tostring(params[key] or PARAMS[key])
end
lines[#lines + 1] = "delete_gaps=" .. tostring(PARAMS.delete_gaps)
lines[#lines + 1] = ""
lines[#lines + 1] = "Measured"
for _, key in ipairs({ "sample_rate", "duration", "max_db", "floor_db_p20", "visible_threshold_db", "dark_gap_count" }) do
  lines[#lines + 1] = key .. "=" .. tostring(stats[key] or "")
end
lines[#lines + 1] = ""
lines[#lines + 1] = "Events"
for i, event in ipairs(project_events) do
  lines[#lines + 1] = string.format(
    "%02d source_start=%.3fs source_finish=%.3fs project_start=%.6f project_finish=%.6f length=%.3fs",
    i,
    event.source_start,
    event.source_finish,
    event.start,
    event.finish,
    event.finish - event.start
  )
end

write_report(lines)
