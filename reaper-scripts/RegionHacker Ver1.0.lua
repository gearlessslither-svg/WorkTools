-- @description Auto Create Regions from Track Hierarchy (Simulating Wildcards)
-- @version 1.0
-- @author Game Audio Expert

function CreateRegionsFromSelection()
    -- 1. 检查是否有选中的 Item
    local count = reaper.CountSelectedMediaItems(0)
    if count == 0 then
        reaper.ShowMessageBox("请先选择至少一个 Item！", "提示", 0)
        return
    end

    reaper.Undo_BeginBlock() -- 开始 Undo 记录

    -- 用于重名计数的表
    local name_counters = {}

    for i = 0, count - 1 do
        local item = reaper.GetSelectedMediaItem(0, i)
        
        -- 2. 获取时间信息
        local start_pos = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
        local length = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
        local end_pos = start_pos + length
        
        -- 3. 获取轨道层级命名 (模拟 $folder$track)
        local track = reaper.GetMediaItem_Track(item)
        local _, track_name = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
        
        -- 清理轨道名中的特殊字符 (可选)
        track_name = track_name:gsub("[^%w_]", "") -- 只保留字母数字和下划线
        
        -- 获取父级 Folder 名称
        local parent_track = reaper.GetParentTrack(track)
        local parent_name = ""
        
        if parent_track then
            local _, p_name = reaper.GetSetMediaTrackInfo_String(parent_track, "P_NAME", "", false)
            parent_name = p_name:gsub("[^%w_]", "") .. "_" -- 加上下划线连接符
        else
            -- 如果没有父级，你可以决定是否要加前缀，或者留空
            parent_name = "" 
        end

        -- 4. 组合基础名称 (这里你可以自定义你的组合逻辑)
        -- 对应你的需求: FootSteps_(Folder) + Stone_(Track)
        local base_name = parent_name .. track_name
        
        -- 5. 处理计数逻辑 (自动加 _01, _02)
        if not name_counters[base_name] then
            name_counters[base_name] = 1
        else
            name_counters[base_name] = name_counters[base_name] + 1
        end
        
        -- 格式化编号，例如 _01, _02
        local region_name = string.format("%s_%02d", base_name, name_counters[base_name])

        -- 6. 创建 Region
        -- AddProjectMarker2(proj, isrgn, pos, rgnend, name, wantidx, color)
        reaper.AddProjectMarker2(0, true, start_pos, end_pos, region_name, -1, 0)
    end

    reaper.UpdateTimeline() -- 刷新视图
    reaper.Undo_EndBlock("Auto Create Regions from Tracks", -1)
end

CreateRegionsFromSelection()
