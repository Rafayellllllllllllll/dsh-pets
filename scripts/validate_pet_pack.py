#!/usr/bin/env python3
"""Check a DSH Go pet pack against the public rule set.

This is a local parser. It loads the published test vectors in
scripts/testdata/pet-pack-local-v1.json and then, when given a pack,
checks that pack the same way. Passing here is not an import test
inside the DSH Go app.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zipfile
import zlib
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "testdata" / "pet-pack-local-v1.json"
APP_MAX = 1073741824
KNOWN_REACTIONS = ("tapLeft", "tapRight", "tickle", "drag")
ROAM_FACES = {"left", "right", "front"}


def round_away(value: float) -> int:
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def is_bool(value: object) -> bool:
    return isinstance(value, bool)


def is_finite_number(value: object) -> bool:
    return (
        not is_bool(value)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def is_whole(value: object, low: int, high: int) -> bool:
    if not is_finite_number(value):
        return False
    number = float(value)
    if not number.is_integer():
        return False
    whole = int(number)
    return low <= whole <= high


def f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


def trunc_box(box_edge: object, scale: object) -> int | None:
    if not is_finite_number(box_edge) or not is_finite_number(scale):
        return None
    return int(math.trunc(f32(f32(float(box_edge)) * f32(float(scale)))))


def load_json(path: Path) -> tuple[object | None, list[str]]:
    raw = path.read_bytes()
    errors: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("json-bom")
        return None, errors
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        errors.append("json-utf8")
        return None, errors

    def pairs(items: list[tuple[str, object]]) -> dict:
        keys = [key for key, _ in items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate-key")
        return dict(items)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _k: math.nan), errors
    except ValueError as exc:
        errors.append("json-duplicate-key" if "duplicate" in str(exc) else "json-parse")
        return None, errors


def _rect_ok(rect: object, *, whole: bool) -> bool:
    if not isinstance(rect, dict):
        return False
    if any(key not in rect for key in ("x", "y", "w", "h")):
        return False
    for key in ("x", "y", "w", "h"):
        if not is_finite_number(rect[key]):
            return False
        if whole and not is_whole(rect[key], -APP_MAX, APP_MAX):
            return False
    width = round_away(float(rect["w"])) if not whole else int(float(rect["w"]))
    height = round_away(float(rect["h"])) if not whole else int(float(rect["h"]))
    return width > 0 and height > 0


def _kept_frames(frames: object) -> list[tuple[int, int]] | None:
    if not isinstance(frames, list):
        return None
    kept: list[tuple[int, int]] = []
    for item in frames:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        duration = item.get("ms")
        if not is_finite_number(index) or not is_finite_number(duration):
            continue
        kept.append((round_away(float(index)), round_away(float(duration))))
    return kept


def _anim_names(animations: object) -> set[str]:
    if not isinstance(animations, dict):
        return set()
    return {name for name in animations if isinstance(name, str)}


def _refs_ok(names: set[str], value: object) -> bool:
    if isinstance(value, str):
        return value in names
    return False


def parser_errors(pack: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["pack-type"]
    if pack.get("schema") != 1:
        errors.append("schema")
    if not isinstance(pack.get("id"), str) or not pack.get("id"):
        errors.append("id")
    canvas = pack.get("canvas")
    if not _rect_ok({"x": 0, "y": 0, **canvas} if isinstance(canvas, dict) else None, whole=False):
        # canvas is {w,h}; reuse positivity checks via a synthetic rect
        if (
            not isinstance(canvas, dict)
            or not is_finite_number(canvas.get("w"))
            or not is_finite_number(canvas.get("h"))
            or round_away(float(canvas["w"])) < 1
            or round_away(float(canvas["h"])) < 1
        ):
            errors.append("canvas")
    if not _rect_ok(pack.get("contentBox"), whole=False):
        errors.append("contentBox")
    animations = pack.get("animations")
    names = _anim_names(animations)
    if not isinstance(animations, dict) or not animations:
        errors.append("animations")
    else:
        for name, anim in animations.items():
            if not isinstance(anim, dict):
                errors.append(f"animation {name}")
                continue
            if not isinstance(anim.get("sheet"), str) or not anim["sheet"]:
                errors.append(f"sheet {name}")
            for key in ("frameW", "frameH", "cols"):
                if not is_finite_number(anim.get(key)) or round_away(float(anim[key])) < 1:
                    errors.append(f"{key} {name}")
            if not _rect_ok(anim.get("box"), whole=False):
                errors.append(f"box {name}")
            kept = _kept_frames(anim.get("frames"))
            if not kept:
                errors.append(f"frames {name}")
                continue
            for index, duration in kept:
                if index < 0 or duration <= 0:
                    errors.append(f"frames {name}")
                    break
    states = pack.get("states")
    if not isinstance(states, dict):
        errors.append("states")
    else:
        idle = states.get("idle")
        if not isinstance(idle, list) or not idle:
            errors.append("states.idle")
        for _state, listed in states.items():
            if not isinstance(listed, list) or not listed:
                continue
            if any(not _refs_ok(names, item) for item in listed):
                errors.append("state-reference")
                break
    _check_optional_refs(pack, names, errors)
    return errors


def _check_optional_refs(pack: dict, names: set[str], errors: list[str]) -> None:
    reactions = pack.get("reactions")
    if isinstance(reactions, dict):
        if any(not _refs_ok(names, target) for target in reactions.values()):
            errors.append("reaction-reference")
    roam = pack.get("roam")
    if isinstance(roam, dict) and "anim" in roam and not _refs_ok(names, roam.get("anim")):
        errors.append("roam-reference")
    idle_pool = pack.get("idlePool")
    pool_items: list[object] = []
    if isinstance(idle_pool, list):
        pool_items = idle_pool
    elif isinstance(idle_pool, dict) and isinstance(idle_pool.get("anims"), list):
        pool_items = idle_pool["anims"]
    if any(not isinstance(item, dict) or not _refs_ok(names, item.get("anim")) for item in pool_items):
        errors.append("idle-pool-reference")
    tiers = pack.get("tiers")
    if isinstance(tiers, dict):
        for listed in tiers.values():
            if not isinstance(listed, list):
                continue
            if any(not isinstance(item, dict) or not _refs_ok(names, item.get("anim")) for item in listed):
                errors.append("tier-reference")
                break
    bonus = pack.get("bonus")
    if isinstance(bonus, dict):
        for entry in bonus.values():
            if not isinstance(entry, dict) or not isinstance(entry.get("anims"), list):
                continue
            if any(not _refs_ok(names, item) for item in entry["anims"]):
                errors.append("bonus-reference")
                break


def app_errors(pack: object) -> list[str]:
    errors = parser_errors(pack)
    if not isinstance(pack, dict):
        return errors
    scale = pack.get("scale", 1)
    if "scale" in pack:
        if not is_whole(scale, 1, 4096):
            errors.append("app-scale")
    elif not is_whole(scale, 1, 4096):
        errors.append("app-scale")
    canvas = pack.get("canvas")
    if not isinstance(canvas, dict) or not is_whole(canvas.get("w"), 1, APP_MAX) or not is_whole(
        canvas.get("h"), 1, APP_MAX
    ):
        errors.append("app-canvas")
    animations = pack.get("animations")
    if isinstance(animations, dict):
        if "idle" not in animations:
            errors.append("app-idle")
        for name, anim in animations.items():
            if not isinstance(anim, dict):
                continue
            for key in ("frameW", "frameH", "cols"):
                if not is_whole(anim.get(key), 1, 4096):
                    errors.append(f"app-{key} {name}")
            box = anim.get("box")
            if isinstance(box, dict) and is_whole(scale, 1, 4096):
                for edge, frame_key in (("w", "frameW"), ("h", "frameH")):
                    expected = trunc_box(box.get(edge), scale)
                    actual = anim.get(frame_key)
                    if expected is None or not is_whole(actual, 1, 4096) or int(float(actual)) != expected:
                        errors.append(f"app-geometry {name}")
                        break
            frames = anim.get("frames")
            if not isinstance(frames, list) or not frames:
                errors.append(f"app-frames {name}")
                continue
            for item in frames:
                if (
                    not isinstance(item, dict)
                    or not is_whole(item.get("i"), 0, APP_MAX)
                    or not is_whole(item.get("ms"), 1, APP_MAX)
                ):
                    errors.append(f"app-frames {name}")
                    break
    return errors


def check_fixture() -> list[str]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if payload.get("rulesRevision") != 3:
        return ["fixture revision is not 3"]
    mismatches: list[str] = []
    for case in payload["cases"]:
        pack = case["pack"]
        parser_ok = not parser_errors(pack)
        app_ok = not app_errors(pack)
        if parser_ok != bool(case["parserValid"]) or app_ok != bool(case["appImportValid"]):
            mismatches.append(
                f"{case['name']}: parser {parser_ok} expected {case['parserValid']}, "
                f"app {app_ok} expected {case['appImportValid']}; parser_errors={parser_errors(pack)}"
            )
    return mismatches


def _png_errors(data: bytes) -> list[str]:
    errors: list[str] = []
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return ["png-signature"]
    pos = len(signature)
    chunks: list[tuple[bytes, bytes]] = []
    idat_started = False
    idat_done = False
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        end = pos + 12 + length
        if end > len(data):
            return ["png-truncated"]
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length : end])[0]
        if (zlib.crc32(ctype + chunk) & 0xFFFFFFFF) != crc:
            errors.append(f"png-crc {ctype.decode('ascii', 'replace')}")
        if ctype in (b"acTL", b"fcTL", b"fdAT"):
            errors.append("png-apng")
        if ctype == b"IDAT":
            if idat_done:
                errors.append("png-idat-gap")
            idat_started = True
        elif idat_started:
            idat_done = True
        chunks.append((ctype, chunk))
        pos = end
        if ctype == b"IEND":
            break
    else:
        errors.append("png-iend")
    if pos != len(data):
        errors.append("png-trailing")
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        errors.append("png-order")
        return errors
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        return errors + ["png-ihdr"]
    width, height, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8 or color != 6 or comp != 0 or filt != 0 or interlace != 0:
        errors.append(f"png-ihdr {width}x{height} depth {depth} color {color} interlace {interlace}")
    if width > 4096 or height > 4096 or width < 1 or height < 1:
        errors.append("png-side")
    if width * height * 4 > 16 * 1024 * 1024:
        errors.append("png-decode-buffer")
    idat = b"".join(chunk for ctype, chunk in chunks if ctype == b"IDAT")
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(idat)
        raw += decompressor.flush()
        if decompressor.unused_data:
            errors.append("png-zlib-trailing")
    except zlib.error:
        errors.append("png-zlib")
        return errors
    bpp = 4
    stride = width * bpp
    expected = height * (stride + 1)
    if len(raw) != expected:
        errors.append(f"png-scanlines {len(raw)} != {expected}")
        return errors
    row = bytearray(stride)
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        filter_type = raw[base]
        scan = raw[base + 1 : base + 1 + stride]
        if filter_type > 4:
            errors.append("png-filter")
            return errors
        for x, value in enumerate(scan):
            left = row[x - bpp] if x >= bpp else 0
            up = prev[x]
            up_left = prev[x - bpp] if x >= bpp else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = (value + left) & 255
            elif filter_type == 2:
                decoded = (value + up) & 255
            elif filter_type == 3:
                decoded = (value + ((left + up) // 2)) & 255
            else:
                estimate = left + up - up_left
                choices = (left, up, up_left)
                decoded = (value + min(choices, key=lambda item: abs(estimate - item))) & 255
            row[x] = decoded
        prev = bytearray(row)
    return errors


def _sheet_name_ok(name: str) -> bool:
    if not name.endswith(".png") or "/" in name or "\\" in name or "\x00" in name:
        return False
    if name.startswith(".") or len(name.encode("utf-16-le")) // 2 > 120:
        return False
    return True


def check_pack(pack_dir: Path, zip_path: Path, display_name: str | None) -> list[str]:
    errors: list[str] = []
    pack_path = pack_dir / "pack.json"
    loaded, json_errors = load_json(pack_path)
    errors.extend(json_errors)
    if not isinstance(loaded, dict):
        return errors + ["pack.json"]
    for problem in app_errors(loaded):
        errors.append(problem)
    if "license" in loaded or "licence" in loaded:
        errors.append("license field is set")
    if display_name is not None and loaded.get("name") != display_name:
        errors.append(f"name is {loaded.get('name')!r}, expected {display_name!r}")
    animations = loaded.get("animations")
    if not isinstance(animations, dict):
        return errors
    sheets: dict[str, int] = {}
    for name, anim in animations.items():
        if not isinstance(anim, dict):
            continue
        sheet_name = anim.get("sheet")
        if not isinstance(sheet_name, str) or not _sheet_name_ok(sheet_name):
            errors.append(f"sheet-name {name}")
            continue
        sheet_path = pack_dir / sheet_name
        if not sheet_path.is_file():
            errors.append(f"sheet-missing {sheet_name}")
            continue
        data = sheet_path.read_bytes()
        if len(data) > 8 * 1024 * 1024:
            errors.append(f"sheet-size {sheet_name}")
        sheets[sheet_name] = len(data)
        errors.extend(f"{sheet_name}: {item}" for item in _png_errors(data))
        width, height = struct.unpack(">II", data[16:24]) if data.startswith(b"\x89PNG") and len(data) >= 24 else (0, 0)
        frames = anim.get("frames") if isinstance(anim.get("frames"), list) else []
        indexes = [int(item["i"]) for item in frames if isinstance(item, dict) and is_whole(item.get("i"), 0, APP_MAX)]
        if indexes and is_whole(anim.get("frameW"), 1, 4096) and is_whole(anim.get("frameH"), 1, 4096) and is_whole(anim.get("cols"), 1, 4096):
            need_w = int(anim["cols"]) * int(anim["frameW"])
            need_h = (max(indexes) // int(anim["cols"]) + 1) * int(anim["frameH"])
            if width < need_w or height < need_h:
                errors.append(f"sheet-frame-bounds {name}")
    if len(sheets) > 64:
        errors.append("sheet-count")
    if sum(sheets.values()) > 48 * 1024 * 1024:
        errors.append("sheet-total-size")
    if pack_path.stat().st_size > 256 * 1024:
        errors.append("pack-size")
    reactions = loaded.get("reactions")
    if not isinstance(reactions, dict) or any(key not in reactions for key in KNOWN_REACTIONS):
        errors.append("reactions")
    roam = loaded.get("roam")
    if not isinstance(roam, dict) or roam.get("faces") not in ROAM_FACES or not isinstance(roam.get("anim"), str):
        errors.append("roam")
    idle_pool = loaded.get("idlePool")
    if not isinstance(idle_pool, dict) or not isinstance(idle_pool.get("anims"), list):
        errors.append("idlePool")
    else:
        every = idle_pool.get("everyMs")
        if not isinstance(every, dict) or any(not is_whole(every.get(key), 1, APP_MAX) for key in ("mean", "min", "max")):
            errors.append("idlePool.everyMs")
        elif not (int(every["min"]) <= int(every["mean"]) <= int(every["max"])):
            errors.append("idlePool.everyMs order")
    errors.extend(_zip_errors(zip_path, loaded, pack_path.read_bytes()))
    return errors


def _zip_errors(zip_path: Path, pack: dict, pack_bytes: bytes) -> list[str]:
    errors: list[str] = []
    if not zip_path.is_file():
        return ["zip-missing"]
    if zip_path.stat().st_size > 64 * 1024 * 1024:
        errors.append("zip-size")
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        return errors + ["zip-invalid"]
    with archive:
        infos = archive.infolist()
        if len(infos) > 256:
            errors.append("zip-entries")
        expanded = 0
        names: list[str] = []
        for info in infos:
            expanded += info.file_size
            names.append(info.filename)
            if info.flag_bits & 0x1:
                errors.append(f"zip-encrypted {info.filename}")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                errors.append(f"zip-method {info.filename}")
            if info.is_dir():
                continue
            path = Path(info.filename)
            if (
                info.filename.startswith("/")
                or "\\" in info.filename
                or ":" in info.filename
                or ".." in path.parts
                or path.parts in {(".",), ()}
            ):
                errors.append(f"zip-path {info.filename}")
        if expanded > 64 * 1024 * 1024:
            errors.append("zip-expanded")
        packs = [name for name in names if Path(name).name == "pack.json"]
        if len(packs) != 1 or len(Path(packs[0]).parts) > 2:
            errors.append(f"zip-pack-count {packs}")
            return errors
        prefix = "" if len(Path(packs[0]).parts) == 1 else str(Path(packs[0]).parent)
        if archive.read(packs[0]) != pack_bytes:
            errors.append("zip-pack-json-mismatch")
        wanted = {anim["sheet"] for anim in pack["animations"].values()}
        for sheet in sorted(wanted):
            member = f"{prefix}/{sheet}" if prefix else sheet
            if member not in names:
                errors.append(f"zip-missing {member}")
        allowed = {packs[0], *([f"{prefix}/{sheet}" if prefix else sheet for sheet in wanted])}
        extras = [name for name in names if name not in allowed and not name.endswith("/")]
        if extras:
            errors.append(f"zip-extra {extras}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="validate a local DSH Go pet pack")
    parser.add_argument("pack_dir", nargs="?", type=Path)
    parser.add_argument("zip_path", nargs="?", type=Path)
    parser.add_argument("--display-name")
    args = parser.parse_args()

    mismatches = check_fixture()
    if mismatches:
        print("FIXTURE FAIL")
        for line in mismatches:
            print("  " + line)
        return 1
    print(f"OK   public fixture rulesRevision 3 ({len(json.loads(FIXTURE.read_text())['cases'])} cases)")

    if args.pack_dir is None:
        print("local format validation passed for the fixture only (not an App import test)")
        return 0
    if args.zip_path is None:
        print("zip path is required when a pack directory is given", file=sys.stderr)
        return 2
    problems = check_pack(args.pack_dir, args.zip_path, args.display_name)
    if problems:
        print("PACK FAIL")
        for line in problems:
            print("  " + line)
        return 1
    print(f"OK   {args.pack_dir} and {args.zip_path}")
    print("local format validation passed (not an App import test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
