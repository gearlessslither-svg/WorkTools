-- @description Reaper to Wwise Bridge (V7.0 Normalization Edition)
-- @version 7.0
-- @author Game Audio Expert
-- @about 
--   新增 Normalization (标准化) 功能。
--   支持在导出前自动将音频标准化到指定 Peak dB (Common Gain 模式)。
--   渲染后自动复原音量，不破坏工程混音。
--   包含 V6.0 所有 WAAPI 修复。

local r = reaper
local ctx = r.ImGui_CreateContext('WwiseBridge')
local sep = package.config:sub(1,1)

-- === 用户配置 ===
local settings = {
    wwise_root_path = "\\Actor-Mixer Hierarchy\\Default Work Unit", 
    render_path = "", 
    container_types = {"Random Container", "Sequence Container", "Blend Container", "Switch Container"},
    default_container = 0,
    scan_mode = 0,
    
    -- [新功能] Normalization 设置
    enable_norm = false,      -- 开关
    target_peak_db = -1.0     -- 目标 Peak dB
}

local container_types_str = table.concat(settings.container_types, '\0') .. '\0'
local scan_modes_list = "All Regions (Recommended)\0Region Render Matrix\0Regions over Selected Items\0Time Selection\0"

local region_groups = {} 
local debug_info = "Ready."
local last_scan_count = 0

-- === JSON 转换 (V6 Kernel) ===
function LuaTableToAkJson(val)
    local t = type(val)
    if t == "table" then
        local is_array = false
        local max_idx = 0
        for k, _ in pairs(val) do
            if type(k) == "number" then
                if k > max_idx then max_idx = k end
                is_array = true
            end
        end
        if is_array and #val > 0 then
            local arr = r.AK_AkJson_Array()
            for _, v in ipairs(val) do r.AK_AkJson_Array_Add(arr, LuaTableToAkJson(v)) end
            return arr
        else
            local map = r.AK_AkJson_Map()
            for k, v in pairs(val) do r.AK_AkJson_Map_Set(map, tostring(k), LuaTableToAkJson(v)) end
            return map
        end
    elseif t == "string" then return r.AK_AkVariant_String(val)
    elseif t == "number" then return r.AK_AkVariant_Double(val)
    elseif t == "boolean" then return r.AK_AkVariant_Bool(val)
    end
    return r.AK_AkVariant_String("null")
end

-- === 工具函数 ===
function PathJoin(p1, p2)
    local p = p1 .. sep .. p2
    return p:gsub(sep..sep, sep)
end

function CheckDependencies()
    if not r.ImGui_CreateContext then r.ShowMessageBox("Error: ReaImGui Missing!", "Error", 0) return false end
    if not r.AK_Waapi_Connect then r.ShowMessageBox("Error: ReaWwise Missing!", "Error", 0) return false end
    return true
end

function ConnectWaapi()
    if not r.AK_Waapi_Connect("127.0.0.1", 8080) then
        r.ShowMessageBox("Connection Failed: Please open Wwise and enable WAAPI.", "Error", 0)
        return false
    end
    return true
end

-- === 扫描相关 ===
function GetMatrixRegionIDs(proj)
    local valid_ids = {}
    local i = 0
    while true do
        local status, region_id, track = pcall(r.EnumRegionRenderMatrix, proj, i)
        if not status or not region_id then break end
        valid_ids[region_id] = true
        i = i + 1
    end
    return valid_ids
end

function GetSelectedRanges(proj)
    local count = r.CountSelectedMediaItems(proj)
    if count == 0 then return nil end
    local ranges = {}
    for i = 0, count - 1 do
        local item = r.GetSelectedMediaItem(proj, i)
        local start = r.GetMediaItemInfo_Value(item, "D_POSITION")
        local len = r.GetMediaItemInfo_Value(item, "D_LENGTH")
        table.insert(ranges, {s = start, e = start + len})
    end
    return ranges
end

