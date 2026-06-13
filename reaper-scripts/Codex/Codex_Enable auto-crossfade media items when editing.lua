-- @description Codex - Enable auto-crossfade media items when editing
-- @version 1.0
-- @author Codex

local AUTO_CROSSFADE_CMD = 40041

local function get_state()
  if reaper.GetToggleCommandStateEx then
    return reaper.GetToggleCommandStateEx(0, AUTO_CROSSFADE_CMD)
  end
  return reaper.GetToggleCommandState(AUTO_CROSSFADE_CMD)
end

if get_state() == 0 then
  reaper.Main_OnCommand(AUTO_CROSSFADE_CMD, 0)
end
