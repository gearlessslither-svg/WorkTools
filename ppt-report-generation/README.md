# PPT Report Generation

This category contains the PPT report-generation workflow we stabilized for recurring weekly or biweekly reports.

The main lesson is simple: do not learn only a visual PPT template. Learn a completed pair:

1. Previous source folder.
2. Previous finished PPT.
3. New source folder.

The recommended generator preserves the finished PPT package structure, fonts, backgrounds, media slide geometry, and relationship IDs, then replaces text and media with the new source-folder content.

## Contents

- `tools/`: Python GUI/CLI generator and supporting scripts.
- `skills/ppt-pair-mapped-generation/`: Codex skill that records the correct workflow and failure rules.
- `docs/problem-retrospective.md`: Problems encountered and the final decisions.
- `docs/validation-checklist.md`: Checks to run before trusting a generated deck.
- `launchers/start-ppt-tool.bat`: Windows launcher for the GUI.

## Install

```powershell
cd C:\Users\user1\WorkTools\ppt-report-generation
python -m pip install -r .\tools\requirements.txt
```

To install the skill into Codex:

```powershell
Copy-Item -Recurse -Force `
  .\skills\ppt-pair-mapped-generation `
  "$env:USERPROFILE\.codex\skills\ppt-pair-mapped-generation"
```

## Recommended CLI

```powershell
python .\tools\pdf_ppt_tool.py `
  --pair-map `
  --template "<previous-source-folder>\previous-finished-report.pptx" `
  --learn-dir "<previous-source-folder>" `
  --source "<new-source-folder>" `
  --output ".\tools\生成结果" `
  --title "new-report-name"
```

## GUI

Run:

```powershell
.\launchers\start-ppt-tool.bat
```

In the GUI, choose the previous finished PPT as `学习PPT/模板`, choose the new source folder, then click `样本映射直替PPT（推荐）`.

The old template-only buttons are kept as fallback draft modes. For final recurring reports, use the pair-mapped mode.

## Repository Policy

Do not commit generated reports, source documents, screenshots, images, audio, video, PDFs, PPTX outputs, or Codex task packages. The `.gitignore` is intentionally strict so this repository stays as a reusable tool/process library.