function ScanRegions()
    region_groups = {}
    local matched_regions = 0
    local proj = r.EnumProjects(-1, "")
    local allowed_ids, sel_ranges = nil, nil
    local t_s, t_e = 0, 0
    
    if settings.scan_mode == 1 then allowed_ids = GetMatrixRegionIDs(proj) end
    if settings.scan_mode == 2 then sel_ranges = GetSelectedRanges(proj) end
    if settings.scan_mode == 3 then t_s, t_e = r.GetSet_LoopTimeRange(false, false, 0, 0, false) end
    
    if settings.render_path == "" then settings.render_path = PathJoin(r.GetProjectPath(), "Rendered_Assets") end
    
    local i = 0
    while true do
        local retval, isrgn, pos, rgnend, name, idx = r.EnumProjectMarkers3(proj, i)
        if retval == 0 then break end 
        if isrgn then
            local is_valid = false
            if settings.scan_mode == 0 then is_valid = true 
            elseif settings.scan_mode == 1 then
                if allowed_ids and allowed_ids[idx] then is_valid = true end
            elseif settings.scan_mode == 2 then
                if sel_ranges then
                    for _, range in ipairs(sel_ranges) do
                        if (rgnend > range.s + 0.001) and (pos < range.e - 0.001) then is_valid = true; break end
                    end
                end
            elseif settings.scan_mode == 3 then
                 if (pos >= t_s - 0.001) and (rgnend <= t_e + 0.001) and (t_s ~= t_e) then is_valid = true end
            end
            
            if is_valid then
                matched_regions = matched_regions + 1
                local base_name = name:match("^(.*)_%d+$")
                if not base_name then base_name = name end 
                if base_name == "" then base_name = "Unnamed" end
                if not region_groups[base_name] then
                    region_groups[base_name] = { name = base_name, items = {}, container_idx = settings.default_container }
                end
                table.insert(region_groups[base_name].items, { rgn_name = name, start_pos = pos, end_pos = rgnend })
            end
        end
        i = i + 1
    end
    last_scan_count = matched_regions
    debug_info = string.format("Scan Complete. Matched %d regions.", matched_regions)
end

-- === [新] Normalization 逻辑 ===
function SelectItemsInRegion(proj, start_pos, end_pos)
    r.Main_OnCommand(40289, 0) -- Unselect all items
    
    local count = r.CountMediaItems(proj)
    local items_found = {}
    
    for i = 0, count - 1 do
        local item = r.GetMediaItem(proj, i)
        local i_start = r.GetMediaItemInfo_Value(item, "D_POSITION")
        local i_len = r.GetMediaItemInfo_Value(item, "D_LENGTH")
        local i_end = i_start + i_len
        
        -- 碰撞检测：Item 在 Region 范围内
        if (i_end > start_pos + 0.001) and (i_start < end_pos - 0.001) then
            r.SetMediaItemSelected(item, true)
            table.insert(items_found, item)
        end
    end
    return items_found
end

function ApplyNormalization(items)
    -- 1. 记录原始音量 (用于复原)
    local original_vols = {}
    for _, item in ipairs(items) do
        table.insert(original_vols, {
            item = item,
            vol = r.GetMediaItemInfo_Value(item, "D_VOL")
        })
    end
    
    -- 2. 执行 Common Gain Normalize 到 0dB (Action 42460)
    -- 这个 Action 会保持多个 Item 之间的相对比例
    r.Main_OnCommand(42460, 0) 
    
    -- 3. 应用目标 dB 偏移
    -- 如果目标是 -1dB，则在 0dB 基础上乘以 10^(-1/20)
    local gain_factor = 10 ^ (settings.target_peak_db / 20)
    
    -- 由于 Action 42460 已经把最高点推到了 1.0 (0dB)
    -- 我们只需要把所有选中 items 再乘以 gain_factor 即可
    for _, item in ipairs(items) do
        local current_vol = r.GetMediaItemInfo_Value(item, "D_VOL")
        r.SetMediaItemInfo_Value(item, "D_VOL", current_vol * gain_factor)
    end
    
    r.UpdateArrange() -- 刷新界面显示波形变化
    return original_vols
