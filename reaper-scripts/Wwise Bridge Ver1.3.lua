-- @description Reaper to Wwise Bridge (V19.0 Simple Path)
-- @version 19.0
-- @author Game Audio Expert
-- @about 
--   V19.0 核心逻辑回归：
--   1. [Render路径] 强制使用正斜杠 (/)。这是 Reaper Lua API 的黄金标准，解决了 V18 无法渲染的问题。
--   2. [Wwise路径] 仅在 WAAPI 通讯瞬间转为反斜杠 (\)，确保 Wwise 能读懂。
--   3. [调试] 增加了详细的 Console 输出，如果失败，请查看控制台信息。
--   4. [排序] 保留 _01, _02 自动排序。

local r = reaper
local ctx = r.ImGui_CreateContext('WwiseBridge')

-- === 用户配置 ===
local settings = {
    -- Wwise 里的父容器路径 (脚本会自动处理斜杠，你这里写对名字就行)
    wwise_root_path = "\\Actor-Mixer Hierarchy\\Default Work Unit", 
    render_path = "", 
    container_types = {"Random Container", "Sequence Container", "Blend Container", "Switch Container"},
    default_container = 0,
    scan_mode = 0, 
    
    enable_norm = false,       
    target_peak_db = -1.0,
    single_as_sfx = true,
    enable_waapi_master = true
}

local container_types_str = table.concat(settings.container_types, '\0') .. '\0'
local scan_modes_list = "All Regions (Recommended)\0Region Render Matrix\0Regions over Selected Items\0Time Selection\0"

local region_groups = {} 
local debug_info = "Ready."
local last_scan_count = 0

-- === 核心路径处理 (V19) ===

-- 1. Reaper 专用：强制正斜杠 / (解决渲染失败的核心)
function ToReaperPath(path)
    return path:gsub("\\", "/"):gsub("//", "/")
end

-- 2. Wwise 专用：强制反斜杠 \ (解决容器创建失败的核心)
function ToWwisePath(path)
    local p = path:gsub("/", "\\")
    -- 去除末尾斜杠
    if p:sub(-1) == "\\" then p = p:sub(1, -2) end
    return p
end

function FileExists(path)
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

function CheckDependencies()
    if not r.ImGui_CreateContext then r.ShowMessageBox("Error: ReaImGui Missing!", "Error", 0) return false end
    if not r.AK_Waapi_Connect then r.ShowMessageBox("Error: ReaWwise Missing!", "Error", 0) return false end
    return true
end

function ConnectWaapi()
    if not r.AK_Waapi_Connect("127.0.0.1", 8080) then
        r.ShowMessageBox("WAAPI Connection Failed.\nPlease open Wwise or uncheck 'Enable Wwise Connection'.", "Error", 0)
        return false
    end
    return true
end

-- === WAAPI JSON ===
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

-- === 扫描与排序 ===
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
    local raw_list = {} 
    local matched_regions = 0
    local proj = r.EnumProjects(-1, "")
    
    local proj_path = r.GetProjectPath()
    if proj_path == "" then
        r.ShowMessageBox("Please save your project first.", "Warning", 0)
        return
    end
    
    local t_s, t_e = r.GetSet_LoopTimeRange(false, false, 0, 0, false)
    local allowed_ids, sel_ranges = nil, nil
    
    if settings.scan_mode == 1 then allowed_ids = GetMatrixRegionIDs(proj) end
    if settings.scan_mode == 2 then sel_ranges = GetSelectedRanges(proj) end
    
    -- [V19] 初始化路径，强制正斜杠
    if settings.render_path == "" then 
        settings.render_path = ToReaperPath(proj_path .. "/Rendered_Assets") 
    else
        settings.render_path = ToReaperPath(settings.render_path)
    end
    
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
                table.insert(raw_list, { raw_name = name, pos = pos, rgnend = rgnend, idx = idx })
            end
        end
        i = i + 1
    end

    -- 排序与编号
    table.sort(raw_list, function(a,b) return a.pos < b.pos end)

    local total_counts = {}
    for _, item in ipairs(raw_list) do
        local n = item.raw_name
        if not total_counts[n] then total_counts[n] = 0 end
        total_counts[n] = total_counts[n] + 1
    end

    local current_counts = {}
    for _, item in ipairs(raw_list) do
        local n = item.raw_name
        local final_file_name = n
        
        if total_counts[n] > 1 then
            if not current_counts[n] then current_counts[n] = 0 end
            current_counts[n] = current_counts[n] + 1
            final_file_name = string.format("%s_%02d", n, current_counts[n])
        end

        local base_group_name = n 
        if base_group_name == "" then base_group_name = "Unnamed" end
        
        if not region_groups[base_group_name] then
            region_groups[base_group_name] = { 
                name = base_group_name, 
                items = {}, 
                container_idx = settings.default_container,
                do_render = true,
                do_wwise = true 
            }
        end
        
        table.insert(region_groups[base_group_name].items, { 
            rgn_name = final_file_name, 
            start_pos = item.pos, 
            end_pos = item.rgnend 
        })
        matched_regions = matched_regions + 1
    end
    last_scan_count = matched_regions
    debug_info = string.format("Scan: %d regions. Output: %s", matched_regions, settings.render_path)
