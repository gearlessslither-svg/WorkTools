# PPTX 智能生成工具

这个工具用于把“上一期资料夹 + 上一期成品 PPT + 新资料文件夹”自动整理成可继续编辑的 PPTX。当前版本专精 PPT，不再默认生成 PDF。

正式周报优先使用“样本映射直替”模式：它会学习上一期资料如何变成上一期 PPT，再把新资料按同样规则替换进去。旧的“模板 + 资料文件夹”模式保留为草稿或临时排版入口。

## 使用方式

1. 双击根目录的 `启动PPT生成工具.bat`。
2. 在界面中选择上一期成品 `.pptx`，例如学习资料夹里的 `音频中心工作汇报 0518 - 0529.pptx`。
3. 选择要生成报告的资料文件夹。
4. 选择输出目录，默认会保存到 `PDF生成工具/生成结果`。
5. 点击 `样本映射直替PPT（推荐）`，会保留上一期 PPT 的字体、排版、底图和媒体页结构，只替换为新资料夹内容。
6. `生成同风格（高保真）PPT`、`优化/新设计` 是旧的模板生成模式，适合快速草稿，不建议用于最终周报。
7. 点击 `交给 Codex 处理`，会生成一个 Codex 任务包，并把提示词复制到剪贴板。回到 Codex 对话里粘贴即可。

## 样本映射直替规则

- 学习对象必须是“上一期资料夹里的成品 PPT”，不是空模板。
- 文本替换尽量在原文本节点内完成，避免字体、字号、加粗和换行漂移。
- 视频复用上一期的媒体页：上一期是一页一个视频，新 PPT 也保持一页一个视频。
- 视频会作为 PPT 媒体对象直接嵌入，不再生成假媒体卡。
- MP3/WAV 不会混进 MP4 视频页；如果上一期没有音频页模板，会写入计划文件的 `skipped_audio`。
- 工具会在 PPTX 包内替换媒体关系并清理旧媒体，避免误用上一期视频或音频。

## 可保留模块

- 字体
- 配色
- 底图/背景
- Logo/水印
- 母版/版式
- 页面比例
- 配图比例

## 资料支持

- 文档：`.docx`、`.txt`、`.md`、`.pdf`
- 表格：`.xlsx`、`.xlsm`
- 图片：`.png`、`.jpg`、`.jpeg`、`.bmp`、`.gif`、`.webp`
- 媒体：`.mp4`、`.mov`、`.wav`、`.mp3` 等

旧版 `.doc` 暂不直接解析，建议另存为 `.docx`。

工具会跳过 `_internal`、`.dist-info`、`.DS_Store`、Office 临时文件等依赖和缓存文件，避免把程序包内容误当作周报资料。

## 模型设置

默认使用规则引擎，不需要联网。

如果要接入模型，可在界面中选择：

- `Ollama 本地模型`：默认地址 `http://localhost:11434/api/chat`
- `OpenAI 兼容远程模型`：默认地址 `https://api.openai.com/v1/chat/completions`

模型会读取模板解析结果生成设计理解备注，也可以整理资料标题和要点；实际落版由本地代码完成，结果更稳定。

## 命令行用法

```powershell
python .\PDF生成工具\pdf_ppt_tool.py `
  --template .\AI_Test.pptx `
  --source .\AI测试 `
  --output .\PDF生成工具\生成结果 `
  --title "AI测试周报"
```

推荐的样本映射直替：

```powershell
python .\PDF生成工具\pdf_ppt_tool.py `
  --pair-map `
  --template ".\2026 0518 - 0529 -  双周报\音频中心工作汇报 0518 - 0529.pptx" `
  --learn-dir ".\2026 0518 - 0529 -  双周报" `
  --source ".\2026 0601 - 0612 -  双周报" `
  --output .\PDF生成工具\生成结果 `
  --title "音频中心工作汇报 0601 - 0612 - 样本映射媒体直替版"
```

生成优化设计版：

```powershell
python .\PDF生成工具\pdf_ppt_tool.py `
  --template .\AI_Test.pptx `
  --source .\AI测试 `
  --output .\PDF生成工具\生成结果 `
  --title "AI测试周报" `
  --optimize `
  --preserve font,color,background,logo,page_size
```

## 输出文件

每次生成会输出：

- `.pptx`：可继续编辑的演示文稿
- `_slide_plan.json`：页面计划和模板学习结果，便于排查和二次开发
- `_template_profile.json`：模板学习结果

旧模板模式默认会把音视频素材生成在展示区的可点击媒体卡上。勾选 `插入真实音视频对象` 或使用 `--embed-media` 时，工具会额外尝试调用 PowerPoint COM 插入真实媒体对象；这个模式可能更慢，也会让 PPTX 变大。正式周报请优先用 `--pair-map` 或界面的推荐按钮，它不依赖 COM，直接在 PPTX 包内替换真实媒体。

## 交给 Codex 处理

这个按钮不会让本地工具直接调用 ChatGPT/Codex 订阅，而是生成一个任务包，方便你把同一批路径、模板分析和资料摘要交给当前 Codex 会话继续处理。

任务包会保存到输出目录的 `Codex任务包` 文件夹，包含：

- `codex_task.json`
- `给Codex的提示.txt`
- `template_profile.json`
- `source_inventory.json`
- `slide_plan.json`
- `资料摘要.md`

工具还会写入 `PDF生成工具/最新Codex任务包.txt`。你可以直接对 Codex 说：“处理最新任务包”。

命令行生成任务包：

```powershell
python .\PDF生成工具\pdf_ppt_tool.py `
  --template .\AI_Test.pptx `
  --source .\AI测试 `
  --output .\PDF生成工具\生成结果 `
  --title "AI测试周报" `
  --codex-task
```

## 相似度说明

`.pptx` 模板的“同风格”模式是高保真模式，会保留模板页上的真实背景、装饰元素、文本框位置和大部分文字样式。

模板学习会读取中文字体字段、逐页文本槽位、图片框和媒体展示区；生成时会优先复用这些槽位，减少字体、格式和素材位置偏移。

更高保真的方式是“样本映射直替”：它不仅学习 PPT 长什么样，还学习旧资料文件夹里的内容如何进入旧 PPT。对于双周报这类固定结构报告，这个模式比只学模板更稳定。