end

function RestoreVolumes(vol_data)
    for _, data in ipairs(vol_data) do
        -- 检查 Item 是否还存在 (防止渲染出错导致崩溃)
        if r.ValidatePtr(data.item, "MediaItem*") then
            r.SetMediaItemInfo_Value(data.item, "D_VOL", data.vol)
        end
    end
    r.UpdateArrange()
end

-- === 执行处理 ===
function ExecuteProcess()
    if not ConnectWaapi() then return end
    local proj = r.EnumProjects(-1, "")
    process_log = "=== Task Started ===\n"
    r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 2, true) 
    
    -- 为了安全，临时关闭 UI 更新
    r.PreventUIRefresh(1)
    
    for group_name, group_data in pairs(region_groups) do
        local container_type_str = settings.container_types[group_data.container_idx + 1]
        local wwise_type = "RandomSequenceContainer" 
        if container_type_str == "Switch Container" then wwise_type = "SwitchContainer" end
        if container_type_str == "Blend Container" then wwise_type = "BlendContainer" end
        
        -- A. Render Phase
        for _, item in ipairs(group_data.items) do
            r.GetSet_LoopTimeRange(true, false, item.start_pos, item.end_pos, false) 
            
            -- [Step 1] Normalization Pre-Process
            local vol_snapshot = nil
            if settings.enable_norm then
                local selected_items = SelectItemsInRegion(proj, item.start_pos, item.end_pos)
                if #selected_items > 0 then
                    vol_snapshot = ApplyNormalization(selected_items)
                else
                    process_log = process_log .. "[Warn] No items found in region: " .. item.rgn_name .. "\n"
                end
            end
            
            -- [Step 2] Render
            r.GetSetProjectInfo_String(proj, "RENDER_FILE", settings.render_path, true)
            r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", item.rgn_name, true)
            r.Main_OnCommand(42230, 0) 
            
            -- [Step 3] Restore Volumes
            if vol_snapshot then
                RestoreVolumes(vol_snapshot)
            end
        end
        process_log = process_log .. "Rendered: " .. group_name .. "\n"
        
        -- B. WAAPI Import Phase
        local create_args_tbl = {
            parent = settings.wwise_root_path, type = wwise_type, name = group_name, onNameConflict = "merge"
        }
        local args_ak = LuaTableToAkJson(create_args_tbl)
        local opts_ak = r.AK_AkJson_Map()
        r.AK_Waapi_Call("ak.wwise.core.object.create", args_ak, opts_ak)
        
        local imports_list = {}
        local parent_obj_path = settings.wwise_root_path .. "\\" .. group_name
        for _, item in ipairs(group_data.items) do
            local file_full_path = PathJoin(settings.render_path, item.rgn_name .. ".wav")
            table.insert(imports_list, { audioFile = file_full_path, objectPath = parent_obj_path .. "\\<Sound>" .. item.rgn_name })
        end
        
        local import_payload_tbl = {
            importOperation = "useExisting", default = { importLanguage = "SFX" }, imports = imports_list
        }
        local import_args_ak = LuaTableToAkJson(import_payload_tbl)
        local import_opts_ak = r.AK_AkJson_Map()
        
        local res = r.AK_Waapi_Call("ak.wwise.core.audio.import", import_args_ak, import_opts_ak)
        if res then process_log = process_log .. "Imported to Wwise: " .. group_name .. "\n" 
        else process_log = process_log .. "!!! Wwise Import Failed: " .. group_name .. "\n" end
    end
    
    r.AK_AkJson_ClearAll()
    r.PreventUIRefresh(-1)
    r.UpdateArrange()
    
    r.ShowMessageBox("Done!\n" .. process_log, "Result", 0)
    r.AK_Waapi_Disconnect() 