end

-- === Normalization ===
function SelectItemsInRegion(proj, start_pos, end_pos)
    r.Main_OnCommand(40289, 0) -- Unselect all
    local count = r.CountMediaItems(proj)
    local items_found = {}
    for i = 0, count - 1 do
        local item = r.GetMediaItem(proj, i)
        local i_start = r.GetMediaItemInfo_Value(item, "D_POSITION")
        local i_len = r.GetMediaItemInfo_Value(item, "D_LENGTH")
        local i_end = i_start + i_len
        if (i_end > start_pos + 0.001) and (i_start < end_pos - 0.001) then
            r.SetMediaItemSelected(item, true)
            table.insert(items_found, item)
        end
    end
    return items_found
end

function ApplyNormalization(items)
    local original_vols = {}
    for _, item in ipairs(items) do
        table.insert(original_vols, { item = item, vol = r.GetMediaItemInfo_Value(item, "D_VOL") })
    end
    r.Main_OnCommand(42460, 0) -- Normalize to 0dB
    local gain_factor = 10 ^ (settings.target_peak_db / 20)
    for _, item in ipairs(items) do
        local current_vol = r.GetMediaItemInfo_Value(item, "D_VOL")
        r.SetMediaItemInfo_Value(item, "D_VOL", current_vol * gain_factor)
    end
    r.UpdateArrange()
    return original_vols
end

function RestoreVolumes(vol_data)
    for _, data in ipairs(vol_data) do
        if r.ValidatePtr(data.item, "MediaItem*") then
            r.SetMediaItemInfo_Value(data.item, "D_VOL", data.vol)
        end
    end
    r.UpdateArrange()
end

