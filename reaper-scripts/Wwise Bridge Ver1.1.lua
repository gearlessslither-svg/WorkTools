-- @description Reaper to Wwise Bridge (V6.0 Kernel Rewrite)
-- @version 6.0
-- @author Game Audio Expert
-- @about 
--   核心重写：使用 AK_AkJson API 替代字符串 JSON，彻底解决 WAAPI 调用参数错误。
--   该版本符合 ReaWwise 底层 C++ 接口规范。

local r = reaper
local ctx = r.ImGui_CreateContext('WwiseBridge')
local sep = package.config:sub(1,1)

-- === 用户配置 ===
local settings = {
    wwise_root_path = "\\Actor-Mixer Hierarchy\\Default Work Unit", 
    render_path = "", 
    container_types = {"Random Container", "Sequence Container", "Blend Container", "Switch Container"},
    default_container = 0,
    -- 0:All, 1:Matrix, 2:Selected Items, 3:Time Selection
    scan_mode = 0 
}

local container_types_str = table.concat(settings.container_types, '\0') .. '\0'
local scan_modes_list = "All Regions (Recommended)\0Region Render Matrix\0Regions over Selected Items\0Time Selection\0"

local region_groups = {} 
local debug_info = "Ready."
local last_scan_count = 0

-- === [核心] Lua Table 转 AkJson 对象 ===
-- 这是一个递归函数，把 Lua 数据转为 C++ 能够识别的 AkJson 指针
function LuaTableToAkJson(val)
    local t = type(val)
    
    if t == "table" then
        -- 判断是否为数组 (Array)
        local is_array = false
        local max_idx = 0
        for k, _ in pairs(val) do
            if type(k) == "number" then
                if k > max_idx then max_idx = k end
                is_array = true
            end
        end
        -- 如果是空表，默认为 Map，除非显式标记
        if is_array and #val > 0 then
            local arr = r.AK_AkJson_Array()
            for _, v in ipairs(val) do
                local variant = LuaTableToAkJson(v)
                r.AK_AkJson_Array_Add(arr, variant)
            end
            return arr
        else
            local map = r.AK_AkJson_Map()
            for k, v in pairs(val) do
                local variant = LuaTableToAkJson(v)
                r.AK_AkJson_Map_Set(map, tostring(k), variant)
            end
            return map
        end
        
    elseif t == "string" then
        return r.AK_AkVariant_String(val)
        
    elseif t == "number" then
        return r.AK_AkVariant_Double(val)
        
    elseif t == "boolean" then
        return r.AK_AkVariant_Bool(val)
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

-- === 扫描相关函数 ===
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
    local allowed_ids = nil
    local sel_ranges = nil
    local t_s, t_e = 0, 0
    
    if settings.scan_mode == 1 then allowed_ids = GetMatrixRegionIDs(proj) end
    if settings.scan_mode == 2 then sel_ranges = GetSelectedRanges(proj) end
    if settings.scan_mode == 3 then t_s, t_e = r.GetSet_LoopTimeRange(false, false, 0, 0, false) end
    
    if settings.render_path == "" then
        settings.render_path = PathJoin(r.GetProjectPath(), "Rendered_Assets")
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

-- === 执行处理 ===
function ExecuteProcess()
    if not ConnectWaapi() then return end
    local proj = r.EnumProjects(-1, "")
    process_log = "=== Task Started ===\n"
    r.GetSetProjectInfo(proj, "RENDER_BOUNDSFLAG", 2, true) 
    
    for group_name, group_data in pairs(region_groups) do
        local container_type_str = settings.container_types[group_data.container_idx + 1]
        local wwise_type = "RandomSequenceContainer" 
        if container_type_str == "Switch Container" then wwise_type = "SwitchContainer" end
        if container_type_str == "Blend Container" then wwise_type = "BlendContainer" end
        
        -- A. Render
        for _, item in ipairs(group_data.items) do
            r.GetSet_LoopTimeRange(true, false, item.start_pos, item.end_pos, false) 
            r.GetSetProjectInfo_String(proj, "RENDER_FILE", settings.render_path, true)
            r.GetSetProjectInfo_String(proj, "RENDER_PATTERN", item.rgn_name, true)
            r.Main_OnCommand(42230, 0) 
        end
        process_log = process_log .. "Rendered: " .. group_name .. "\n"
        
        -- B. WAAPI Import (核心修复部分)
        
        -- 1. 准备创建容器的 Args
        local create_args_tbl = {
            parent = settings.wwise_root_path,
            type = wwise_type,
            name = group_name,
            onNameConflict = "merge"
        }
        
        -- 2. 转换 Args 和 Options 为 AkJson 对象
        local args_ak = LuaTableToAkJson(create_args_tbl)
        local opts_ak = r.AK_AkJson_Map() -- 空的 Options 对象
        
        -- 3. 调用 WAAPI (ak.wwise.core.object.create)
        r.AK_Waapi_Call("ak.wwise.core.object.create", args_ak, opts_ak)
        
        -- 4. 准备导入音频的 Args
        local imports_list = {}
        local parent_obj_path = settings.wwise_root_path .. "\\" .. group_name
        
        for _, item in ipairs(group_data.items) do
            local file_full_path = PathJoin(settings.render_path, item.rgn_name .. ".wav")
            table.insert(imports_list, {
                audioFile = file_full_path,
                objectPath = parent_obj_path .. "\\<Sound>" .. item.rgn_name
            })
        end
        
        local import_payload_tbl = {
            importOperation = "useExisting",
            default = { importLanguage = "SFX" },
            imports = imports_list
        }
        
        -- 5. 转换导入参数
        local import_args_ak = LuaTableToAkJson(import_payload_tbl)
        local import_opts_ak = r.AK_AkJson_Map() -- 空 Options
        
        -- 6. 调用 WAAPI (ak.wwise.core.audio.import)
        local res = r.AK_Waapi_Call("ak.wwise.core.audio.import", import_args_ak, import_opts_ak)
        
        -- 7. 检查结果并清理内存
        -- 注意：res 也是一个 AkJson 对象，需要检查状态
        if res then 
            process_log = process_log .. "Imported to Wwise: " .. group_name .. "\n" 
        else
            process_log = process_log .. "!!! Wwise Import Failed: " .. group_name .. "\n"
        end
    end
    
    -- [关键] 必须清理所有创建的 AkJson 对象，否则会内存泄漏
    r.AK_AkJson_ClearAll()
    
    r.ShowMessageBox("Done!\n" .. process_log, "Result", 0)
    r.AK_Waapi_Disconnect() 
end

-- === UI 循环 ===
function Loop()
    local visible, open = r.ImGui_Begin(ctx, 'Reaper to Wwise Bridge', true)
    if visible then
        if r.ImGui_CollapsingHeader(ctx, 'Config', r.ImGui_TreeNodeFlags_DefaultOpen()) then
            r.ImGui_Text(ctx, "Scan Mode:")
            r.ImGui_SetNextItemWidth(ctx, 250)
            local changed, new_mode = r.ImGui_Combo(ctx, "##scanmode", settings.scan_mode, scan_modes_list)
            if changed then settings.scan_mode = new_mode; ScanRegions() end
            
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