end

-- === UI 循环 ===
function Loop()
    local visible, open = r.ImGui_Begin(ctx, 'Reaper to Wwise Bridge', true)
    if visible then
        if r.ImGui_CollapsingHeader(ctx, 'Config', r.ImGui_TreeNodeFlags_DefaultOpen()) then
            -- 扫描设置
            r.ImGui_Text(ctx, "Scan Mode:")
            r.ImGui_SetNextItemWidth(ctx, 250)
            local changed, new_mode = r.ImGui_Combo(ctx, "##scanmode", settings.scan_mode, scan_modes_list)
            if changed then settings.scan_mode = new_mode; ScanRegions() end
            
            -- Normalization 设置
            r.ImGui_Separator(ctx)
            r.ImGui_Text(ctx, "Audio Processing:")
            local norm_changed, norm_val = r.ImGui_Checkbox(ctx, "Enable Peak Normalization (Common Gain)", settings.enable_norm)
            if norm_changed then settings.enable_norm = norm_val end
            
            if settings.enable_norm then
                r.ImGui_SameLine(ctx)
                r.ImGui_SetNextItemWidth(ctx, 100)
                local db_changed, new_db = r.ImGui_SliderDouble(ctx, "Target dB", settings.target_peak_db, -12.0, 0.0, "%.1f dB")
                if db_changed then settings.target_peak_db = new_db end
            end
            r.ImGui_Separator(ctx)

            r.ImGui_Text(ctx, "Wwise Path:")
            _, settings.wwise_root_path = r.ImGui_InputText(ctx, '##wwisepath', settings.wwise_root_path)
            
            if r.ImGui_Button(ctx, 'Refresh / Rescan') then ScanRegions() end
            r.ImGui_TextColored(ctx, 0xFFFF00FF, debug_info)
        end
        r.ImGui_Separator(ctx)
        
        if r.ImGui_BeginTable(ctx, 'AssetsTable', 3, r.ImGui_TableFlags_Borders() | r.ImGui_TableFlags_RowBg() | r.ImGui_TableFlags_Resizable()) then
            r.ImGui_TableSetupColumn(ctx, 'Group Name', r.ImGui_TableColumnFlags_WidthStretch())
            r.ImGui_TableSetupColumn(ctx, 'Count', r.ImGui_TableColumnFlags_WidthFixed(), 50.0)
            r.ImGui_TableSetupColumn(ctx, 'Container Type', r.ImGui_TableColumnFlags_WidthFixed(), 150.0)
            r.ImGui_TableHeadersRow(ctx)
            
            for name, data in pairs(region_groups) do
                r.ImGui_TableNextRow(ctx)
                r.ImGui_TableSetColumnIndex(ctx, 0)
                r.ImGui_Text(ctx, name) 
                r.ImGui_TableSetColumnIndex(ctx, 1)
                r.ImGui_Text(ctx, tostring(#data.items))
                r.ImGui_TableSetColumnIndex(ctx, 2)
                r.ImGui_PushID(ctx, name)
                r.ImGui_SetNextItemWidth(ctx, -1)
                local changed, new_val = r.ImGui_Combo(ctx, "", data.container_idx, container_types_str)
                if changed then data.container_idx = new_val end
                r.ImGui_PopID(ctx)
            end
            r.ImGui_EndTable(ctx)
        end
        r.ImGui_Separator(ctx)
        local has_data = (last_scan_count > 0)
        if not has_data then r.ImGui_BeginDisabled(ctx) end
        if r.ImGui_Button(ctx, 'EXECUTE: Render & Import', -1, 50) then ExecuteProcess() end
        if not has_data then r.ImGui_EndDisabled(ctx) end
        r.ImGui_End(ctx)
    end
    if open then r.defer(Loop) end
end

if CheckDependencies() then
    ScanRegions()
    r.defer(Loop)
end
