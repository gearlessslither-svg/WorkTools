# Validation Checklist

Run these checks before trusting a generated PPT.

## Package Checks

- The command exits with code `0`.
- The generated PPTX and `_package_plan.json` both exist.
- Visible slide count is expected.
- Visible video shape count equals `media_count` in the plan.
- Package AV part count equals the number of inserted videos.
- `old_av_residuals` is empty.
- No generated PPTX requires PowerPoint repair on open.

## Visual Checks

- Fonts, title sizes, bullet spacing, and staff-list layout match the learning deck.
- Backgrounds and decorative bottom images remain on text/project pages.
- Media pages retain the learned full-slide geometry.
- No page contains multiple videos unless the learning deck explicitly did so.
- MP3/WAV audio is not mixed into MP4 pages.

## Plan Checks

- `learn_dir`, `learn_ppt`, `source_dir`, and `output` are correct.
- `media_insertions` reference the new source folder, not the learning folder.
- `skipped_audio` contains only files that lack a learned audio template.
- Duplicated/deleted media-slide behavior matches the new media count.

