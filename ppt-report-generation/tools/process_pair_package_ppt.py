from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
from PIL import Image, ImageDraw, ImageFont

import process_pair_learned_ppt as pair


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"

ET.register_namespace("p", P_NS)
ET.register_namespace("a", A_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("p14", P14_NS)

STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
BASE_NAME = f"{pair.safe_filename(pair.SOURCE_DIR.name + ' - 样本映射媒体直替版')}-{STAMP}"
PPTX_OUT = pair.OUTPUT_DIR / f"{BASE_NAME}.pptx"
PLAN_OUT = pair.OUTPUT_DIR / f"{BASE_NAME}_package_plan.json"


def qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def safe_part_name(path: Path, slide_no: int) -> str:
    suffix = path.suffix.lower() or ".bin"
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_")[:36] or "media"
    return f"ppt/media/codex_s{slide_no:02d}_{stem}{suffix}"


def poster_part_name(slide_no: int, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_")[:36] or "poster"
    return f"ppt/media/codex_s{slide_no:02d}_{stem}_poster.png"


def rel_target_from_part(part_name: str) -> str:
    return "../media/" + Path(part_name).name


def content_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".mp3": "audio/mpeg",
        ".wav": "audio/x-wav",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix, "application/octet-stream")


def read_xml(contents: dict[str, bytes], name: str) -> ET.Element:
    return ET.fromstring(contents[name])


def write_xml(contents: dict[str, bytes], name: str, root: ET.Element):
    contents[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def extract_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(qn(A_NS, "t")))


def replace_text_in_slide(contents: dict[str, bytes], slide_name: str, replacements: dict[str, str]) -> list[dict[str, str]]:
    if not replacements:
        return []
    root = read_xml(contents, slide_name)
    changed = []
    for marker, new_text in replacements.items():
        for shape in root.iter():
            if local_name(shape.tag) not in {"sp", "pic"}:
                continue
            texts = list(shape.iter(qn(A_NS, "t")))
            if not texts:
                continue
            old = "".join(node.text or "" for node in texts)
            if marker not in old:
                continue
            texts[0].text = new_text.replace("\r", "\n")
            for node in texts[1:]:
                node.text = ""
            changed.append({"slide": slide_name, "marker": marker, "text": new_text})
            break
    write_xml(contents, slide_name, root)
    return changed


def find_media_rids(contents: dict[str, bytes], slide_name: str) -> dict[str, str]:
    root = read_xml(contents, slide_name)
    for pic in root.iter(qn(P_NS, "pic")):
        video = next((node for node in pic.iter(qn(A_NS, "videoFile"))), None)
        media = next((node for node in pic.iter(qn(P14_NS, "media"))), None)
        blip = next((node for node in pic.iter(qn(A_NS, "blip"))), None)
        if video is not None or media is not None:
            return {
                "video": video.attrib.get(qn(R_NS, "link"), "") if video is not None else "",
                "media": media.attrib.get(qn(R_NS, "embed"), "") if media is not None else "",
                "poster": blip.attrib.get(qn(R_NS, "embed"), "") if blip is not None else "",
            }
    return {}


def update_media_shape_name(contents: dict[str, bytes], slide_name: str, name: str):
    root = read_xml(contents, slide_name)
    for pic in root.iter(qn(P_NS, "pic")):
        if next((node for node in pic.iter(qn(A_NS, "videoFile"))), None) is None:
            continue
        for node in pic.iter(qn(P_NS, "cNvPr")):
            node.set("name", name[:80])
            break
        break
    write_xml(contents, slide_name, root)


def rels_name_for_slide(slide_name: str) -> str:
    return "ppt/slides/_rels/" + Path(slide_name).name + ".rels"


def update_rel_target(contents: dict[str, bytes], rels_name: str, rid: str, target: str):
    root = read_xml(contents, rels_name)
    for rel in root:
        if rel.attrib.get("Id") == rid:
            rel.set("Target", target)
            break
    write_xml(contents, rels_name, root)


def rel_target(contents: dict[str, bytes], rels_name: str, rid: str) -> str | None:
    root = read_xml(contents, rels_name)
    for rel in root:
        if rel.attrib.get("Id") == rid:
            return rel.attrib.get("Target")
    return None


def absolute_part_from_rel(slide_name: str, target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("/"):
        return target.lstrip("/")
    base = Path(slide_name).parent
    return str((base / target).as_posix()).replace("ppt/slides/../", "ppt/")


def poster_png(path: Path, label: str) -> bytes:
    frame = None
    if path.suffix.lower() in pair.VIDEO_EXTS:
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            ok, encoded = cv2.imencode(".png", frame)
            if ok:
                return encoded.tobytes()

    image = Image.new("RGB", (1280, 720), (31, 41, 55))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("msyh.ttc", 36)
    except Exception:
        font = ImageFont.load_default()
    text = label[:60]
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((1280 - (bbox[2] - bbox[0])) / 2, (720 - (bbox[3] - bbox[1])) / 2), text, fill=(255, 255, 255), font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def presentation_maps(contents: dict[str, bytes]):
    pres = read_xml(contents, "ppt/presentation.xml")
    pres_rels = read_xml(contents, "ppt/_rels/presentation.xml.rels")
    rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in pres_rels}
    sld_id_lst = pres.find(qn(P_NS, "sldIdLst"))
    slides = []
    for sld in list(sld_id_lst):
        rid = sld.attrib[qn(R_NS, "id")]
        target = rid_to_target[rid]
        slides.append({"element": sld, "rid": rid, "target": "ppt/" + target})
    return pres, pres_rels, sld_id_lst, slides


def next_numeric_ids(contents: dict[str, bytes]):
    pres, pres_rels, _sld_id_lst, slides = presentation_maps(contents)
    slide_nums = []
    for name in contents:
        match = re.match(r"ppt/slides/slide(\d+)\.xml$", name)
        if match:
            slide_nums.append(int(match.group(1)))
    rel_nums = []
    for rel in pres_rels:
        match = re.match(r"rId(\d+)$", rel.attrib.get("Id", ""))
        if match:
            rel_nums.append(int(match.group(1)))
    sld_ids = [int(slide["element"].attrib["id"]) for slide in slides]
    return max(slide_nums) + 1, max(rel_nums) + 1, max(sld_ids) + 1


def duplicate_slide(contents: dict[str, bytes], source_slide_no: int, after_slide_no: int, counters: dict[str, int]) -> int:
    new_no = counters["slide_no"]
    counters["slide_no"] += 1
    new_rid = f"rId{counters['rid']}"
    counters["rid"] += 1
    new_sld_id = str(counters["sld_id"])
    counters["sld_id"] += 1

    source_name = f"ppt/slides/slide{source_slide_no}.xml"
    new_name = f"ppt/slides/slide{new_no}.xml"
    contents[new_name] = contents[source_name]

    source_rels = rels_name_for_slide(source_name)
    new_rels = rels_name_for_slide(new_name)
    if source_rels in contents:
        root = read_xml(contents, source_rels)
        for rel in list(root):
            if rel.attrib.get("Type", "").endswith("/notesSlide"):
                root.remove(rel)
        write_xml(contents, new_rels, root)

    pres, pres_rels, sld_id_lst, slides = presentation_maps(contents)
    rel = ET.Element(qn(REL_NS, "Relationship"))
    rel.set("Id", new_rid)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
    rel.set("Target", f"slides/slide{new_no}.xml")
    pres_rels.append(rel)

    sld = ET.Element(qn(P_NS, "sldId"))
    sld.set("id", new_sld_id)
    sld.set(qn(R_NS, "id"), new_rid)
    insert_at = len(sld_id_lst)
    for index, item in enumerate(slides):
        if item["target"] == f"ppt/slides/slide{after_slide_no}.xml":
            insert_at = index + 1
            break
    sld_id_lst.insert(insert_at, sld)
    write_xml(contents, "ppt/presentation.xml", pres)
    write_xml(contents, "ppt/_rels/presentation.xml.rels", pres_rels)
    add_content_override(contents, f"/ppt/slides/slide{new_no}.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
    return new_no


def remove_slide_from_order(contents: dict[str, bytes], slide_no: int):
    pres, pres_rels, sld_id_lst, slides = presentation_maps(contents)
    target = f"ppt/slides/slide{slide_no}.xml"
    remove_rid = None
    for item in slides:
        if item["target"] == target:
            sld_id_lst.remove(item["element"])
            remove_rid = item["rid"]
            break
    if remove_rid:
        for rel in list(pres_rels):
            if rel.attrib.get("Id") == remove_rid:
                pres_rels.remove(rel)
                break
    write_xml(contents, "ppt/presentation.xml", pres)
    write_xml(contents, "ppt/_rels/presentation.xml.rels", pres_rels)
    contents.pop(target, None)
    contents.pop(rels_name_for_slide(target), None)
    remove_content_override(contents, "/" + target)


def add_content_override(contents: dict[str, bytes], part_name: str, ctype: str):
    root = read_xml(contents, "[Content_Types].xml")
    for child in root:
        if child.attrib.get("PartName") == part_name:
            child.set("ContentType", ctype)
            write_xml(contents, "[Content_Types].xml", root)
            return
    node = ET.Element(qn(CT_NS, "Override"))
    node.set("PartName", part_name)
    node.set("ContentType", ctype)
    root.append(node)
    write_xml(contents, "[Content_Types].xml", root)


def remove_content_override(contents: dict[str, bytes], part_name: str):
    root = read_xml(contents, "[Content_Types].xml")
    changed = False
    for child in list(root):
        if child.attrib.get("PartName") == part_name:
            root.remove(child)
            changed = True
    if changed:
        write_xml(contents, "[Content_Types].xml", root)


def apply_media_assignment(contents: dict[str, bytes], slide_no: int, media_path: Path, title: str, replaced_parts: set[str]) -> dict:
    slide_name = f"ppt/slides/slide{slide_no}.xml"
    rels_name = rels_name_for_slide(slide_name)
    rids = find_media_rids(contents, slide_name)
    if not rids:
        raise RuntimeError(f"未找到媒体形状：slide {slide_no}")

    old_media_targets = []
    for rid in [rids.get("video"), rids.get("media")]:
        old_part = absolute_part_from_rel(slide_name, rel_target(contents, rels_name, rid))
        if old_part:
            old_media_targets.append(old_part)
            replaced_parts.add(old_part)
    old_poster = absolute_part_from_rel(slide_name, rel_target(contents, rels_name, rids.get("poster")))
    if old_poster:
        replaced_parts.add(old_poster)

    media_part = safe_part_name(media_path, slide_no)
    poster_part = poster_part_name(slide_no, media_path)
    contents[media_part] = media_path.read_bytes()
    contents[poster_part] = poster_png(media_path, media_path.stem)
    add_content_override(contents, "/" + media_part, content_type(media_part))
    add_content_override(contents, "/" + poster_part, "image/png")

    for rid in [rids.get("video"), rids.get("media")]:
        if rid:
            update_rel_target(contents, rels_name, rid, rel_target_from_part(media_part))
    if rids.get("poster"):
        update_rel_target(contents, rels_name, rids["poster"], rel_target_from_part(poster_part))
    replace_text_in_slide(contents, slide_name, {"产出展示": title, "音频展示": title})
    update_media_shape_name(contents, slide_name, media_path.stem)
    return {
        "slide": slide_no,
        "path": str(media_path),
        "name": media_path.name,
        "title": title,
        "media_part": media_part,
        "poster_part": poster_part,
        "old_parts": old_media_targets,
    }


def build_assignments(contents: dict[str, bytes]) -> tuple[list[tuple[int, Path, str]], list[str]]:
    groups = pair.media_groups()
    counters_tuple = next_numeric_ids(contents)
    counters = {"slide_no": counters_tuple[0], "rid": counters_tuple[1], "sld_id": counters_tuple[2]}

    assignments: list[tuple[int, Path, str]] = []
    skipped_audio: list[str] = []

    def add_group(base_slides: list[int], paths: list[Path], title_prefix: str):
        usable_paths = []
        for path in paths:
            if pair.media_kind(path) == "audio":
                skipped_audio.append(str(path))
            else:
                usable_paths.append(path)
        paths = usable_paths
        if len(paths) < len(base_slides):
            for slide_no in reversed(base_slides[len(paths) :]):
                remove_slide_from_order(contents, slide_no)
        slides = base_slides[: len(paths)]
        after = slides[-1] if slides else base_slides[-1]
        source = base_slides[-1]
        while len(slides) < len(paths):
            new_slide = duplicate_slide(contents, source, after, counters)
            slides.append(new_slide)
            after = new_slide
        for slide_no, path in zip(slides, paths):
            label = "音频展示" if pair.media_kind(path) == "audio" else "产出展示"
            assignments.append((slide_no, path, f"{title_prefix} {label}：{path.stem}"))

    add_group([6, 7], groups["L"], "重点工作1——L项目")
    add_group([11, 12, 13, 14], groups["LL"], "重点工作2——LL项目")
    add_group([17], groups["U"], "重点工作3——U项目")
    add_group([21], groups["FM"], "重点工作4——FM项目")
    add_group([25, 26], groups["EF"], "重点工作5——EF项目")
    add_group([28, 29], groups["TA"], "TA")
    return assignments, skipped_audio


def remove_replaced_old_parts(contents: dict[str, bytes], replaced_parts: set[str]):
    for part in list(replaced_parts):
        contents.pop(part, None)
        remove_content_override(contents, "/" + part)
    # Drop any unreferenced old template audio/video parts as well.
    for name in list(contents):
        if name.startswith("ppt/media/") and Path(name).suffix.lower() in pair.MEDIA_EXTS and Path(name).stem.startswith(("media", "audio")):
            contents.pop(name, None)
            remove_content_override(contents, "/" + name)


def apply_text_replacements(contents: dict[str, bytes]) -> list[dict[str, str]]:
    changes = []
    for slide_index, replacements in pair.source_text_plan().items():
        slide_name = f"ppt/slides/slide{slide_index}.xml"
        if slide_name in contents:
            changes.extend(replace_text_in_slide(contents, slide_name, replacements))
    return changes


def build_package() -> dict:
    if not pair.LEARN_DIR.exists():
        raise FileNotFoundError(f"学习资料夹不存在：{pair.LEARN_DIR}")
    if not pair.SOURCE_DIR.exists():
        raise FileNotFoundError(f"新资料夹不存在：{pair.SOURCE_DIR}")
    if not pair.LEARN_PPT.exists():
        raise FileNotFoundError(f"学习PPT不存在：{pair.LEARN_PPT}")

    with ZipFile(pair.LEARN_PPT, "r") as source_zip:
        contents = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}

    text_changes = apply_text_replacements(contents)
    assignments, skipped_audio = build_assignments(contents)
    replaced_parts: set[str] = set()
    media_insertions = []
    for slide_no, media_path, title in assignments:
        media_insertions.append(apply_media_assignment(contents, slide_no, media_path, title, replaced_parts))
    remove_replaced_old_parts(contents, replaced_parts)

    with ZipFile(PPTX_OUT, "w", ZIP_DEFLATED) as output_zip:
        for name, data in contents.items():
            output_zip.writestr(name, data)

    plan = {
        "strategy": "direct_pptx_package_pair_mapping",
        "learn_ppt": str(pair.LEARN_PPT),
        "learn_dir": str(pair.LEARN_DIR),
        "source_dir": str(pair.SOURCE_DIR),
        "output": str(PPTX_OUT),
        "media_groups": {key: [str(path) for path in value] for key, value in pair.media_groups().items()},
        "text_changes": text_changes,
        "media_insertions": media_insertions,
        "media_count": len(media_insertions),
        "skipped_audio": skipped_audio,
    }
    PLAN_OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def configure_run(args: argparse.Namespace) -> None:
    global STAMP, BASE_NAME, PPTX_OUT, PLAN_OUT

    pair.configure_paths(
        root=args.root,
        learn_dir=args.learn_dir,
        source_dir=args.source_dir,
        learn_ppt=args.learn_ppt,
        output_dir=args.output,
        title=args.title,
    )
    STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    title = args.title or f"{pair.SOURCE_DIR.name} - 样本映射媒体直替版"
    BASE_NAME = f"{pair.safe_filename(title)}-{STAMP}"
    PPTX_OUT = pair.OUTPUT_DIR / f"{BASE_NAME}.pptx"
    PLAN_OUT = pair.OUTPUT_DIR / f"{BASE_NAME}_package_plan.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按旧资料夹+旧成品PPT样本映射生成新PPT")
    parser.add_argument("--root", default="", help="项目根目录；相对路径会基于当前目录解析")
    parser.add_argument("--learn-dir", default="", help="学习资料夹，包含旧资料和旧成品PPT")
    parser.add_argument("--learn-ppt", default="", help="学习用成品PPT；不填则自动在学习资料夹内寻找")
    parser.add_argument("--source-dir", default="", help="要生成的新资料夹")
    parser.add_argument("--output", default="", help="输出目录")
    parser.add_argument("--title", default="", help="输出文件名前缀")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_run(args)
    plan = build_package()
    print(f"PPTX={PPTX_OUT}")
    print(f"PLAN={PLAN_OUT}")
    print(f"MEDIA_INSERTIONS={plan['media_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
