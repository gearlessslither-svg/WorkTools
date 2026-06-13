-- @description Wwise Bridge - Region Container Exporter
-- @version 20.3
-- @author Codex
-- @about
--   Scans REAPER regions, lets you choose regions in batches, renders them,
--   creates Random or Switch Containers in Wwise via ReaWwise WAAPI, and
--   assigns container-level Switch/State value buckets instead of individual
--   Sound SFX resources. Names ending with a Switch/State value, such as
--   Lure_Small, can be grouped under a shared parent Switch Container.

local r = reaper

local SCRIPT_TITLE = "Wwise Bridge - Region Container Exporter"
local EXT_SECTION = "Codex_WwiseRegionBridge"
local ACTION_RENDER_RECENT = 42230 -- File: Render project, using the most recent render settings, auto-close render dialog

local settings = {
  render_path = "",
  render_ext = "wav",
  render_regions = true,
  import_to_wwise = true,
  originals_subfolder = "",
  import_operation_idx = 0, -- useExisting
  wwise_root_path = "\\Actor-Mixer Hierarchy\\Default Work Unit",
  auto_save_wwise = false,
}

local import_operations = { "useExisting", "replaceExisting", "createNew" }
local output_mode_combo = "Export local files only\0Export local files + import to Wwise\0Import existing local files to Wwise\0"
local import_operation_combo = "useExisting\0replaceExisting\0createNew\0"
local container_type_combo = "Random Container\0Switch Container\0"
local scan_filter = ""
local wwise_filter = ""
local region_count_total = 0
local selected_region_count = 0
local status_text = "Ready."
local process_log = {}
local last_clicked_region = nil -- anchor row for Shift-range / Alt-toggle multi-select

local regions = {}
local groups = {}
local groups_by_name = {}
local wwise_nodes = {}
local wwise_switch_groups = {}
local wwise_connected = false
local wwise_info = "Not connected"
local active_render_state = nil
local ui_refresh_locked = false
local undo_open = false

local ctx = nil

