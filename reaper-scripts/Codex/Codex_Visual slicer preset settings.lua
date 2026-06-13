-- @description Codex - Visual slicer preset settings
-- @version 0.1.0
-- @author Codex
-- @about
--   Choose the preset used by "Codex - Visual slicer for selected item via source file".

local EXT_SECTION = "CodexVisualSlicer"

local PRESET_BY_NUMBER = {
  ["1"] = "balanced",
  ["2"] = "tight",
  ["3"] = "loose",
  ["4"] = "sensitive",
  ["5"] = "percussive",
}

local LABELS = {
  balanced = "Balanced visual blocks",
  tight = "Tight short tails",
  loose = "Loose long tails",
  sensitive = "Sensitive quiet variations",
  percussive = "Percussive rapid hits",
}

local current = reaper.GetExtState(EXT_SECTION, "preset")
if current == "" then
  current = "balanced"
end

local current_number = "1"
for number, name in pairs(PRESET_BY_NUMBER) do
  if name == current then
    current_number = number
    break
  end
end

local delete_gaps = reaper.GetExtState(EXT_SECTION, "delete_gaps")
if delete_gaps == "" then
  delete_gaps = "1"
end

local title = "Codex visual slicer preset"
local captions = "Preset 1 Balanced 2 Tight 3 Loose 4 Sensitive 5 Percussive,Delete gaps 1/0"
local defaults = current_number .. "," .. delete_gaps

local ok, csv = reaper.GetUserInputs(title, 2, captions, defaults)
if not ok then
  return
end

local values = {}
for value in string.gmatch(csv .. ",", "([^,]*),") do
  values[#values + 1] = value
end

local preset = PRESET_BY_NUMBER[values[1]] or current
local delete_value = tonumber(values[2]) == 0 and "0" or "1"

reaper.SetExtState(EXT_SECTION, "preset", preset, true)
reaper.SetExtState(EXT_SECTION, "delete_gaps", delete_value, true)

reaper.ShowMessageBox(
  "Visual slicer preset set to:\n" ..
  preset .. " - " .. (LABELS[preset] or "") ..
  "\n\nDelete gaps: " .. delete_value ..
  "\n\nRun the main visual slicer again after Undo if you want to recut the same item.",
  "Codex visual slicer",
  0
)
