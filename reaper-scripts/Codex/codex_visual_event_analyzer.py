from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path


def write_tsv(path: Path, rows: list[tuple[object, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write("\t".join(str(value) for value in row) + "\n")


def db_from_amp(value: float) -> float:
    if value <= 0:
        return -180.0
    return 20.0 * math.log10(value)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return -180.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * fraction))
    index = max(0, min(len(ordered) - 1, index))
    return ordered[index]


def sample_value(raw: bytes, sample_width: int) -> int:
    if sample_width == 1:
        return raw[0] - 128
    return int.from_bytes(raw, "little", signed=True)


def read_peak_frames(path: Path, startoffs: float, length: float, hop_ms: float) -> tuple[int, float, list[float]]:
    with wave.open(str(path), "rb") as handle:
        if handle.getcomptype() != "NONE":
            raise ValueError(f"Compressed WAV is not supported: {handle.getcomptype()}")

        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        total_frames = handle.getnframes()

        if channels < 1 or sample_rate <= 0 or total_frames <= 0:
            return sample_rate, 0.0, []
        if sample_width not in {1, 2, 3, 4}:
            raise ValueError(f"Unsupported WAV sample width: {sample_width}")

        start_frame = max(0, int(round(startoffs * sample_rate)))
        requested_frames = max(0, int(round(length * sample_rate)))
        end_frame = min(total_frames, start_frame + requested_frames)
        if end_frame <= start_frame:
            return sample_rate, 0.0, []

        handle.setpos(start_frame)

        hop_frames = max(1, int(round(sample_rate * hop_ms / 1000.0)))
        bytes_per_sample = sample_width
        bytes_per_frame = bytes_per_sample * channels
        full_scale = float(128 if sample_width == 1 else 1 << (sample_width * 8 - 1))

        frames_remaining = end_frame - start_frame
        chunk_frames = max(4096, hop_frames * 256)
        peaks: list[float] = []
        current_peak = 0.0
        current_frames = 0

        while frames_remaining > 0:
            read_frames = min(chunk_frames, frames_remaining)
            data = handle.readframes(read_frames)
            if not data:
                break
            frames_read = len(data) // bytes_per_frame
            frames_remaining -= frames_read

            for frame_offset in range(0, frames_read * bytes_per_frame, bytes_per_frame):
                frame_peak = 0
                for channel in range(channels):
                    sample_offset = frame_offset + channel * bytes_per_sample
                    raw = data[sample_offset : sample_offset + bytes_per_sample]
                    value = abs(sample_value(raw, sample_width))
                    if value > frame_peak:
                        frame_peak = value

                normalized = min(1.0, frame_peak / full_scale)
                if normalized > current_peak:
                    current_peak = normalized
                current_frames += 1

                if current_frames >= hop_frames:
                    peaks.append(current_peak)
                    current_peak = 0.0
                    current_frames = 0

        if current_frames:
            peaks.append(current_peak)

        duration = (end_frame - start_frame) / sample_rate
        return sample_rate, duration, peaks


def rolling_max(values: list[float], half_window: int) -> list[float]:
    if half_window <= 0 or not values:
        return list(values)
    smoothed: list[float] = []
    count = len(values)
    for index in range(count):
        first = max(0, index - half_window)
        last = min(count, index + half_window + 1)
        smoothed.append(max(values[first:last]))
    return smoothed


def detect_events(args: argparse.Namespace) -> tuple[list[tuple[float, float]], dict[str, float]]:
    sample_rate, duration, peaks = read_peak_frames(
        Path(args.input),
        args.startoffs,
        args.length,
        args.hop_ms,
    )
    if not peaks or duration <= 0:
        return [], {
            "sample_rate": float(sample_rate),
            "duration": duration,
            "max_db": -180.0,
            "floor_db_p20": -180.0,
            "visible_threshold_db": -180.0,
            "dark_gap_count": 0.0,
        }

    smooth_half_window = max(0, int(round((args.smoothing_ms / args.hop_ms) / 2.0)))
    smoothed = rolling_max(peaks, smooth_half_window)
    db_values = [db_from_amp(value) for value in smoothed]

    max_db = max(db_values)
    floor_db = percentile(db_values, 0.20)
    threshold = max(max_db - args.visible_below_peak_db, floor_db + args.floor_margin_db)

    min_dark_frames = max(1, int(round(args.min_dark_gap_ms / args.hop_ms)))
    dark_runs: list[tuple[int, int]] = []
    dark_start: int | None = None

    for index, db_value in enumerate(db_values):
        is_dark = db_value < threshold
        if is_dark and dark_start is None:
            dark_start = index
        elif not is_dark and dark_start is not None:
            if index - dark_start >= min_dark_frames:
                dark_runs.append((dark_start, index - 1))
            dark_start = None

    if dark_start is not None and len(db_values) - dark_start >= min_dark_frames:
        dark_runs.append((dark_start, len(db_values) - 1))

    raw_events: list[tuple[float, float]] = []
    event_start = 0.0
    hop_seconds = args.hop_ms / 1000.0
    for first, last in dark_runs:
        gap_start = first * hop_seconds
        gap_end = min(duration, (last + 1) * hop_seconds)
        if gap_start > event_start:
            raw_events.append((event_start, gap_start))
        event_start = gap_end
    if event_start < duration:
        raw_events.append((event_start, duration))

    min_event = args.min_event_ms / 1000.0
    pre_pad = args.pre_pad_ms / 1000.0
    tail_pad = args.tail_pad_ms / 1000.0

    events: list[tuple[float, float]] = []
    for start, finish in raw_events:
        if finish - start >= min_event:
            events.append((
                args.startoffs + max(0.0, start - pre_pad),
                args.startoffs + min(duration, finish + tail_pad),
            ))

    return events, {
        "sample_rate": float(sample_rate),
        "duration": duration,
        "max_db": max_db,
        "floor_db_p20": floor_db,
        "visible_threshold_db": threshold,
        "dark_gap_count": float(len(dark_runs)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--startoffs", type=float, required=True)
    parser.add_argument("--length", type=float, required=True)
    parser.add_argument("--hop-ms", type=float, default=5)
    parser.add_argument("--smoothing-ms", type=float, default=25)
    parser.add_argument("--visible-below-peak-db", type=float, default=54)
    parser.add_argument("--floor-margin-db", type=float, default=9)
    parser.add_argument("--min-dark-gap-ms", type=float, default=140)
    parser.add_argument("--min-event-ms", type=float, default=80)
    parser.add_argument("--pre-pad-ms", type=float, default=15)
    parser.add_argument("--tail-pad-ms", type=float, default=110)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    rows: list[tuple[object, ...]] = [
        ("STATUS", "OK"),
        ("PARAM", "hop_ms", args.hop_ms),
        ("PARAM", "smoothing_ms", args.smoothing_ms),
        ("PARAM", "visible_below_peak_db", args.visible_below_peak_db),
        ("PARAM", "floor_margin_db", args.floor_margin_db),
        ("PARAM", "min_dark_gap_ms", args.min_dark_gap_ms),
        ("PARAM", "min_event_ms", args.min_event_ms),
        ("PARAM", "pre_pad_ms", args.pre_pad_ms),
        ("PARAM", "tail_pad_ms", args.tail_pad_ms),
    ]

    try:
        events, stats = detect_events(args)
        for key, value in stats.items():
            if key == "sample_rate" or key == "dark_gap_count":
                rows.append(("STAT", key, int(value)))
            else:
                rows.append(("STAT", key, value))
        for index, (start, finish) in enumerate(events, 1):
            rows.append(("EVENT", index, f"{start:.9f}", f"{finish:.9f}", f"{finish - start:.9f}"))
    except Exception as exc:
        rows = [("STATUS", "ERROR"), ("ERROR", repr(exc))]

    write_tsv(Path(args.out), rows)


if __name__ == "__main__":
    main()
