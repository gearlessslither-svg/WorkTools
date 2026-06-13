-- @description Batch Create Regions: Auto-Group Layers (Smart Clustering)
-- @version 5.0
-- @author Game Audio Expert
-- @about
--   1. Sorts all selected items by time.
--   2. Groups items that overlap or are close to each other (Vertical Layers).
--   3. Separates items that are far apart (Horizontal Sequence).
--   4. Creates a Region for EACH group using Top-Track Naming logic.

-- === 设置：判定阈值 ===
-- 如果两块素材之间的空隙小于这个秒数，视为同一个音效的一部分
-- 如果大于这个秒数，视为下一个独立的音效
local GROUP_THRESHOLD = 0.15 

function main()
    reaper.Undo_BeginBlock() 

    -- 1. 获取所有选中项
    local count = reaper.CountSelectedMediaItems(0)
    if count == 0 then return end

    -- 2. 收集数据到 Lua 表中 (以便排序)
    local items_data = {}
    for i = 0, count - 1 do
        local item = reaper.GetSelectedMediaItem(0, i)
        local pos = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
        local length = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
        local end_pos = pos + length
        local track = reaper.GetMediaItem_Track(item)
        local track_idx = reaper.GetMediaTrackInfo_Value(track, "IP_TRACKNUMBER") -- 1-based index
        
        table.insert(items_data, {
            item = item,
            start_p = pos,
            end_p = end_pos,
            track = track,
            track_idx = track_idx
        })
    end

    -- 3. 按开始时间排序 (关键步骤)
    table.sort(items_data, function(a, b) return a.start_p < b.start_p end)

    -- 4. 执行聚类 (Grouping)
    local groups = {}
    if #items_data > 0 then
        -- 初始化第一个组
        local current_group = { items_data[1] }
        -- 当前组的“最晚结束时间”
        local current_group_max_end = items_data[1].end_p 

        for i = 2, #items_data do
            local current_item = items_data[i]
            
            -- 判断：当前素材的开始时间，是否在 (当前组最晚结束时间 + 阈值) 之内？
            if current_item.start_p < (current_group_max_end + GROUP_THRESHOLD) then
                -- >> 是同一组 (重叠或紧邻)
                table.insert(current_group, current_item)
                
                -- 更新当前组的最晚结束时间
                if current_item.end_p > current_group_max_end then
                    current_group_max_end = current_item.end_p
                end
            else
                -- >> 离得太远，是新的一组
                table.insert(groups, current_group) -- 保存旧组
                current_group = { current_item }    -- 开启新组
                current_group_max_end = current_item.end_p
            end
        end
        table.insert(groups, current_group) -- 保存最后一组
    end

    -- 5. 遍历每一组，生成 Region (复用 V4.0 的核心逻辑)
    -- 预先获取所有 Marker 以便查询 (性能优化)
    local all_markers = {}
    local m_i = 0
    while true do
        local ret, isrgn, pos, rgnend, name, idx = reaper.EnumProjectMarkers(m_i)
        if ret == 0 then break end
        if not isrgn then table.insert(all_markers, {pos=pos, name=name}) end
        m_i = m_i + 1
    end

    -- 开始处理每个组
    for _, group in ipairs(groups) do
        -- A. 计算该组的 Bounding Box 和 Top Track
        local g_start = math.huge
        local g_end = -math.huge
        local min_track_idx = 999999
        local primary_track = nil

        for _, data in ipairs(group) do
            if data.start_p < g_start then g_start = data.start_p end
            if data.end_p > g_end then g_end = data.end_p end
            
            -- 寻找最顶层轨道
            if data.track_idx < min_track_idx then
                min_track_idx = data.track_idx
                primary_track = data.track
            end
        end

        -- B. 获取命名 (Folder + Track)
        if primary_track then
            local retval, track_name = reaper.GetSetMediaTrackInfo_String(primary_track, "P_NAME", "", false)
            
            local folder_name = ""
            local parent_track = reaper.GetParentTrack(primary_track)
            if parent_track then
                local retval_p, parent_name_str = reaper.GetSetMediaTrackInfo_String(parent_track, "P_NAME", "", false)
                if retval_p and parent_name_str ~= "" then
                    folder_name = parent_name_str .. "_"
                end
            end

            -- C. 寻找该组范围内的 Marker
            local marker_str = ""
            for _, mrk in ipairs(all_markers) do
                if mrk.pos >= g_start and mrk.pos <= g_end then
                    if mrk.name ~= "" then
                        marker_str = "_" .. mrk.name
                        break -- 每个 Region 只取一个 Marker
                    end
                end
            end

            -- D. 创建 Region
            local final_name = folder_name .. track_name .. marker_str
            reaper.AddProjectMarker2(0, true, g_start, g_end, final_name, -1, 0)
        end
    end

    reaper.UpdateTimeline()
    reaper.Undo_EndBlock("Batch Create Smart Regions", -1)
end

main()
