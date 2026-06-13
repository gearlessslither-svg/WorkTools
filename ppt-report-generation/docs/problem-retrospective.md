# Problem Retrospective

## What Went Wrong

- The early generator learned only the PPT shell, not how the source folder became the finished PPT.
- Old slide-count assumptions and hardcoded page logic did not match the real learning deck.
- Copying template slides with embedded media carried old media relationships into the new deck.
- Bulk picture deletion removed backgrounds and decorative bottom art.
- Rebuilding text boxes changed fonts, boldness, sizes, line breaks, and staff-list layout.
- Fake media cards were not acceptable when the requested result needed real PPT media objects.
- Multiple videos on one slide did not match the learned deck.
- MP3/WAV audio was mixed with MP4 video pages, which was not part of the learned format.
- PowerPoint COM was unstable on large existing decks.
- Removing media without cleaning relationships, content types, timing nodes, and old media parts caused broken or bloated packages.

## Final Decisions

- Use a previous source-folder plus finished-PPT pair as the learning target.
- Preserve the existing PPTX package and replace text/media inside it.
- Replace text inside existing XML text nodes whenever possible.
- Replace video relationship targets and poster images at the package level.
- Duplicate or remove learned media slides to match new video count.
- Keep one video per learned video slide.
- Skip audio unless a learned audio page/template exists.
- Clean old AV parts and orphan slide parts before returning the file.
- Always write a plan JSON with assignments, skipped audio, and output paths.

## Current Known Behavior

- The pair-mapped generator embeds new video files into the PPTX, so output size can be large.
- If the learning PPT contains no audio page template, audio files are listed under `skipped_audio`.
- Project-specific source text extraction still lives in `process_pair_learned_ppt.py`; generalizing to new report families requires adding or deriving a new mapping layer.