local function Log(text)
  process_log[#process_log + 1] = tostring(text)
  r.ShowConsoleMsg(tostring(text) .. "\n")
end

local function SaveSetting(key, value)
  r.SetExtState(EXT_SECTION, key, tostring(value), true)
end

local function LoadSetting(key, default)
  local value = r.GetExtState(EXT_SECTION, key)
  if value == nil or value == "" then return default end
  return value
end

local function LoadSettings()
  settings.render_path = LoadSetting("render_path", settings.render_path)
  settings.render_ext = LoadSetting("render_ext", settings.render_ext)
  settings.originals_subfolder = LoadSetting("originals_subfolder", settings.originals_subfolder)
  settings.wwise_root_path = LoadSetting("wwise_root_path", settings.wwise_root_path)
  settings.render_regions = LoadSetting("render_regions", "1") ~= "0"
  settings.import_to_wwise = LoadSetting("import_to_wwise", "1") ~= "0"
  settings.auto_save_wwise = LoadSetting("auto_save_wwise", "0") == "1"
  settings.import_operation_idx = tonumber(LoadSetting("import_operation_idx", "0")) or 0
end

local function SaveSettings()
  SaveSetting("render_path", settings.render_path)
  SaveSetting("render_ext", settings.render_ext)
  SaveSetting("originals_subfolder", settings.originals_subfolder)
  SaveSetting("wwise_root_path", settings.wwise_root_path)
  SaveSetting("render_regions", settings.render_regions and "1" or "0")
  SaveSetting("import_to_wwise", settings.import_to_wwise and "1" or "0")
  SaveSetting("auto_save_wwise", settings.auto_save_wwise and "1" or "0")
  SaveSetting("import_operation_idx", tostring(settings.import_operation_idx))
end

local function OutputModeIndex()
  if settings.import_to_wwise then
    if settings.render_regions then return 1 end
    return 2
  end
  return 0
end

local function ApplyOutputModeIndex(index)
  if index == 0 then
    settings.render_regions = true
    settings.import_to_wwise = false
  elseif index == 2 then
    settings.render_regions = false
    settings.import_to_wwise = true
  else
    settings.render_regions = true
    settings.import_to_wwise = true
  end
end

local function ToReaperPath(path)
  return (path or ""):gsub("\\", "/"):gsub("//+", "/")
end

local function ToWwisePath(path)
  local p = (path or ""):gsub("/", "\\")
  p = p:gsub("\\\\+", "\\")
  if #p > 1 and p:sub(-1) == "\\" then p = p:sub(1, -2) end
  return p
end

local function PathJoin(a, b)
  if not a or a == "" then return b or "" end
  if not b or b == "" then return a end
  local sep = "/"
  return ToReaperPath(a .. sep .. b)
end

local function FileExists(path)
  local f = io.open(path, "rb")
  if f then f:close() return true end
  return false
end

local function SanitizeName(name)
  local n = tostring(name or "")
  n = n:gsub("[\\/:*?\"<>|]", "_")
  n = n:gsub("[%c]", "_")
  n = n:gsub("^%s+", ""):gsub("%s+$", "")
  n = n:gsub("_+", "_")
  if n == "" or n == "." or n == ".." then n = "Unnamed" end
  return n
end

local function FormatTime(seconds)
  return string.format("%.3f", tonumber(seconds) or 0)
end

local function Lower(text)
  return tostring(text or ""):lower()
end

local function MatchesFilter(text, filter)
  filter = Lower(filter)
  if filter == "" then return true end
  return Lower(text):find(filter, 1, true) ~= nil
end

local function IsChildPath(path, parent_path)
  local p = ToWwisePath(path)
  local parent = ToWwisePath(parent_path)
  if p == "" or parent == "" or Lower(p) == Lower(parent) then return false end
  return Lower(p:sub(1, #parent + 1)) == Lower(parent .. "\\")
end

local function SelectedRegionsForGroup(group)
  local out = {}
  for _, region_index in ipairs(group.region_indices) do
    local region = regions[region_index]
    if region and region.selected then out[#out + 1] = region_index end
  end
  table.sort(out, function(a, b) return regions[a].pos < regions[b].pos end)
  return out
end

local function ChildNameForSelectedIndex(group, selected_index)
  return string.format("%s_%02d", group.safe_name, selected_index)
end

local function ChildNameForRegion(group, region_index)
  local selected = SelectedRegionsForGroup(group)
  for i, idx in ipairs(selected) do
    if idx == region_index then return ChildNameForSelectedIndex(group, i) end
  end
  return string.format("%s_--", group.safe_name)
end

local function SelectedCountForGroup(group)
  local count = 0
  for _, region_index in ipairs(group.region_indices) do
    if regions[region_index] and regions[region_index].selected then count = count + 1 end
  end
  return count
end

local function SwitchGroupById(id)
  if not id or id == "" then return nil end
  for _, group in ipairs(wwise_switch_groups) do
    if group.id == id then return group end
  end
  return nil
end

local function SwitchValueById(switch_group, id)
  if not switch_group or not switch_group.children or not id or id == "" then return nil end
  for _, child in ipairs(switch_group.children) do
    if child.id == id then return child end
  end
  return nil
end

local function FirstAssignedSwitchValue(group, switch_group)
  if not group.assignments or not switch_group then return "" end
  local selected = SelectedRegionsForGroup(group)
  for _, region_index in ipairs(selected) do
    local old_id = group.assignments[region_index]
    if SwitchValueById(switch_group, old_id) then return old_id end
  end
  return ""
end

local function NameTokenParts(text)
  local parts = {}
  for raw in tostring(text or ""):gmatch("[%w]+") do
    parts[#parts + 1] = { raw = raw, key = Lower(raw) }
  end
  return parts
end

local function TokenPartsEndWith(parts, suffix)
  if not parts or not suffix or #suffix == 0 or #parts < #suffix then return false end
  local offset = #parts - #suffix
  for i = 1, #suffix do
    if parts[offset + i].key ~= suffix[i].key then return false end
  end
  return true
end

local function JoinTokenParts(parts, start_index, end_index, sep)
  local names = {}
  for i = start_index, end_index do
    if parts[i] then names[#names + 1] = parts[i].raw end
  end
  return table.concat(names, sep or "_")
end

local function CommonSwitchValuePrefixLength(switch_group)
  if not switch_group or not switch_group.children or #switch_group.children == 0 then return 0 end
  local first = NameTokenParts(switch_group.children[1].name)
  local prefix_len = #first
  for i = 2, #switch_group.children do
    local parts = NameTokenParts(switch_group.children[i].name)
    local j = 1
    while j <= prefix_len and j <= #parts and first[j].key == parts[j].key do
      j = j + 1
    end
    prefix_len = j - 1
  end
  return prefix_len
end

local function SwitchValueSuffixParts(switch_group, switch_value)
  local parts = NameTokenParts(switch_value and switch_value.name or "")
  if #parts == 0 then return parts end
  local prefix_len = CommonSwitchValuePrefixLength(switch_group)
  if prefix_len >= #parts then prefix_len = #parts - 1 end
  local suffix = {}
  for i = prefix_len + 1, #parts do
    suffix[#suffix + 1] = parts[i]
  end
  if #suffix == 0 then suffix[#suffix + 1] = parts[#parts] end
  return suffix
end

local function SwitchValueSuffixText(switch_group, switch_value)
  local suffix = SwitchValueSuffixParts(switch_group, switch_value)
  if #suffix == 0 then return switch_value and switch_value.name or "Value" end
  return JoinTokenParts(suffix, 1, #suffix, "_")
end

local function GuessValueSeparator(source_name)
  local text = tostring(source_name or "")
  if text:find("_", 1, true) then return "_" end
  if text:find("-", 1, true) then return "-" end
  if text:find("%s") then return " " end
  return "_"
end

local function InferParentBucketFromName(group, switch_group)
  if not group or not switch_group or not switch_group.children or #switch_group.children == 0 then return nil end
  local source_parts = NameTokenParts(group.safe_name)
  if #source_parts < 2 then return nil end

  local best = nil
  local ambiguous = false
  for _, switch_value in ipairs(switch_group.children) do
    local candidate_suffixes = {
      SwitchValueSuffixParts(switch_group, switch_value),
      NameTokenParts(switch_value.name),
    }
    for _, suffix in ipairs(candidate_suffixes) do
      if TokenPartsEndWith(source_parts, suffix) then
        local parent_end = #source_parts - #suffix
        if parent_end >= 1 then
          local candidate = {
            switch_value = switch_value,
            parent_name = SanitizeName(JoinTokenParts(source_parts, 1, parent_end, "_")),
            value_container_name = SanitizeName(group.safe_name),
            value_separator = GuessValueSeparator(group.safe_name),
            score = #suffix,
          }
          if not best or candidate.score > best.score then
            best = candidate
            ambiguous = false
          elseif best and candidate.score == best.score and candidate.switch_value.id ~= best.switch_value.id then
            ambiguous = true
          end
        end
      end
    end
  end

  if ambiguous then return nil end
  return best
end

local function ApplyParentInference(group, switch_group)
  local inferred = InferParentBucketFromName(group, switch_group)
  if not inferred then return false end
  group.container_type_idx = 1
  group.switch_group_id = switch_group.id
  group.switch_value_id = inferred.switch_value.id
  group.use_parent_container = true
  group.parent_name = inferred.parent_name
  group.value_container_name = inferred.value_container_name
  group.value_separator = inferred.value_separator
  group.auto_parent_detected = true
  return true
end

local function InferGroupFromName(group)
  if not group then return false, "missing group" end
  local selected_switch_group = SwitchGroupById(group.switch_group_id)
  if selected_switch_group then
    if ApplyParentInference(group, selected_switch_group) then return true end
    return false, "no value suffix match"
  end

  local match = nil
  local ambiguous = false
  for _, switch_group in ipairs(wwise_switch_groups) do
    if InferParentBucketFromName(group, switch_group) then
      if match then
        ambiguous = true
      else
        match = switch_group
      end
    end
  end
  if ambiguous then return false, "ambiguous switch group" end
  if match and ApplyParentInference(group, match) then return true end
  return false, "no value suffix match"
end

local function EnsureSwitchAssignments(group)
  if group.container_type_idx ~= 1 then return end
  local switch_group = SwitchGroupById(group.switch_group_id)
  if not switch_group then
    if #wwise_switch_groups > 0 then group.switch_value_id = "" end
    return
  end
  if not SwitchValueById(switch_group, group.switch_value_id) then
    group.switch_value_id = FirstAssignedSwitchValue(group, switch_group)
  end
end

local function RebuildGroups(preserve)
  preserve = preserve or {}
  groups = {}
  groups_by_name = {}
  selected_region_count = 0

  for i, region in ipairs(regions) do
    if region.selected then selected_region_count = selected_region_count + 1 end
    local name = region.safe_name
    local group = groups_by_name[name]
    if not group then
      local old = preserve[name] or {}
      group = {
        raw_name = region.raw_name,
        safe_name = name,
        region_indices = {},
        container_type_idx = old.container_type_idx or 0,
        switch_group_id = old.switch_group_id or "",
        switch_value_id = old.switch_value_id or "",
        use_parent_container = old.use_parent_container or false,
        parent_name = old.parent_name or "",
        value_container_name = old.value_container_name or "",
        value_separator = old.value_separator or "_",
        create_missing_value_buckets = old.create_missing_value_buckets or false,
        auto_parent_detected = old.auto_parent_detected or false,
        assignments = old.assignments or {},
      }
      groups_by_name[name] = group
      groups[#groups + 1] = group
    end
    group.region_indices[#group.region_indices + 1] = i
  end

  table.sort(groups, function(a, b) return Lower(a.safe_name) < Lower(b.safe_name) end)
  for _, group in ipairs(groups) do
    table.sort(group.region_indices, function(a, b) return regions[a].pos < regions[b].pos end)
    EnsureSwitchAssignments(group)
  end
end

local function CaptureGroupConfig()
  local preserve = {}
  for _, group in ipairs(groups) do
    preserve[group.safe_name] = {
      container_type_idx = group.container_type_idx,
      switch_group_id = group.switch_group_id,
      switch_value_id = group.switch_value_id,
      use_parent_container = group.use_parent_container,
      parent_name = group.parent_name,
      value_container_name = group.value_container_name,
      value_separator = group.value_separator,
      create_missing_value_buckets = group.create_missing_value_buckets,
      auto_parent_detected = group.auto_parent_detected,
      assignments = group.assignments,
    }
  end
  return preserve
end

local function Project()
  return r.EnumProjects(-1, "")
end

local function DefaultRenderPath()
  local path = r.GetProjectPath()
  if not path or path == "" then
    path = r.GetResourcePath()
  end
  return ToReaperPath(PathJoin(path, "Rendered_Assets"))
end

local function ScanRegions()
  local preserve = CaptureGroupConfig()
  regions = {}
  region_count_total = 0

  local proj = Project()
  local i = 0
  while true do
    local retval, isrgn, pos, rgnend, name, idx, color = r.EnumProjectMarkers3(proj, i)
    if retval == 0 then break end
    if isrgn then
      region_count_total = region_count_total + 1
      local safe_name = SanitizeName(name)
      regions[#regions + 1] = {
        marker_index = idx,
        raw_name = name ~= "" and name or "Unnamed",
        safe_name = safe_name,
        pos = pos,
        rgnend = rgnend,
        color = color,
        selected = true,
      }
    end
    i = i + 1
  end

  table.sort(regions, function(a, b) return a.pos < b.pos end)
  if settings.render_path == "" then settings.render_path = DefaultRenderPath() end
  RebuildGroups(preserve)
  status_text = string.format("Scanned %d regions. Selected %d.", region_count_total, selected_region_count)
end

local function SelectAllRegions(value, only_visible)
  for _, region in ipairs(regions) do
    if not only_visible or MatchesFilter(region.raw_name .. " " .. region.safe_name, scan_filter) then
      region.selected = value
    end
  end
  RebuildGroups(CaptureGroupConfig())
end

local function SelectRegionsInTimeSelection()
  local start_pos, end_pos = r.GetSet_LoopTimeRange(false, false, 0, 0, false)
  if start_pos == end_pos then
    status_text = "No time selection."
    return
  end
  for _, region in ipairs(regions) do
    region.selected = (region.rgnend > start_pos + 0.001) and (region.pos < end_pos - 0.001)
  end
  RebuildGroups(CaptureGroupConfig())
end

local function SelectRegionsOverSelectedItems()
  local proj = Project()
  local item_count = r.CountSelectedMediaItems(proj)
  if item_count == 0 then
    status_text = "No selected media items."
    return
  end

  local ranges = {}
  for i = 0, item_count - 1 do
    local item = r.GetSelectedMediaItem(proj, i)
    local pos = r.GetMediaItemInfo_Value(item, "D_POSITION")
    local len = r.GetMediaItemInfo_Value(item, "D_LENGTH")
    ranges[#ranges + 1] = { pos = pos, end_pos = pos + len }
  end

  for _, region in ipairs(regions) do
    region.selected = false
    for _, range in ipairs(ranges) do
      if (region.rgnend > range.pos + 0.001) and (region.pos < range.end_pos - 0.001) then
        region.selected = true
        break
      end
    end
  end
  RebuildGroups(CaptureGroupConfig())
end

local function LuaToAkJson(value)
  local t = type(value)
  if t == "table" then
    local is_array = true
    local max_index = 0
    local count = 0
    for k, _ in pairs(value) do
      if type(k) ~= "number" then is_array = false break end
      if k > max_index then max_index = k end
      count = count + 1
    end
    if is_array and count > 0 and max_index == count then
      local arr = r.AK_AkJson_Array()
      for _, child in ipairs(value) do
        r.AK_AkJson_Array_Add(arr, LuaToAkJson(child))
      end
      return arr
    end
    local map = r.AK_AkJson_Map()
    for k, child in pairs(value) do
      r.AK_AkJson_Map_Set(map, tostring(k), LuaToAkJson(child))
    end
    return map
  elseif t == "string" then
    return r.AK_AkVariant_String(value)
  elseif t == "number" then
    return r.AK_AkVariant_Double(value)
  elseif t == "boolean" then
    return r.AK_AkVariant_Bool(value)
  elseif value == nil then
    return r.AK_AkVariant_String("")
  end
  return r.AK_AkVariant_String(tostring(value))
end

local function AkStatus(result)
  if not result then return false end
  if not r.AK_AkJson_GetStatus then return true end
  local ok, status = pcall(r.AK_AkJson_GetStatus, result)
  if ok then return status end
  return true
end

local function AkMapGet(map, key)
  if not map or not r.AK_AkJson_Map_Get then return nil end
  local ok, value = pcall(r.AK_AkJson_Map_Get, map, key)
  if ok then return value end
  return nil
end

local function AkArraySize(arr)
  if not arr or not r.AK_AkJson_Array_Size then return 0 end
  local ok, size = pcall(r.AK_AkJson_Array_Size, arr)
  if ok and size then return size end
  return 0
end

local function AkArrayGet(arr, index)
  if not arr or not r.AK_AkJson_Array_Get then return nil end
  local ok, value = pcall(r.AK_AkJson_Array_Get, arr, index)
  if ok then return value end
  return nil
end

local function AkString(value)
  if not value then return "" end
  if r.AK_AkVariant_GetString then
    local ok, text = pcall(r.AK_AkVariant_GetString, value)
    if ok and text ~= nil then return tostring(text) end
  end
  if r.AK_AkVariant_GetDouble then
    local ok, number = pcall(r.AK_AkVariant_GetDouble, value)
    if ok and number ~= nil then return tostring(number) end
  end
  return ""
end

local function AkFieldString(map, key)
  return AkString(AkMapGet(map, key))
end

local function AkError(result)
  local message = AkFieldString(result, "message")
  if message ~= "" then return message end
  return "WAAPI call failed."
end

local function CheckDependencies(show_message)
  if not r.ImGui_CreateContext then
    if show_message then r.ShowMessageBox("ReaImGui is missing.", SCRIPT_TITLE, 0) end
    return false
  end
  if not r.AK_Waapi_Connect or not r.AK_Waapi_Call then
    if show_message then r.ShowMessageBox("ReaWwise is missing. Install/enable ReaWwise first.", SCRIPT_TITLE, 0) end
    return false
  end
  return true
end

local function ConnectWaapi(show_message)
  if not CheckDependencies(show_message) then return false end
  if wwise_connected then return true end
  local ok = r.AK_Waapi_Connect("127.0.0.1", 8080)
  if not ok then
    wwise_connected = false
    wwise_info = "Disconnected"
    if show_message then
      r.ShowMessageBox("Cannot connect to Wwise. Open Wwise and enable Authoring API.", SCRIPT_TITLE, 0)
    end
    return false
  end
  wwise_connected = true
  wwise_info = "Connected"
  return true
end

local function DisconnectWaapi()
  if wwise_connected and r.AK_Waapi_Disconnect then
    pcall(r.AK_Waapi_Disconnect)
  end
  wwise_connected = false
end

local function WaapiCall(command, args, options)
  if not ConnectWaapi(false) then return false, nil, "Not connected" end
  local result = r.AK_Waapi_Call(command, LuaToAkJson(args or {}), LuaToAkJson(options or {}))
  local status = AkStatus(result)
  if not status then return false, result, AkError(result) end
  return true, result, ""
end

local function ParseReturnObjects(result)
  local out = {}
  local arr = AkMapGet(result, "return")
  local size = AkArraySize(arr)
  for i = 0, size - 1 do
    local item = AkArrayGet(arr, i)
    if item then
      out[#out + 1] = {
        id = AkFieldString(item, "id"),
        name = AkFieldString(item, "name"),
        type = AkFieldString(item, "type"),
        path = AkFieldString(item, "path"),
      }
    end
  end
  return out
end

local function RefreshWwiseInfo()
  local ok, result, err = WaapiCall("ak.wwise.core.getInfo", {}, {})
  if not ok then
    wwise_info = err
    return false
  end
  local display = AkFieldString(result, "displayName")
  if display == "" then display = "Connected" end
  wwise_info = display
  return true
end

local function QueryWaql(waql, returns)
  return WaapiCall("ak.wwise.core.object.get", { waql = waql }, { ["return"] = returns })
end

local function FetchWwiseStructure()
  if not ConnectWaapi(true) then return end
  RefreshWwiseInfo()

  local by_path = {}
  local wanted_types = {
    "WorkUnit",
    "Folder",
    "ActorMixer",
    "RandomSequenceContainer",
    "SwitchContainer",
    "BlendContainer",
  }

  for _, object_type in ipairs(wanted_types) do
    local waql = "\"\\Actor-Mixer Hierarchy\" select descendants where type = \"" .. object_type .. "\""
    local ok, result, err = QueryWaql(waql, { "id", "name", "type", "path" })
    if ok then
      for _, node in ipairs(ParseReturnObjects(result)) do
        if node.path ~= "" then by_path[node.path] = node end
      end
    else
      Log("Structure query failed for " .. object_type .. ": " .. err)
    end
  end

  wwise_nodes = {}
  for _, node in pairs(by_path) do
    wwise_nodes[#wwise_nodes + 1] = node
  end
  table.sort(wwise_nodes, function(a, b) return Lower(a.path) < Lower(b.path) end)

  status_text = string.format("Wwise structure refreshed: %d target nodes.", #wwise_nodes)
end

local function AddSwitchValueToGroup(group, child, expected_type)
  if not group or not child then return end
  if child.type == "" then child.type = expected_type end
  if child.type ~= "Switch" and child.type ~= "State" then return end
  if expected_type and expected_type ~= "" and child.type ~= expected_type then return end
  if child.path ~= "" and group.path ~= "" and not IsChildPath(child.path, group.path) then return end

  group.children = group.children or {}
  for _, existing in ipairs(group.children) do
    if (child.id ~= "" and existing.id == child.id)
      or (child.path ~= "" and existing.path == child.path)
      or (child.id == "" and child.path == "" and existing.name == child.name) then
      return
    end
  end
  group.children[#group.children + 1] = child
end

local function SortSwitchChildren(group)
  if not group.children then group.children = {} end
  table.sort(group.children, function(a, b)
    if Lower(a.name) == Lower(b.name) then return Lower(a.path) < Lower(b.path) end
    return Lower(a.name) < Lower(b.name)
  end)
end

local function FetchSwitchChildren(group)
  group.children = group.children or {}
  local expected_type = group.type == "StateGroup" and "State" or "Switch"
  local queries = {}
  if group.path and group.path ~= "" then
    queries[#queries + 1] = "\"" .. group.path .. "\" select children"
    queries[#queries + 1] = "\"" .. group.path .. "\" select descendants where type = \"" .. expected_type .. "\""
  end
  if group.id and group.id ~= "" then
    queries[#queries + 1] = "\"" .. group.id .. "\" select children"
  end

  for _, waql in ipairs(queries) do
    local ok, result, err = QueryWaql(waql, { "id", "name", "type", "path" })
    if ok then
      for _, child in ipairs(ParseReturnObjects(result)) do
        AddSwitchValueToGroup(group, child, expected_type)
      end
    else
      Log("Switch value query failed: " .. (group.name or group.path) .. " - " .. err)
    end
  end
  SortSwitchChildren(group)
end

local function AttachSwitchValuesByPath(groups_found, root, expected_type)
  local waql = "\"" .. root .. "\" select descendants where type = \"" .. expected_type .. "\""
  local ok, result, err = QueryWaql(waql, { "id", "name", "type", "path" })
  if not ok then
    Log("Switch value scan failed for " .. root .. ": " .. err)
    return
  end

  for _, child in ipairs(ParseReturnObjects(result)) do
    for _, group in ipairs(groups_found) do
      if IsChildPath(child.path, group.path) then
        AddSwitchValueToGroup(group, child, expected_type)
      end
    end
  end
end

local function FetchSwitchGroups()
  if not ConnectWaapi(true) then return end
  local found = {}
  local roots = {
    { root = "\\Switches", type = "SwitchGroup", label = "Switch" },
    { root = "\\States", type = "StateGroup", label = "State" },
  }
  for _, spec in ipairs(roots) do
    local waql = "\"" .. spec.root .. "\" select descendants where type = \"" .. spec.type .. "\""
    local ok, result, err = QueryWaql(waql, { "id", "name", "type", "path" })
    if ok then
      for _, group in ipairs(ParseReturnObjects(result)) do
        group.kind_label = spec.label
        group.children = {}
        group.value_type = spec.label == "State" and "State" or "Switch"
        found[#found + 1] = group
      end
    else
      Log("Switch group query failed for " .. spec.root .. ": " .. err)
    end
  end

  table.sort(found, function(a, b) return Lower(a.path) < Lower(b.path) end)
  AttachSwitchValuesByPath(found, "\\Switches", "Switch")
  AttachSwitchValuesByPath(found, "\\States", "State")
  for _, group in ipairs(found) do
    SortSwitchChildren(group)
  end
  wwise_switch_groups = found
  for _, group in ipairs(wwise_switch_groups) do
    if not group.children or #group.children == 0 then
      FetchSwitchChildren(group)
    end
  end
  for _, group in ipairs(groups) do
    EnsureSwitchAssignments(group)
  end
  status_text = string.format("Wwise switches refreshed: %d groups.", #wwise_switch_groups)
end

local function RefreshWwiseAll()
  FetchWwiseStructure()
  FetchSwitchGroups()
end

local function SetRandomContainerMode(container_path)
  local ok, _, err = WaapiCall("ak.wwise.core.object.setProperty", {
    object = container_path,
    property = "RandomOrSequence",
    value = 1,
  }, {})
  if not ok then Log("Warning: could not set random mode on " .. container_path .. ": " .. err) end
end

local function SetSwitchGroupReference(container_path, switch_group_id)
  local ok, _, err = WaapiCall("ak.wwise.core.object.setReference", {
    object = container_path,
    reference = "SwitchGroupOrStateGroup",
    value = switch_group_id,
  }, {})
  if ok then return true end

  local ok_alt, _, err_alt = WaapiCall("ak.wwise.core.object.setReference", {
    object = container_path,
    reference = "@SwitchGroupOrStateGroup",
    value = switch_group_id,
  }, {})
  if not ok_alt then
    Log("Switch reference failed for " .. container_path .. ": " .. err .. " / " .. err_alt)
  end
  return ok_alt
end

local function AddSwitchAssignment(child_path, switch_id)
  if not switch_id or switch_id == "" then return false end
  local ok, _, err = WaapiCall("ak.wwise.core.switchContainer.addAssignment", {
    child = child_path,
    stateOrSwitch = switch_id,
  }, {})
  if not ok then
    Log("Assignment failed: " .. child_path .. " -> " .. switch_id .. " : " .. err)
  end
  return ok
end

local function CreateNamedContainer(parent_path, name, container_type)
  local ok, _, err = WaapiCall("ak.wwise.core.object.create", {
    parent = parent_path,
    type = container_type,
    name = name,
    onNameConflict = "merge",
  }, {})
  if not ok then
    Log("Container create failed: " .. name .. " - " .. err)
    return false, parent_path .. "\\" .. name
  end
  local container_path = parent_path .. "\\" .. name
  if container_type == "RandomSequenceContainer" then
    SetRandomContainerMode(container_path)
  end
  return true, container_path
end

local function CreateContainer(parent_path, group)
  local container_type = group.container_type_idx == 1 and "SwitchContainer" or "RandomSequenceContainer"
  return CreateNamedContainer(parent_path, group.safe_name, container_type)
end

local function SwitchValueContainerName(group, switch_value)
  local value_name = switch_value and switch_value.name or "Unassigned"
  return SanitizeName(group.safe_name .. "_" .. value_name)
end

local function GroupUsesParentContainer(group)
  return group
    and group.container_type_idx == 1
    and group.use_parent_container
    and group.parent_name
    and group.parent_name ~= ""
end

local function ParentContainerName(group)
  if GroupUsesParentContainer(group) then return SanitizeName(group.parent_name) end
  return SanitizeName(group.safe_name)
end

local function ParentContainerPath(group)
  return settings.wwise_root_path .. "\\" .. ParentContainerName(group)
end

local function BucketNameForSwitchValue(group, switch_group, switch_value)
  if not switch_value then return SanitizeName(group.safe_name) end
  if GroupUsesParentContainer(group) then
    if group.value_container_name and group.value_container_name ~= "" then
      return SanitizeName(group.value_container_name)
    end
    return SanitizeName(ParentContainerName(group) .. (group.value_separator or "_") .. SwitchValueSuffixText(switch_group, switch_value))
  end
  return SwitchValueContainerName(group, switch_value)
end

local function SiblingBucketNameForSwitchValue(group, switch_group, switch_value)
  if GroupUsesParentContainer(group) then
    return SanitizeName(ParentContainerName(group) .. (group.value_separator or "_") .. SwitchValueSuffixText(switch_group, switch_value))
  end
  return SwitchValueContainerName(group, switch_value)
end

local function ValueBucketPath(group, switch_group, switch_value)
  if group.container_type_idx ~= 1 then return ParentContainerPath(group) end
  return ParentContainerPath(group) .. "\\" .. BucketNameForSwitchValue(group, switch_group, switch_value)
end

local function ParentKeyForGroup(group)
  return Lower(ParentContainerPath(group)) .. "|" .. tostring(group.switch_group_id or "")
end

local function PresentSwitchValuesForParent(group)
  local present = {}
  if not group or not group.switch_group_id or group.switch_group_id == "" then return present end
  local parent_name = ParentContainerName(group)
  for _, other in ipairs(groups) do
    if other.container_type_idx == 1
      and SelectedCountForGroup(other) > 0
      and other.switch_group_id == group.switch_group_id
      and ParentContainerName(other) == parent_name
      and other.switch_value_id
      and other.switch_value_id ~= "" then
      present[other.switch_value_id] = true
    end
  end
  return present
end

local function MissingSwitchValuesForParent(group)
  local missing = {}
  if not GroupUsesParentContainer(group) then return missing end
  local switch_group = SwitchGroupById(group.switch_group_id)
  if not switch_group or not switch_group.children then return missing end
  local present = PresentSwitchValuesForParent(group)
  for _, switch_value in ipairs(switch_group.children) do
    if switch_value.id ~= "" and not present[switch_value.id] then
      missing[#missing + 1] = switch_value
    end
  end
  return missing
end

local function MissingSwitchValueText(group)
  local missing = MissingSwitchValuesForParent(group)
  if #missing == 0 then return "complete" end
  local names = {}
  for i, switch_value in ipairs(missing) do
    if i > 3 then
      names[#names + 1] = "..."
      break
    end
    names[#names + 1] = SwitchValueSuffixText(SwitchGroupById(group.switch_group_id), switch_value)
  end
  return "missing: " .. table.concat(names, ", ")
end

local function AutoInferParentsFromNames()
  if #wwise_switch_groups == 0 then
    status_text = "Load Wwise Switch/State Groups before name inference."
    return
  end

  local applied = 0
  local skipped = 0
  for _, group in ipairs(groups) do
    if SelectedCountForGroup(group) > 0 then
      local ok = InferGroupFromName(group)
      if ok then
        applied = applied + 1
      else
        skipped = skipped + 1
      end
    end
  end
  status_text = string.format("Name inference applied to %d groups. Skipped %d.", applied, skipped)
end

local function PromptMissingSiblingBuckets()
  local pending = {}
  local seen = {}
  for _, group in ipairs(groups) do
    if group.container_type_idx == 1
      and GroupUsesParentContainer(group)
      and SelectedCountForGroup(group) > 0
      and not group.create_missing_value_buckets then
      local switch_group = SwitchGroupById(group.switch_group_id)
      local switch_value = SwitchValueById(switch_group, group.switch_value_id)
      local missing = MissingSwitchValuesForParent(group)
      local key = ParentKeyForGroup(group)
      if switch_group and switch_value and #missing > 0 and not seen[key] then
        seen[key] = true
        local names = {}
        for _, missing_value in ipairs(missing) do
          names[#names + 1] = SiblingBucketNameForSwitchValue(group, switch_group, missing_value)
        end
        pending[#pending + 1] = {
          key = key,
          parent_name = ParentContainerName(group),
          switch_group = switch_group.path,
          missing_names = table.concat(names, ", "),
        }
      end
    end
  end

  if #pending == 0 then return true end

  local lines = {
    "Some inferred parent Switch Containers are missing sibling value buckets.",
    "",
  }
  for i, item in ipairs(pending) do
    if i > 8 then
      lines[#lines + 1] = "..."
      break
    end
    lines[#lines + 1] = item.parent_name .. " -> " .. item.missing_names
  end
  lines[#lines + 1] = ""
  lines[#lines + 1] = "Create these empty sibling buckets and bind them to their Switch/State values?"

  local result = r.ShowMessageBox(table.concat(lines, "\n"), SCRIPT_TITLE, 3)
  if result == 2 then return false end
  if result == 6 then
    local allowed = {}
    for _, item in ipairs(pending) do
      allowed[item.key] = true
    end
    for _, group in ipairs(groups) do
      if allowed[ParentKeyForGroup(group)] then
        group.create_missing_value_buckets = true
      end
    end
  end
  return true
end

local function ValidateImportPlan()
  if not settings.import_to_wwise then return true end
  if settings.wwise_root_path == "" then
    r.ShowMessageBox("Wwise import target path is empty.", SCRIPT_TITLE, 0)
    return false
  end

  local invalid = {}
  for _, group in ipairs(groups) do
    if SelectedCountForGroup(group) > 0 and group.container_type_idx == 1 then
      local switch_group = SwitchGroupById(group.switch_group_id)
      local switch_value = SwitchValueById(switch_group, group.switch_value_id)
      if not switch_group then
        invalid[#invalid + 1] = group.safe_name .. " : missing Switch/State Group"
      elseif not switch_value then
        invalid[#invalid + 1] = group.safe_name .. " : missing Switch/State Value"
      elseif GroupUsesParentContainer(group) and ParentContainerName(group) == "" then
        invalid[#invalid + 1] = group.safe_name .. " : missing parent container name"
      end
    end
  end

  if #invalid == 0 then return true end
  local lines = {
    "Some selected Switch Containers are not ready for Wwise import.",
    "",
  }
  for i, text in ipairs(invalid) do
    if i > 12 then
      lines[#lines + 1] = "..."
      break
    end
    lines[#lines + 1] = text
  end
  lines[#lines + 1] = ""
  lines[#lines + 1] = "Continue and skip these groups?"
  local result = r.ShowMessageBox(table.concat(lines, "\n"), SCRIPT_TITLE, 4)
  return result == 6
end

-- Returns false ONLY when a WAV's data chunk is provably all-zero (silent render).
-- Any parse uncertainty (non-WAV, odd header, read error) returns true, so a real
-- file is never wrongly blocked; this only catches the "full-length but silent" case.
local function RenderedFileHasAudio(path)
  local f = io.open(path, "rb")
  if not f then return true end
  local header = f:read(12)
  if not header or #header < 12 or header:sub(1, 4) ~= "RIFF" or header:sub(9, 12) ~= "WAVE" then
    f:close()
    return true
  end
  while true do
    local chunk = f:read(8)
    if not chunk or #chunk < 8 then break end
    local id = chunk:sub(1, 4)
    local size = string.unpack("<I4", chunk, 5)
    if id == "data" then
      local remaining = size
      while remaining > 0 do
        local block = f:read(math.min(remaining, 65536))
        if not block or #block == 0 then break end
        if block:find("[^\0]") then
          f:close()
          return true
        end
        remaining = remaining - #block
      end
      f:close()
      return false
    end
    f:seek("cur", size + (size % 2))
  end
  f:close()
  return true
end

local function RenderOneRegion(region, child_name)
  local proj = Project()
  local ext = settings.render_ext:gsub("^%.", "")
  local expected_file = PathJoin(settings.render_path, child_name .. "." .. ext)

  r.GetSet_LoopTimeRange(true, false, region.pos, region.rgnend, false)
  -- Force render source = Master mix and bounds = Time selection. Without this the
  -- script inherits "most recent render settings"; if that source was Stems/selected
  -- items/region matrix, the master is silent over the time selection and we get a
  -- full-length SILENT wav that still passes FileExists (the empty-shell bug).
  r.GetSetProjectInfo(proj, "RENDER_SETTINGS", 0, true)
  r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 2, true)
  r.GetSetProjectInfo_String(proj, "RENDER_FILE", ToReaperPath(settings.render_path), true)
  r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", child_name, true)
  r.Main_OnCommand(ACTION_RENDER_RECENT, 0)

  if FileExists(expected_file) then
    return true, expected_file
  end
  return false, expected_file
end

local function SnapshotRenderState()
  local proj = Project()
  local loop_start, loop_end = r.GetSet_LoopTimeRange(false, false, 0, 0, false)
  local _, render_file = r.GetSetProjectInfo_String(proj, "RENDER_FILE", "", false)
  local _, render_pattern = r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", "", false)
  local bounds = r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 0, false)
  local render_settings = r.GetSetProjectInfo(proj, "RENDER_SETTINGS", 0, false)
  return {
    loop_start = loop_start,
    loop_end = loop_end,
    render_file = render_file,
    render_pattern = render_pattern,
    bounds = bounds,
    render_settings = render_settings,
  }
end

local function RestoreRenderState(state)
  if not state then return end
  local proj = Project()
  r.GetSet_LoopTimeRange(true, false, state.loop_start, state.loop_end, false)
  r.GetSetProjectInfo_String(proj, "RENDER_FILE", state.render_file or "", true)
  r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", state.render_pattern or "", true)
  if state.bounds then r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", state.bounds, true) end
  if state.render_settings ~= nil then r.GetSetProjectInfo(proj, "RENDER_SETTINGS", state.render_settings, true) end
end

-- RENDER_BOUNDSFLAG values: 0=custom time, 1=entire project, 2=time selection,
-- 3=project regions, 4=selected media items, 5=selected regions, 6=razor edit areas.
-- If your REAPER's Render dialog shows the wrong "Bounds" after running, change this.
local RENDER_BOUNDS_SELECTED_REGIONS = 5

-- Leave REAPER's Render dialog defaulted to the region-based workflow the user wants:
-- Source = Master mix, Bounds = Selected regions, File name = region name ($region).
-- This does NOT change the script's own per-region export (which keeps the RegionName_NN
-- naming for Wwise import); it only sets the native render defaults the user sees.
local function ApplyPreferredRenderDefaults()
  local proj = Project()
  r.GetSetProjectInfo(proj, "RENDER_SETTINGS", 0, true)
  r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", RENDER_BOUNDS_SELECTED_REGIONS, true)
  r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", "$region", true)
  if settings.render_path and settings.render_path ~= "" then
    r.GetSetProjectInfo_String(proj, "RENDER_FILE", ToReaperPath(settings.render_path), true)
  end
end

local function WriteLastLog()
  local root = r.GetResourcePath()
  local path = ToReaperPath(PathJoin(root, "Scripts/Codex_WwiseBridge_last_log.txt"))
  local f = io.open(path, "w")
  if not f then return end
  f:write(table.concat(process_log, "\n"))
  f:write("\n")
  f:close()
end

local function ExecuteProcess()
  SaveSettings()
  process_log = {}
  if r.ClearConsole then r.ClearConsole() end
  Log("=== Wwise Bridge Region Export started ===")

  if selected_region_count == 0 then
    r.ShowMessageBox("No regions selected.", SCRIPT_TITLE, 0)
    return
  end
  if settings.render_path == "" then settings.render_path = DefaultRenderPath() end
  settings.render_path = ToReaperPath(settings.render_path)
  settings.wwise_root_path = ToWwisePath(settings.wwise_root_path)

  if settings.import_to_wwise and not ConnectWaapi(true) then return end
  if settings.import_to_wwise then
    RefreshWwiseInfo()
    FetchSwitchGroups()
  end
  if not ValidateImportPlan() then return end
  if settings.import_to_wwise and not PromptMissingSiblingBuckets() then return end

  r.RecursiveCreateDirectory(settings.render_path, 0)

  local render_state = SnapshotRenderState()
  active_render_state = render_state
  local proj = Project()
  r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 2, true)

  local rendered_files = {}
  local render_failures = 0

  r.PreventUIRefresh(1)
  ui_refresh_locked = true
  r.Undo_BeginBlock()
  undo_open = true

  if settings.import_to_wwise then
    WaapiCall("ak.wwise.core.undo.beginGroup", {}, {})
  end

  local assigned_value_buckets = {}
  local function EnsureAssignedValueBucket(parent_path, bucket_name, switch_value_id)
    local ok_bucket, bucket_path = CreateNamedContainer(parent_path, bucket_name, "RandomSequenceContainer")
    if ok_bucket then
      local key = Lower(bucket_path) .. "|" .. tostring(switch_value_id or "")
      if not assigned_value_buckets[key] then
        AddSwitchAssignment(bucket_path, switch_value_id)
        assigned_value_buckets[key] = true
      end
    end
    return ok_bucket, bucket_path
  end

  for _, group in ipairs(groups) do
    local selected = SelectedRegionsForGroup(group)
    if #selected > 0 then
      EnsureSwitchAssignments(group)
      Log("")
      Log("Group: " .. group.safe_name .. " (" .. tostring(#selected) .. " regions)")

      local switch_group = SwitchGroupById(group.switch_group_id)
      local switch_value = SwitchValueById(switch_group, group.switch_value_id)
      local container_path = group.container_type_idx == 1 and ParentContainerPath(group) or (settings.wwise_root_path .. "\\" .. group.safe_name)
      local import_container_path = group.container_type_idx == 1 and ValueBucketPath(group, switch_group, switch_value) or container_path
      local import_items = {}

      for i, region_index in ipairs(selected) do
        local region = regions[region_index]
        local child_name = ChildNameForSelectedIndex(group, i)
        local file_path = PathJoin(settings.render_path, child_name .. "." .. settings.render_ext:gsub("^%.", ""))
        local render_ok = true
        if settings.render_regions then
          local ok, expected = RenderOneRegion(region, child_name)
          file_path = expected
          if not ok then
            render_ok = false
            render_failures = render_failures + 1
            Log("Render missing: " .. file_path)
          elseif not RenderedFileHasAudio(expected) then
            render_ok = false
            render_failures = render_failures + 1
            Log("Render produced SILENCE, skipped import (check render source = Master mix, and track mute/solo): " .. file_path)
          else
            Log("Rendered: " .. file_path)
          end
        else
          Log("Using existing file: " .. file_path)
        end
        rendered_files[region_index] = file_path
        if render_ok then
          import_items[#import_items + 1] = {
            audioFile = ToWwisePath(file_path),
            objectPath = import_container_path .. "\\<Sound SFX>" .. child_name,
            originalsSubFolder = settings.originals_subfolder,
          }
        end
      end

      if settings.import_to_wwise then
        local can_import = true
        if group.container_type_idx == 1 then
          if not switch_group then
            can_import = false
            Log("Skipped Wwise import because Switch/State Group is missing: " .. group.safe_name)
          elseif not switch_value then
            can_import = false
            Log("Skipped Wwise import because Switch/State Value is missing: " .. group.safe_name)
          end
        end
        for _, item in ipairs(import_items) do
          if not FileExists(ToReaperPath(item.audioFile)) then
            can_import = false
            Log("Skipped Wwise import because file is missing: " .. item.audioFile)
          end
        end

        if can_import then
          local container_ready = false
          if group.container_type_idx == 1 then
            local ok_parent = CreateNamedContainer(settings.wwise_root_path, ParentContainerName(group), "SwitchContainer")
            if ok_parent then
              if SetSwitchGroupReference(container_path, group.switch_group_id) then
                local bucket_name = BucketNameForSwitchValue(group, switch_group, switch_value)
                local ok_value_container = EnsureAssignedValueBucket(container_path, bucket_name, group.switch_value_id)
                container_ready = ok_value_container
                if container_ready and group.create_missing_value_buckets and GroupUsesParentContainer(group) then
                  for _, missing_value in ipairs(MissingSwitchValuesForParent(group)) do
                    local missing_name = SiblingBucketNameForSwitchValue(group, switch_group, missing_value)
                    EnsureAssignedValueBucket(container_path, missing_name, missing_value.id)
                  end
                end
              else
                Log("Skipped Wwise import because Switch/State Group reference failed: " .. group.safe_name)
              end
            end
          elseif CreateContainer(settings.wwise_root_path, group) then
            container_ready = true
          end

          if container_ready then
            local import_payload = {
              importOperation = import_operations[settings.import_operation_idx + 1] or "useExisting",
              default = { importLanguage = "SFX" },
              imports = import_items,
              autoAddToSourceControl = false,
            }
            local ok, _, err = WaapiCall("ak.wwise.core.audio.import", import_payload, {})
            if ok then
              Log("Imported to Wwise: " .. container_path)
              if group.container_type_idx == 1 then
                Log("Switch value container: " .. import_container_path .. " -> " .. switch_value.name)
              end
            else
              Log("Import failed: " .. group.safe_name .. " - " .. err)
            end
          else
            Log("Skipped Wwise import because container setup failed: " .. group.safe_name)
          end
        end
      end
    end
  end

  if settings.import_to_wwise then
    WaapiCall("ak.wwise.core.undo.endGroup", { displayName = "REAPER Region Container Export" }, {})
    if settings.auto_save_wwise then
      local ok, _, err = WaapiCall("ak.wwise.core.project.save", {}, {})
      if ok then Log("Wwise project saved.") else Log("Wwise save failed: " .. err) end
    else
      Log("Wwise project not saved by script.")
    end
  end

  RestoreRenderState(render_state)
  ApplyPreferredRenderDefaults()
  active_render_state = nil
  r.Undo_EndBlock("Wwise Bridge - Region Container Export", -1)
  undo_open = false
  r.PreventUIRefresh(-1)
  ui_refresh_locked = false
  r.UpdateArrange()

  if r.AK_AkJson_ClearAll then r.AK_AkJson_ClearAll() end
  WriteLastLog()

  local summary = string.format(
    "Done.\nSelected regions: %d\nRender warnings: %d\n\nCheck REAPER console for details.",
    selected_region_count,
    render_failures
  )
  status_text = summary:gsub("\n", " ")
  r.ShowMessageBox(summary, SCRIPT_TITLE, 0)
end

local function EmergencyRestore(err)
  if active_render_state then
    RestoreRenderState(active_render_state)
    active_render_state = nil
  end
  if undo_open then
    r.Undo_EndBlock("Wwise Bridge - Region Container Export failed", -1)
    undo_open = false
  end
  if ui_refresh_locked then
    r.PreventUIRefresh(-1)
    ui_refresh_locked = false
  end
  r.UpdateArrange()
  Log("ERROR: " .. tostring(err))
  WriteLastLog()
  r.ShowMessageBox("Script stopped with an error. Render/time-selection state was restored where possible.\n\n" .. tostring(err), SCRIPT_TITLE, 0)
end

local function DrawConnectionPanel()
  if r.ImGui_CollapsingHeader(ctx, "Wwise Connection and Target", r.ImGui_TreeNodeFlags_DefaultOpen()) then
    local color = wwise_connected and 0x28C76FFF or 0xFF5C5CFF
    r.ImGui_TextColored(ctx, color, wwise_connected and "Connected" or "Disconnected")
    r.ImGui_SameLine(ctx)
    r.ImGui_Text(ctx, wwise_info)

    if r.ImGui_Button(ctx, "Connect / Refresh Wwise") then
      RefreshWwiseAll()
    end
    r.ImGui_SameLine(ctx)
    if not wwise_connected then r.ImGui_BeginDisabled(ctx) end
    if r.ImGui_Button(ctx, "Disconnect") then
      DisconnectWaapi()
      wwise_info = "Disconnected"
    end
    if not wwise_connected then r.ImGui_EndDisabled(ctx) end

    r.ImGui_Text(ctx, "Import destination")
    r.ImGui_SetNextItemWidth(ctx, -1)
    local changed, new_path = r.ImGui_InputText(ctx, "##wwise_root_path", settings.wwise_root_path)
    if changed then settings.wwise_root_path = ToWwisePath(new_path) end

    r.ImGui_Text(ctx, "Structure filter")
    r.ImGui_SetNextItemWidth(ctx, -1)
    _, wwise_filter = r.ImGui_InputText(ctx, "##wwise_filter", wwise_filter)

    if #wwise_nodes == 0 then
      r.ImGui_Text(ctx, "No Wwise structure loaded yet.")
    else
      local shown = 0
      if r.ImGui_BeginTable(ctx, "WwiseNodes", 3, r.ImGui_TableFlags_Borders() | r.ImGui_TableFlags_RowBg() | r.ImGui_TableFlags_Resizable()) then
        r.ImGui_TableSetupColumn(ctx, "Target", r.ImGui_TableColumnFlags_WidthStretch())
        r.ImGui_TableSetupColumn(ctx, "Type", r.ImGui_TableColumnFlags_WidthFixed(), 150)
        r.ImGui_TableSetupColumn(ctx, "Path", r.ImGui_TableColumnFlags_WidthStretch())
        r.ImGui_TableHeadersRow(ctx)

        for _, node in ipairs(wwise_nodes) do
          if shown < 120 and MatchesFilter(node.path .. " " .. node.type, wwise_filter) then
            shown = shown + 1
            local depth = select(2, node.path:gsub("\\", ""))
            local indent = string.rep("  ", math.max(0, depth - 2))
            r.ImGui_TableNextRow(ctx)
            r.ImGui_TableSetColumnIndex(ctx, 0)
            local selected = node.path == settings.wwise_root_path
            if r.ImGui_Selectable(ctx, indent .. node.name .. "##" .. node.id, selected) then
              settings.wwise_root_path = node.path
            end
            r.ImGui_TableSetColumnIndex(ctx, 1)
            r.ImGui_Text(ctx, node.type)
            r.ImGui_TableSetColumnIndex(ctx, 2)
            r.ImGui_Text(ctx, node.path)
          end
        end
        r.ImGui_EndTable(ctx)
      end
      r.ImGui_Text(ctx, string.format("Showing %d of %d loaded target nodes.", shown, #wwise_nodes))
    end
  end
end

local function DrawRenderPanel()
  if r.ImGui_CollapsingHeader(ctx, "Render and Import Options", r.ImGui_TreeNodeFlags_DefaultOpen()) then
    local changed
    r.ImGui_Text(ctx, "Output mode")
    r.ImGui_SetNextItemWidth(ctx, 280)
    local output_mode = OutputModeIndex()
    local changed_mode, new_mode = r.ImGui_Combo(ctx, "##output_mode", output_mode, output_mode_combo)
    if changed_mode then ApplyOutputModeIndex(new_mode) end

    if not settings.import_to_wwise then r.ImGui_BeginDisabled(ctx) end
    changed, settings.auto_save_wwise = r.ImGui_Checkbox(ctx, "Save Wwise project after import", settings.auto_save_wwise)
    if not settings.import_to_wwise then r.ImGui_EndDisabled(ctx) end

    r.ImGui_Text(ctx, "Render folder")
    r.ImGui_SetNextItemWidth(ctx, -1)
    changed, settings.render_path = r.ImGui_InputText(ctx, "##render_path", settings.render_path)
    if changed then settings.render_path = ToReaperPath(settings.render_path) end

    r.ImGui_Text(ctx, "Rendered file extension")
    r.ImGui_SetNextItemWidth(ctx, 100)
    changed, settings.render_ext = r.ImGui_InputText(ctx, "##render_ext", settings.render_ext)
    if changed then settings.render_ext = settings.render_ext:gsub("^%.", "") end

    if not settings.import_to_wwise then r.ImGui_BeginDisabled(ctx) end
    r.ImGui_Text(ctx, "Originals subfolder")
    r.ImGui_SetNextItemWidth(ctx, -1)
    _, settings.originals_subfolder = r.ImGui_InputText(ctx, "##originals_subfolder", settings.originals_subfolder)

    r.ImGui_Text(ctx, "Import operation")
    r.ImGui_SetNextItemWidth(ctx, 180)
    changed, settings.import_operation_idx = r.ImGui_Combo(ctx, "##import_operation", settings.import_operation_idx, import_operation_combo)
    if not settings.import_to_wwise then r.ImGui_EndDisabled(ctx) end
  end
end

local function DrawRegionPanel()
  if r.ImGui_CollapsingHeader(ctx, "REAPER Regions", r.ImGui_TreeNodeFlags_DefaultOpen()) then
    r.ImGui_Text(ctx, string.format("Project regions: %d | Selected: %d | Container groups: %d", region_count_total, selected_region_count, #groups))

    if r.ImGui_Button(ctx, "Rescan regions") then ScanRegions() end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "All") then SelectAllRegions(true, false) end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "None") then SelectAllRegions(false, false) end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "Visible all") then SelectAllRegions(true, true) end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "Visible none") then SelectAllRegions(false, true) end

    if r.ImGui_Button(ctx, "Use time selection") then SelectRegionsInTimeSelection() end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "Use selected items") then SelectRegionsOverSelectedItems() end

    r.ImGui_TextDisabled(ctx, "Row click = single | Alt/Ctrl+click = toggle | Shift+click = range")

    r.ImGui_Text(ctx, "Region filter")
    r.ImGui_SetNextItemWidth(ctx, -1)
    _, scan_filter = r.ImGui_InputText(ctx, "##region_filter", scan_filter)

    if r.ImGui_BeginTable(ctx, "Regions", 6, r.ImGui_TableFlags_Borders() | r.ImGui_TableFlags_RowBg() | r.ImGui_TableFlags_Resizable()) then
      r.ImGui_TableSetupColumn(ctx, "Use", r.ImGui_TableColumnFlags_WidthFixed(), 45)
      r.ImGui_TableSetupColumn(ctx, "#", r.ImGui_TableColumnFlags_WidthFixed(), 45)
      r.ImGui_TableSetupColumn(ctx, "Region", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Time", r.ImGui_TableColumnFlags_WidthFixed(), 140)
      r.ImGui_TableSetupColumn(ctx, "Container", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Child preview", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableHeadersRow(ctx)

      -- Build the visible (filtered, capped) index list first so Shift-range select
      -- operates over exactly the rows the user currently sees.
      local visible = {}
      for i, region in ipairs(regions) do
        if #visible < 200 and MatchesFilter(region.raw_name .. " " .. region.safe_name, scan_filter) then
          visible[#visible + 1] = i
        end
      end

      local function PositionInVisible(target)
        for pos, idx in ipairs(visible) do
          if idx == target then return pos end
        end
        return nil
      end

      local function ApplyRegionClick(i)
        local mods = r.ImGui_GetKeyMods(ctx)
        local shift = (mods & r.ImGui_Mod_Shift()) ~= 0
        local add = (mods & (r.ImGui_Mod_Alt() | r.ImGui_Mod_Ctrl())) ~= 0
        if shift and last_clicked_region then
          local a = PositionInVisible(last_clicked_region)
          local b = PositionInVisible(i)
          if a and b then
            if a > b then a, b = b, a end
            for k = a, b do regions[visible[k]].selected = true end
          else
            regions[i].selected = true
          end
        elseif add then
          regions[i].selected = not regions[i].selected
          last_clicked_region = i
        else
          for _, rg in ipairs(regions) do rg.selected = false end
          regions[i].selected = true
          last_clicked_region = i
        end
        RebuildGroups(CaptureGroupConfig())
      end

      for _, i in ipairs(visible) do
        local region = regions[i]
        local group = groups_by_name[region.safe_name]
        r.ImGui_TableNextRow(ctx)
        r.ImGui_TableSetColumnIndex(ctx, 0)
        r.ImGui_PushID(ctx, "region" .. tostring(i))
        local changed, selected = r.ImGui_Checkbox(ctx, "##use", region.selected)
        if changed then
          region.selected = selected
          last_clicked_region = i
          RebuildGroups(CaptureGroupConfig())
        end
        r.ImGui_PopID(ctx)

        r.ImGui_TableSetColumnIndex(ctx, 1)
        r.ImGui_Text(ctx, tostring(i))
        r.ImGui_TableSetColumnIndex(ctx, 2)
        if r.ImGui_Selectable(ctx, region.raw_name .. "##sel" .. tostring(i), region.selected) then
          ApplyRegionClick(i)
        end
        r.ImGui_TableSetColumnIndex(ctx, 3)
        r.ImGui_Text(ctx, FormatTime(region.pos) .. "-" .. FormatTime(region.rgnend))
        r.ImGui_TableSetColumnIndex(ctx, 4)
        r.ImGui_Text(ctx, region.safe_name)
        r.ImGui_TableSetColumnIndex(ctx, 5)
        if group and region.selected then
          r.ImGui_Text(ctx, ChildNameForRegion(group, i))
        else
          r.ImGui_Text(ctx, "-")
        end
      end
      r.ImGui_EndTable(ctx)
    end
  end
end

local function SwitchGroupComboLabel(group)
  local labels = { "(none)" }
  for _, sg in ipairs(wwise_switch_groups) do
    local value_count = sg.children and #sg.children or 0
    labels[#labels + 1] = "[" .. (sg.kind_label or "Switch") .. ":" .. tostring(value_count) .. "] " .. sg.path
  end
  return table.concat(labels, "\0") .. "\0"
end

local function SwitchValueComboLabel(switch_group)
  local labels = { "(none)" }
  if switch_group and switch_group.children then
    for _, child in ipairs(switch_group.children) do
      labels[#labels + 1] = child.name
    end
  end
  return table.concat(labels, "\0") .. "\0"
end

local function SwitchGroupComboIndex(group)
  if not group.switch_group_id or group.switch_group_id == "" then return 0 end
  for i, sg in ipairs(wwise_switch_groups) do
    if sg.id == group.switch_group_id then return i end
  end
  return 0
end

local function SwitchValueComboIndex(switch_group, switch_id)
  if not switch_group or not switch_group.children or not switch_id then return 0 end
  for i, child in ipairs(switch_group.children) do
    if child.id == switch_id then return i end
  end
  return 0
end

local function DrawGroupPanel()
  if r.ImGui_CollapsingHeader(ctx, "Wwise Containers", r.ImGui_TreeNodeFlags_DefaultOpen()) then
    if r.ImGui_Button(ctx, "Infer parents from names") then
      AutoInferParentsFromNames()
    end
    r.ImGui_SameLine(ctx)
    if r.ImGui_Button(ctx, "Clear parent grouping") then
      for _, group in ipairs(groups) do
        group.use_parent_container = false
        group.parent_name = ""
        group.value_container_name = ""
        group.create_missing_value_buckets = false
        group.auto_parent_detected = false
      end
      status_text = "Cleared parent grouping."
    end

    if r.ImGui_BeginTable(ctx, "Groups", 8, r.ImGui_TableFlags_Borders() | r.ImGui_TableFlags_RowBg() | r.ImGui_TableFlags_Resizable()) then
      r.ImGui_TableSetupColumn(ctx, "Source", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Selected", r.ImGui_TableColumnFlags_WidthFixed(), 70)
      r.ImGui_TableSetupColumn(ctx, "Type", r.ImGui_TableColumnFlags_WidthFixed(), 170)
      r.ImGui_TableSetupColumn(ctx, "Parent", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Switch/State Group", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Switch/State Value", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableSetupColumn(ctx, "Siblings", r.ImGui_TableColumnFlags_WidthFixed(), 190)
      r.ImGui_TableSetupColumn(ctx, "Target Path", r.ImGui_TableColumnFlags_WidthStretch())
      r.ImGui_TableHeadersRow(ctx)

      for _, group in ipairs(groups) do
        local selected_count = SelectedCountForGroup(group)
        r.ImGui_TableNextRow(ctx)
        r.ImGui_TableSetColumnIndex(ctx, 0)
        r.ImGui_Text(ctx, group.safe_name)
        r.ImGui_TableSetColumnIndex(ctx, 1)
        r.ImGui_Text(ctx, tostring(selected_count))
        r.ImGui_TableSetColumnIndex(ctx, 2)
        r.ImGui_PushID(ctx, "gtype" .. group.safe_name)
        r.ImGui_SetNextItemWidth(ctx, -1)
        local changed, new_type = r.ImGui_Combo(ctx, "##type", group.container_type_idx, container_type_combo)
        if changed then
          group.container_type_idx = new_type
          if group.container_type_idx ~= 1 then
            group.use_parent_container = false
            group.create_missing_value_buckets = false
          end
          EnsureSwitchAssignments(group)
        end
        r.ImGui_PopID(ctx)

        r.ImGui_TableSetColumnIndex(ctx, 3)
        if group.container_type_idx ~= 1 then
          r.ImGui_Text(ctx, "-")
        else
          r.ImGui_PushID(ctx, "parent" .. group.safe_name)
          local changed_parent_enabled, use_parent = r.ImGui_Checkbox(ctx, "##useparent", group.use_parent_container)
          if changed_parent_enabled then
            group.use_parent_container = use_parent
            if group.use_parent_container then
              local switch_group = SwitchGroupById(group.switch_group_id)
              if not ApplyParentInference(group, switch_group) and (not group.parent_name or group.parent_name == "") then
                group.parent_name = group.safe_name
                group.value_container_name = group.safe_name
              end
            else
              group.create_missing_value_buckets = false
            end
          end
          if group.use_parent_container then
            r.ImGui_SameLine(ctx)
            r.ImGui_SetNextItemWidth(ctx, -1)
            local changed_parent_name, parent_name = r.ImGui_InputText(ctx, "##parentname", group.parent_name or "")
            if changed_parent_name then group.parent_name = SanitizeName(parent_name) end
          end
          r.ImGui_PopID(ctx)
        end

        r.ImGui_TableSetColumnIndex(ctx, 4)
        if group.container_type_idx ~= 1 then
          r.ImGui_Text(ctx, "-")
        else
          r.ImGui_PushID(ctx, "sgroup" .. group.safe_name)
          r.ImGui_SetNextItemWidth(ctx, -1)
          local current = SwitchGroupComboIndex(group)
          local changed_sg, new_sg = r.ImGui_Combo(ctx, "##switchgroup", current, SwitchGroupComboLabel(group))
          if changed_sg then
            if new_sg == 0 then
              group.switch_group_id = ""
            else
              group.switch_group_id = wwise_switch_groups[new_sg] and wwise_switch_groups[new_sg].id or ""
            end
            group.switch_value_id = ""
            ApplyParentInference(group, SwitchGroupById(group.switch_group_id))
            EnsureSwitchAssignments(group)
          end
          r.ImGui_PopID(ctx)
        end

        r.ImGui_TableSetColumnIndex(ctx, 5)
        if group.container_type_idx ~= 1 then
          r.ImGui_Text(ctx, "-")
        else
          local switch_group = SwitchGroupById(group.switch_group_id)
          r.ImGui_PushID(ctx, "svalue" .. group.safe_name)
          r.ImGui_SetNextItemWidth(ctx, -1)
          local current_value = SwitchValueComboIndex(switch_group, group.switch_value_id)
          local changed_value, new_value = r.ImGui_Combo(ctx, "##switchvalue", current_value, SwitchValueComboLabel(switch_group))
          if changed_value then
            if new_value == 0 then
              group.switch_value_id = ""
            else
              local child = switch_group and switch_group.children and switch_group.children[new_value]
              group.switch_value_id = child and child.id or ""
              if group.use_parent_container and (not group.value_container_name or group.value_container_name == "") then
                group.value_container_name = BucketNameForSwitchValue(group, switch_group, child)
              end
            end
          end
          r.ImGui_PopID(ctx)
        end

        r.ImGui_TableSetColumnIndex(ctx, 6)
        local sibling_switch_group = SwitchGroupById(group.switch_group_id)
        local sibling_switch_value = SwitchValueById(sibling_switch_group, group.switch_value_id)
        if group.container_type_idx ~= 1 or not GroupUsesParentContainer(group) or not sibling_switch_group or not sibling_switch_value then
          r.ImGui_Text(ctx, "-")
        else
          local missing = MissingSwitchValuesForParent(group)
          r.ImGui_PushID(ctx, "siblings" .. group.safe_name)
          if #missing == 0 then r.ImGui_BeginDisabled(ctx) end
          local changed_missing, create_missing = r.ImGui_Checkbox(ctx, "All values", group.create_missing_value_buckets)
          if changed_missing then group.create_missing_value_buckets = create_missing end
          if #missing == 0 then r.ImGui_EndDisabled(ctx) end
          r.ImGui_SameLine(ctx)
          r.ImGui_Text(ctx, MissingSwitchValueText(group))
          r.ImGui_PopID(ctx)
        end

        r.ImGui_TableSetColumnIndex(ctx, 7)
        local switch_group = SwitchGroupById(group.switch_group_id)
        local switch_value = SwitchValueById(switch_group, group.switch_value_id)
        if group.container_type_idx == 1 then
          r.ImGui_Text(ctx, ValueBucketPath(group, switch_group, switch_value))
        else
          r.ImGui_Text(ctx, settings.wwise_root_path .. "\\" .. group.safe_name)
        end
      end
      r.ImGui_EndTable(ctx)
    end
  end
end

local function DrawSwitchAssignmentsPanel()
  return
end

local function DrawBottomBar()
  r.ImGui_Separator(ctx)
  r.ImGui_TextColored(ctx, 0xFFD166FF, status_text)
  local can_run = selected_region_count > 0
  if not can_run then r.ImGui_BeginDisabled(ctx) end
  if r.ImGui_Button(ctx, "START EXPORT / IMPORT", -1, 42) then
    local ok, err = xpcall(ExecuteProcess, debug.traceback)
    if not ok then EmergencyRestore(err) end
  end
  if not can_run then r.ImGui_EndDisabled(ctx) end
end

local function Loop()
  local visible, open = r.ImGui_Begin(ctx, SCRIPT_TITLE, true)
  if visible then
    DrawConnectionPanel()
    DrawRenderPanel()
    DrawRegionPanel()
    DrawGroupPanel()
    DrawSwitchAssignmentsPanel()
    DrawBottomBar()
    r.ImGui_End(ctx)
  end

  if open then
    r.defer(Loop)
  else
    SaveSettings()
    DisconnectWaapi()
  end
end

local function Main()
  LoadSettings()
  if not CheckDependencies(true) then return end
  ctx = r.ImGui_CreateContext(SCRIPT_TITLE)
  r.atexit(function()
    SaveSettings()
    DisconnectWaapi()
  end)
  ScanRegions()
  ApplyPreferredRenderDefaults()
  r.defer(Loop)
end

Main()