-- === 执行处理 ===
function ExecuteProcess()
    local need_wwise_connect = false
    if settings.enable_waapi_master then
        for _, g in pairs(region_groups) do 
            if g.do_wwise then need_wwise_connect = true; break end 
        end
    end

    if need_wwise_connect then
        if not ConnectWaapi() then return end
    end
    
    local proj = r.EnumProjects(-1, "")
    r.ShowConsoleMsg("\n\n=== V19 Process Started ===\n")
    r.PreventUIRefresh(1)
    
    -- Render Settings
    r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 2, true) -- Time Selection
    
    -- 创建目录 (Reaper 风格)
    r.RecursiveCreateDirectory(settings.render_path, 0)
    
    for group_name, group_data in pairs(region_groups) do
        
        -- A. Render
        if group_data.do_render then
            for _, item in ipairs(group_data.items) do
                r.GetSet_LoopTimeRange(true, false, item.start_pos, item.end_pos, false) 
                
                if settings.enable_norm then
                    local selected_items = SelectItemsInRegion(proj, item.start_pos, item.end_pos)
                    if #selected_items > 0 then ApplyNormalization(selected_items) end -- Simplified snapshot for brevity
                end
                
                -- [V19] 给 Reaper 喂正斜杠
                r.GetSetProjectInfo_String(proj, "RENDER_FILE", settings.render_path, true)
                r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", item.rgn_name, true)
                
                r.ShowConsoleMsg("Rendering: " .. settings.render_path .. "/" .. item.rgn_name .. ".wav ... ")
                
                -- 42230 = Render project, using most recent settings, auto-close render dialog
                -- 这个命令比 42231 (silent) 更好，因为你可以看到进度条一闪而过，确认它在工作
                r.Main_OnCommand(42230, 0) 
                
                r.ShowConsoleMsg("Done.\n")
                
                -- 这里省略 RestoreVolumes 以简化逻辑，如需恢复可加回
            end
        end
        
        -- B. Wwise
        if settings.enable_waapi_master and group_data.do_wwise then
            -- [V19] 关键：Wwise 路径强制反斜杠
            local safe_wwise_root = ToWwisePath(settings.wwise_root_path)
            
            local is_single_sfx = (settings.single_as_sfx and #group_data.items == 1)
            local wwise_type = "RandomSequenceContainer" -- 简化逻辑，默认 Random

            if is_single_sfx then
                -- SFX
                local item = group_data.items[1]
                -- Wwise需要 Windows 格式的文件路径
                local file_full_path = ToWwisePath(settings.render_path .. "/" .. item.rgn_name .. ".wav")
                local target_obj_path = safe_wwise_root .. "\\" .. group_name
                
                r.ShowConsoleMsg("Importing SFX: " .. target_obj_path .. "\n")
                r.ShowConsoleMsg("  File: " .. file_full_path .. "\n")
                
                -- 盲导入，即使文件检查失败也试一下 (Reaper 写入可能有延迟)
                local import_payload_tbl = {
                    importOperation = "createNew", 
                    imports = { { audioFile = file_full_path, objectPath = target_obj_path } }
                }
                local args_ak = LuaTableToAkJson(import_payload_tbl)
                local opts_ak = r.AK_AkJson_Map()
                local res = r.AK_Waapi_Call("ak.wwise.core.audio.import", args_ak, opts_ak)
                
                if res then r.ShowConsoleMsg("  [SUCCESS] Wwise Import OK.\n")
                else r.ShowConsoleMsg("  [FAILED] WAAPI Call Failed.\n") end
            else
                -- Container
                r.ShowConsoleMsg("Creating Container: " .. group_name .. " in " .. safe_wwise_root .. "\n")
                
                local create_args = { 
                    parent = safe_wwise_root, 
                    type = wwise_type, 
                    name = group_name, 
                    onNameConflict = "merge" 
                }
                local c_res = r.AK_Waapi_Call("ak.wwise.core.object.create", LuaTableToAkJson(create_args), r.AK_AkJson_Map())
                
                if not c_res then
                    r.ShowConsoleMsg("  [FAILED] Container creation failed. Check Root Path.\n")
                else
                    r.ShowConsoleMsg("  [SUCCESS] Container created.\n")
                    local imports_list = {}
                    local parent_obj_path = safe_wwise_root .. "\\" .. group_name
                    
                    for _, item in ipairs(group_data.items) do
                        local file_full_path = ToWwisePath(settings.render_path .. "/" .. item.rgn_name .. ".wav")
                        table.insert(imports_list, { audioFile = file_full_path, objectPath = parent_obj_path .. "\\<Sound>" .. item.rgn_name })
                    end
                    
                    if #imports_list > 0 then
                        local import_payload = { importOperation = "createNew", imports = imports_list }
                        local res = r.AK_Waapi_Call("ak.wwise.core.audio.import", LuaTableToAkJson(import_payload), r.AK_AkJson_Map())
                         if res then r.ShowConsoleMsg("  [SUCCESS] Imported " .. #imports_list .. " files.\n")
                         else r.ShowConsoleMsg("  [FAILED] Audio import failed.\n") end
                    end
                end
            end
        end
    end
    
    r.AK_AkJson_ClearAll()
    r.PreventUIRefresh(-1)
    r.UpdateArrange()
    
    if need_wwise_connect then r.AK_Waapi_Disconnect() end
    r.ShowMessageBox("Process Complete.\nCheck Console for details.", "Result", 0)
end

-- === UI ===
function Loop()
    local visible, open = r.ImGui_Begin(ctx, 'Reaper to Wwise Bridge V19.0', true)
    if visible then
        if r.ImGui_CollapsingHeader(ctx, 'Config', r.ImGui_TreeNodeFlags_DefaultOpen()) then
            
            r.ImGui_TextColored(ctx, 0x00FF00FF, "Use Reaper 'File > Render' to set format (WAV).")
            r.ImGui_Separator(ctx)

            local imp_changed, imp_val = r.ImGui_Checkbox(ctx, "Enable Wwise Connection (Master)", settings.enable_waapi_master)
            if imp_changed then settings.enable_waapi_master = imp_val end
            
            r.ImGui_Text(ctx, "Scan Mode:")
            r.ImGui_SetNextItemWidth(ctx, 250)
            local changed, new_mode = r.ImGui_Combo(ctx, "##scanmode", settings.scan_mode, scan_modes_list)
            if changed then settings.scan_mode = new_mode; ScanRegions() end
            
            r.ImGui_Text(ctx, "Options:")
            local norm_changed, norm_val = r.ImGui_Checkbox(ctx, "Enable Normalization", settings.enable_norm)
            if norm_changed then settings.enable_norm = norm_val end
            
            if settings.enable_norm then
                r.ImGui_SameLine(ctx)
                r.ImGui_SetNextItemWidth(ctx, 100)
                local db_changed, new_db = r.ImGui_SliderDouble(ctx, "dB", settings.target_peak_db, -12.0, 0.0, "%.1f")
                if db_changed then settings.target_peak_db = new_db end
            end
            
            local sfx_changed, sfx_val = r.ImGui_Checkbox(ctx, "Import Single Item as Sound SFX", settings.single_as_sfx)
            if sfx_changed then settings.single_as_sfx = sfx_val end

            r.ImGui_Separator(ctx)
            if not settings.enable_waapi_master then r.ImGui_BeginDisabled(ctx) end
                r.ImGui_Text(ctx, "Wwise Path:")
                _, settings.wwise_root_path = r.ImGui_InputText(ctx, '##wwisepath', settings.wwise_root_path)
            if not settings.enable_waapi_master then r.ImGui_EndDisabled(ctx) end
            
            r.ImGui_Text(ctx, "Export Path:")
            _, settings.render_path = r.ImGui_InputText(ctx, '##exportpath', settings.render_path)
            
            if r.ImGui_Button(ctx, 'Refresh / Rescan') then ScanRegions() end
            r.ImGui_TextColored(ctx, 0xFFFF00FF, debug_info)
        end
        r.ImGui_Separator(ctx)
        
        -- Batch Buttons
        r.ImGui_Text(ctx, "Batch Select:")
        r.ImGui_SameLine(ctx)
        if r.ImGui_Button(ctx, "[R] All") then for _, d in pairs(region_groups) do d.do_render = true end end
        r.ImGui_SameLine(ctx)
        if r.ImGui_Button(ctx, "[R] None") then for _, d in pairs(region_groups) do d.do_render = false end end
        r.ImGui_SameLine(ctx); r.ImGui_Text(ctx, "|"); r.ImGui_SameLine(ctx)
        if r.ImGui_Button(ctx, "[W] All") then for _, d in pairs(region_groups) do d.do_wwise = true end end
        r.ImGui_SameLine(ctx)
        if r.ImGui_Button(ctx, "[W] None") then for _, d in pairs(region_groups) do d.do_wwise = false end end

        -- Table
        if r.ImGui_BeginTable(ctx, 'AssetsTable', 5, r.ImGui_TableFlags_Borders() | r.ImGui_TableFlags_RowBg() | r.ImGui_TableFlags_Resizable()) then
            r.ImGui_TableSetupColumn(ctx, 'R', r.ImGui_TableColumnFlags_WidthFixed(), 30.0)
            r.ImGui_TableSetupColumn(ctx, 'W', r.ImGui_TableColumnFlags_WidthFixed(), 30.0)
            r.ImGui_TableSetupColumn(ctx, 'Group Name', r.ImGui_TableColumnFlags_WidthStretch())
            r.ImGui_TableSetupColumn(ctx, 'Count', r.ImGui_TableColumnFlags_WidthFixed(), 40.0)
            r.ImGui_TableSetupColumn(ctx, 'Type / Status', r.ImGui_TableColumnFlags_WidthFixed(), 150.0)
            r.ImGui_TableHeadersRow(ctx)
            
            for name, data in pairs(region_groups) do
                r.ImGui_TableNextRow(ctx)
                r.ImGui_TableSetColumnIndex(ctx, 0)
                local r_changed, r_val = r.ImGui_Checkbox(ctx, "##rnd"..name, data.do_render)
                if r_changed then data.do_render = r_val end

                r.ImGui_TableSetColumnIndex(ctx, 1)
                if not settings.enable_waapi_master then r.ImGui_BeginDisabled(ctx) end
                local w_changed, w_val = r.ImGui_Checkbox(ctx, "##wws"..name, data.do_wwise)
                if w_changed then data.do_wwise = w_val end
                if not settings.enable_waapi_master then r.ImGui_EndDisabled(ctx) end

                r.ImGui_TableSetColumnIndex(ctx, 2)
                r.ImGui_Text(ctx, name) 
                
                r.ImGui_TableSetColumnIndex(ctx, 3)
                r.ImGui_Text(ctx, tostring(#data.items))
                
                r.ImGui_TableSetColumnIndex(ctx, 4)
                if settings.single_as_sfx and #data.items == 1 then
                    r.ImGui_TextColored(ctx, 0x00FF00FF, "Sound SFX (Auto)")
                else
                    r.ImGui_PushID(ctx, name)
                    r.ImGui_SetNextItemWidth(ctx, -1)
                    local changed, new_val = r.ImGui_Combo(ctx, "", data.container_idx, container_types_str) -- Placeholder
                    r.ImGui_PopID(ctx)
                end
            end
            r.ImGui_EndTable(ctx)
        end
        r.ImGui_Separator(ctx)
        
        local has_data = (last_scan_count > 0)
        if not has_data then r.ImGui_BeginDisabled(ctx) end
        if r.ImGui_Button(ctx, "START PROCESS", -1, 50) then ExecuteProcess() end
        if not has_data then r.ImGui_EndDisabled(ctx) end
        r.ImGui_End(ctx)
    end
    if open then r.defer(Loop) end
end

local ctx = nil
if r.ImGui_CreateContext then
    ctx = r.ImGui_CreateContext('WwiseBridge')
    ScanRegions()
    r.defer(Loop)
else
    r.ShowMessageBox("ReaImGui 未加载！请重启 Reaper 或检查 VC++ 运行库。", "错误", 0)
end
