-- 测试 ImGui 是否活着
if reaper.ImGui_CreateContext then
    reaper.ShowMessageBox("成功！ReaImGui 在 Windows 上加载正常！", "恭喜", 0)
else
    reaper.ShowMessageBox("失败。Reaper 依然找不到 ImGui。", "错误", 0)
end
