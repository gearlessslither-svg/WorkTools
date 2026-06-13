---
name: ppt-pair-mapped-generation
description: Generate recurring PowerPoint reports by learning a previous source-folder plus finished-PPT pair, then mapping a new source folder into the same slide structure. Use when PPT output must preserve an existing report's fonts, layout, backgrounds, media placement, and one-media-per-slide behavior, especially weekly or biweekly report decks with images, MP4/MOV videos, MP3/WAV audio, and project subfolders.
---

# PPT Pair-Mapped Generation

## Core Rule

Use a completed example pair, not a standalone template:

- Learn input: previous source folder + the finished PPT created from it.
- Generate input: new source folder.
- Output: a new editable PPT that preserves the finished PPT's real slide XML, text styles, backgrounds, placeholders, media relationships, and page order, while replacing only the content that should change.

Do not infer the deck from a visual template alone when a previous source folder exists. The important pattern is how files became slides.

## Workflow

1. Inventory the learning source folder and the new source folder by project group, document type, image type, and media type.
2. Inspect the finished learning PPT at the package/XML level. Record slide count, slide order, text markers, media slides, background images, relationship IDs, content types, and embedded media parts.
3. Derive a source-to-slide mapping from the example pair. Prefer learned slide slots over new layout generation.
4. For text, replace text inside the existing text nodes whenever possible. Preserve paragraph runs, font family, size, boldness, alignment, and box geometry.
5. For pictures, replace only learned content picture slots. Never bulk-delete pictures on project pages because background art and decorative images may also be stored as picture shapes.
6. For video, reuse learned media slides and replace the media relationship target and poster image at the PPTX package level. Duplicate or remove learned media slides only to match the new group's video count.
7. For audio, use a learned audio slide/template only if the example PPT contains one. Do not place MP3/WAV into video pages or mix audio and MP4 on one slide.
8. Write a generation plan JSON that lists text changes, media assignments, duplicated/deleted slides, skipped audio, and validation results.
9. Validate before handing off: open/package inspect the PPTX, count visible slides and media shapes, confirm new media paths are embedded, confirm old media parts are gone, and confirm backgrounds remain.

## Hard Rules From Prior Failures

- Do not use a 22-slide or hardcoded old template assumption when the learned PPT has a different slide count.
- Do not copy old template slides with embedded media unless the old media relationships are replaced and unreferenced old media parts are removed.
- Do not create fake media cards when the user asked for directly inserted PPT media objects.
- Do not put multiple videos on one slide if the learned PPT uses one full-screen video per slide.
- Do not place MP3/WAV and MP4 together on a video slide.
- Do not recreate text boxes or flatten text runs unless unavoidable; this causes font, boldness, line break, and staff-list drift.
- Do not remove slide pictures broadly; this can delete backgrounds and bottom art.
- Avoid PowerPoint COM for large existing decks unless necessary. Package-level OOXML replacement is more stable and preserves layout better.
- If deleting/replacing media, also clean slide relationships, content type overrides, timing/media nodes when relevant, transition sounds, and old `ppt/media/media*` or `audio*` parts.

## Validation Checklist

Before final response, verify:

- The output PPTX opens without repair warnings.
- Slide count matches the learned structure plus expected media-slide duplicates/deletions.
- Fonts, title sizes, bullet spacing, and staff list layout are visually close to the learned PPT.
- Backgrounds and decorative bottom images are still present on text/project pages.
- Each MP4/MOV uses one learned media slide unless the learned PPT explicitly uses another layout.
- Audio files are either placed into learned audio pages or listed as skipped with a reason.
- No old learning-period media remains in `ppt/media`.
- The plan JSON includes source folder, learning folder, learning PPT, media count, skipped audio, and exact output path.

## Current Project Notes

For the audio center biweekly report tool in `PDF生成工具`, prefer the package-level script:

- `process_pair_package_ppt.py`: create the final PPT by copying the learned PPT package, replacing text and media relationships, duplicating/deleting media slides, and removing old media parts.
- `process_pair_learned_ppt.py`: provide project-specific source text extraction and media grouping helpers.

When modifying that tool, expose this pair-mapped flow as the recommended path. Keep old "template + source folder" generation only as a fallback or quick draft path.
