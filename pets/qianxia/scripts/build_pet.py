#!/usr/bin/env python3
"""Build the 千夏像素宠物 pack from the archived 4x4 sheet.

Rebuild from the pet directory:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python scripts/build_pet.py

The script writes files only. It does not upload the pack.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PET = Path(__file__).resolve().parents[1]
REPO = PET.parents[1]
SOURCE = PET / "source" / "qianxia-4x4.jpg"
PACK_DIR = PET / "pack"
PREVIEW_DIR = PET / "preview"
PACKAGE_DIR = PET / "packages"
ZIP_PATH = PACKAGE_DIR / "qianxia-pixel-1.zip"
DEBUG_DIR = PET / "work" / "debug"
DISPLAY_NAME = "千夏像素宠物"

GRID = 4
CELL = 256
SCALE = 4  # mode-downsample factor for cleaner pixel edges
PAD_TOP = 8  # full-res padding above the tallest standing frame
PAD_BOTTOM = 8
PAD_LEFT = 8
PAD_RIGHT = 24  # extra room for the humming eighth-note on the right
ALPHA_COVER = 0.40  # min opaque fraction in a SCALE x SCALE block

# Frame index map, row-major: i = row * 4 + col
IDLE = (0, 1, 2, 3)
THINKING = (4, 5)  # writing / looking at the notebook
WORKING = (6, 7)  # writing toward camera, then humming with a note
NOTIFICATION = (8, 9)  # raised hand
ERROR = (10, 11)  # shy / looking down / closed eyes
DROWSY_STAND = 12  # standing, eyes closed (falling asleep / just up)
SLEEP = (13, 14, 15)  # low lying poses; keep these on the shared baseline
STANDING = tuple(i for i in range(16) if i not in SLEEP)

PREVIEW_BG = (54, 56, 62)


def log(msg: str) -> None:
    print(msg, flush=True)


def rgb_to_hsv(arr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)


def estimate_background(arr: np.ndarray) -> np.ndarray:
    border = np.concatenate(
        [
            arr[:10].reshape(-1, 3),
            arr[-10:].reshape(-1, 3),
            arr[:, :10].reshape(-1, 3),
            arr[:, -10:].reshape(-1, 3),
        ]
    )
    return np.median(border, axis=0).astype(np.float32)


def background_mask(arr: np.ndarray, bg_ref: np.ndarray) -> np.ndarray:
    """Edge-flooded background. Keeps inner-ear pink and clothing red."""
    hsv = rgb_to_hsv(arr)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # OpenCV hue 0-180; source bg is ~#EE006F, hue ~332° → 166.
    magenta_h = (h >= 156) & (h <= 178)
    high_s = s >= 165
    # Light inner ear / pale skin-pink: higher G, lower saturation than the key.
    pale_pink = (g.astype(np.int16) >= 90) & (s <= 155) & (v >= 190)
    # True red (necktie / clothes): hue near 0, blue much lower than red.
    true_red = ((h <= 12) | (h >= 170)) & (b.astype(np.int16) * 2 < r.astype(np.int16)) & (g < 100)
    # Clothing red is hue ~0-10, not 170. Keep the conservative exclusion anyway.

    dist = np.sqrt(((arr.astype(np.float32) - bg_ref) ** 2).sum(-1))
    candidate = (magenta_h & high_s & ~pale_pink) | ((dist < 30) & ~pale_pink & ~true_red)

    hgt, wdt = candidate.shape
    edge = np.zeros((hgt, wdt), dtype=bool)
    edge[0, :] = True
    edge[-1, :] = True
    edge[:, 0] = True
    edge[:, -1] = True
    _, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=8)
    seeds = labels[edge & candidate]
    touching = np.unique(seeds)
    return np.isin(labels, touching)


def _magenta_hue(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hsv = rgb_to_hsv(rgb)
    return hsv[:, :, 0], hsv[:, :, 1]


def is_key_fringe(rgb: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """Hot-pink pixels left by the magenta backdrop. Cheek blush and the red tie sit at a low hue."""
    hue, sat = _magenta_hue(rgb)
    return opaque & (hue >= 145) & (hue <= 179) & (sat >= 50)


def clean_magenta_fringe(rgba: np.ndarray, passes: int = 3) -> np.ndarray:
    """Recolor backdrop-tinted edge pixels from nearby fur. Do not delete the silhouette."""
    rgba = rgba.copy()
    height, width = rgba.shape[:2]
    for _ in range(passes):
        opaque = rgba[:, :, 3] >= 128
        if not opaque.any():
            return rgba
        dist = cv2.distanceTransform(opaque.astype(np.uint8), cv2.DIST_L2, 3)
        fringe = is_key_fringe(rgba[:, :, :3], opaque) & (dist <= 2.6)
        if not fringe.any():
            break
        hue, sat = _magenta_hue(rgba[:, :, :3])
        clean = opaque & ~((hue >= 145) & (hue <= 179) & (sat >= 50))
        updated = rgba.copy()
        ys, xs = np.where(fringe)
        for y, x in zip(ys, xs):
            y0, y1 = max(0, y - 2), min(height, y + 3)
            x0, x1 = max(0, x - 2), min(width, x + 3)
            window = clean[y0:y1, x0:x1]
            if not window.any():
                continue
            updated[y, x, :3] = np.median(rgba[y0:y1, x0:x1][:, :, :3][window], axis=0)
        rgba = updated
    return rgba


def fill_small_holes(rgba: np.ndarray, max_area: int = 4) -> np.ndarray:
    """Fill enclosed gaps of a few pixels. Open background stays transparent."""
    rgba = rgba.copy()
    opaque = rgba[:, :, 3] >= 128
    inverse = (~opaque).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(inverse, connectivity=4)
    if count <= 1:
        return rgba
    border = np.zeros(count, dtype=bool)
    border[labels[0, :]] = True
    border[labels[-1, :]] = True
    border[labels[:, 0]] = True
    border[labels[:, -1]] = True
    height, width = opaque.shape
    for index in range(1, count):
        if border[index] or stats[index, cv2.CC_STAT_AREA] > max_area:
            continue
        ys, xs = np.where(labels == index)
        samples = []
        for y, x in zip(ys, xs):
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < height and 0 <= xx < width and rgba[yy, xx, 3] >= 128:
                    samples.append(rgba[yy, xx, :3])
        if not samples:
            continue
        color = np.median(np.stack(samples), axis=0).astype(np.uint8)
        rgba[ys, xs, :3] = color
        rgba[ys, xs, 3] = 255
    return rgba


def despill_edges(rgb: np.ndarray, opaque: np.ndarray, bg_ref: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop saturated backdrop pixels on the cut, then remove isolated specks."""
    del bg_ref
    rgb = rgb.copy()
    opaque = opaque.copy()
    hue, sat = _magenta_hue(rgb)
    kernel = np.ones((3, 3), np.uint8)
    near_clear = cv2.dilate((~opaque).astype(np.uint8), kernel, iterations=1).astype(bool) & opaque
    fringe = near_clear & (hue >= 156) & (hue <= 178) & (sat >= 150)
    opaque[fringe] = False
    count, labels, stats, _ = cv2.connectedComponentsWithStats(opaque.astype(np.uint8), connectivity=8)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 12:
            opaque[labels == index] = False
    return rgb, opaque


def extract_cells(sheet: np.ndarray, bg: np.ndarray) -> list[np.ndarray]:
    """Return 16 RGBA uint8 images, 256x256, with cleaned alpha."""
    rgb = sheet.copy()
    opaque = ~bg
    rgb, opaque = despill_edges(rgb, opaque, estimate_background(sheet))
    rgba = np.dstack([rgb, np.where(opaque, 255, 0).astype(np.uint8)])
    cells = []
    for i in range(16):
        r, c = divmod(i, GRID)
        cells.append(rgba[r * CELL : (r + 1) * CELL, c * CELL : (c + 1) * CELL].copy())
    return cells


def largest_component(opaque: np.ndarray) -> np.ndarray:
    num, labels, stats, _ = cv2.connectedComponentsWithStats(opaque.astype(np.uint8), connectivity=8)
    if num <= 1:
        return opaque
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + int(np.argmax(areas))
    return labels == keep


def frame_metrics(rgba: np.ndarray) -> dict:
    opaque = rgba[:, :, 3] >= 128
    if not opaque.any():
        raise ValueError("empty frame after keying")
    body = largest_component(opaque)
    ys, xs = np.where(opaque)
    bys, bxs = np.where(body)
    return {
        "x0": int(xs.min()),
        "y0": int(ys.min()),
        "x1": int(xs.max()),
        "y1": int(ys.max()),
        "body_x0": int(bxs.min()),
        "body_y0": int(bys.min()),
        "body_x1": int(bxs.max()),
        "body_y1": int(bys.max()),
        "body_cx": float(bxs.mean()),
        "baseline": int(bys.max()),
        "body": body,
        "opaque": opaque,
    }


def mode_downsample(rgba: np.ndarray, factor: int = SCALE, cover: float = ALPHA_COVER) -> np.ndarray:
    h, w, _ = rgba.shape
    h2, w2 = h // factor, w // factor
    rgba = rgba[: h2 * factor, : w2 * factor]
    out = np.zeros((h2, w2, 4), np.uint8)
    for y in range(h2):
        for x in range(w2):
            block = rgba[y * factor : (y + 1) * factor, x * factor : (x + 1) * factor]
            opa = block[:, :, 3] >= 128
            if opa.mean() < cover:
                continue
            cols = block[opa][:, :3]
            q = (cols // 12) * 12
            uniq, counts = np.unique(q, axis=0, return_counts=True)
            best = uniq[counts.argmax()]
            sel = np.all(q == best, axis=1)
            out[y, x, :3] = np.median(cols[sel], axis=0).astype(np.uint8)
            out[y, x, 3] = 255
    return out


def shared_palette(frames: list[np.ndarray], colors: int = 32) -> list[np.ndarray]:
    """Quantize all frames against one palette. Backdrop pink is not a palette color."""
    samples = []
    for fr in frames:
        opaque = fr[:, :, 3] >= 128
        if not opaque.any():
            continue
        keep = opaque & ~is_key_fringe(fr[:, :, :3], opaque)
        if keep.any():
            samples.append(fr[keep][:, :3])
    all_c = np.concatenate(samples, axis=0)
    if len(all_c) > 80000:
        rng = np.random.default_rng(0)
        all_c = all_c[rng.choice(len(all_c), 80000, replace=False)]
    unique_count = len(np.unique(all_c, axis=0))
    k = min(colors, max(8, unique_count))
    z = np.ascontiguousarray(all_c.astype(np.float32))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.25)
    _, _, centers = cv2.kmeans(z, k, None, criteria, 8, cv2.KMEANS_PP_CENTERS)
    pal_rgb = np.clip(np.round(centers), 0, 255).astype(np.int16)
    palette_rgb = pal_rgb.reshape(1, -1, 3).astype(np.uint8)
    hue, sat = _magenta_hue(palette_rgb)
    usable = ~((hue[0] >= 145) & (hue[0] <= 179) & (sat[0] >= 50))
    if not usable.any():
        usable[:] = True
    pal_rgb = pal_rgb[usable]
    out = []
    for fr in frames:
        rgba = fr.copy()
        opaque = rgba[:, :, 3] >= 128
        if opaque.any():
            pix = rgba[opaque][:, :3].astype(np.int32)
            pal32 = pal_rgb.astype(np.int32)
            distance = ((pix[:, None, :] - pal32[None, :, :]) ** 2).sum(-1)
            rgba[opaque, :3] = pal_rgb[distance.argmin(axis=1)].astype(np.uint8)
        rgba[opaque, 3] = 255
        rgba[~opaque, :] = 0
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (rgba[:, :, 3] >= 128).astype(np.uint8), connectivity=8
        )
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] < 4:
                rgba[labels == index] = 0
        out.append(rgba)
    return out


def align_frames(cells: list[np.ndarray]) -> tuple[list[np.ndarray], dict]:
    """Place every cell onto one canvas, feet on a shared baseline, sleep kept low."""
    metrics = [frame_metrics(c) for c in cells]
    # Horizontal anchor: mean body center of standing frames
    stand_cx = float(np.mean([metrics[i]["body_cx"] for i in STANDING]))
    # Full-res extents relative to (stand_cx, baseline_i)
    left = right = up = 0
    for i, m in enumerate(metrics):
        left = max(left, int(np.ceil(stand_cx - m["x0"])))
        right = max(right, int(np.ceil(m["x1"] - stand_cx)))
        up = max(up, m["baseline"] - m["y0"])
    # All frames, including sleep, sit on this baseline (sleep stays a low pose).
    canvas_w = left + right + 1 + PAD_LEFT + PAD_RIGHT
    canvas_h = up + 1 + PAD_TOP + PAD_BOTTOM
    # Pad so both dimensions divide by SCALE
    canvas_w += (SCALE - canvas_w % SCALE) % SCALE
    canvas_h += (SCALE - canvas_h % SCALE) % SCALE
    ax = PAD_LEFT + left
    baseline = PAD_TOP + up
    log(f"full-res canvas {canvas_w}x{canvas_h}, body cx={ax:.1f}, baseline={baseline}")

    placed = []
    for i, cell in enumerate(cells):
        canvas = np.zeros((canvas_h, canvas_w, 4), np.uint8)
        m = metrics[i]
        dx = int(round(ax - m["body_cx"]))
        dy = baseline - m["baseline"]
        src = cell
        y0 = dy
        x0 = dx
        # clip
        sy0 = sx0 = 0
        sy1, sx1 = src.shape[:2]
        if y0 < 0:
            sy0 = -y0
            y0 = 0
        if x0 < 0:
            sx0 = -x0
            x0 = 0
        y1 = y0 + (sy1 - sy0)
        x1 = x0 + (sx1 - sx0)
        if y1 > canvas_h:
            sy1 -= y1 - canvas_h
            y1 = canvas_h
        if x1 > canvas_w:
            sx1 -= x1 - canvas_w
            x1 = canvas_w
        dest = canvas[y0:y1, x0:x1]
        src_p = src[sy0:sy1, sx0:sx1]
        a = src_p[:, :, 3:4].astype(np.float32) / 255.0
        dest[:] = (src_p * a + dest * (1 - a)).astype(np.uint8)
        placed.append(canvas)
        metrics[i]["dx"] = dx
        metrics[i]["dy"] = dy
    return placed, {"w": canvas_w, "h": canvas_h, "ax": ax, "baseline": baseline, "metrics": metrics}


def assemble_sheet(frames: list[np.ndarray], cols: int = 4) -> np.ndarray:
    fh, fw = frames[0].shape[:2]
    rows = int(np.ceil(len(frames) / cols))
    sheet = np.zeros((rows * fh, cols * fw, 4), np.uint8)
    for i, fr in enumerate(frames):
        r, c = divmod(i, cols)
        sheet[r * fh : (r + 1) * fh, c * fw : (c + 1) * fw] = fr
    return sheet


def save_png(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, "PNG", optimize=True)


def measure_content_box(frame: np.ndarray, use_body: bool = True) -> dict:
    opaque = frame[:, :, 3] >= 128
    mask = largest_component(opaque) if use_body else opaque
    ys, xs = np.where(mask)
    return {
        "x": int(xs.min()),
        "y": int(ys.min()),
        "w": int(xs.max() - xs.min() + 1),
        "h": int(ys.max() - ys.min() + 1),
    }


def build_pack_json(frame_w: int, frame_h: int, content: dict, sleeping_box: dict, anchor_x: float, baseline: int) -> dict:
    box = {"x": 0, "y": 0, "w": frame_w, "h": frame_h}

    def anim(frames, loop=True, ms=None):
        rec = {
            "sheet": "qianxia.png",
            "frameW": frame_w,
            "frameH": frame_h,
            "cols": 4,
            "box": dict(box),
            "frames": [{"i": i, "ms": d} for i, d in frames],
        }
        if not loop:
            rec["loop"] = False
        return rec

    pack = {
        "schema": 1,
        "id": "qianxia-pixel",
        "name": DISPLAY_NAME,
        "author": "粉丝向适配（AI 辅助）",
        "pixelArt": True,
        "scale": 1,
        "canvas": {"w": frame_w, "h": frame_h},
        "anchor": {"x": round(anchor_x, 1) if not float(anchor_x).is_integer() else int(anchor_x), "baseline": int(baseline)},
        "contentBox": content,
        "animations": {
            "idle": anim([(0, 450), (1, 450), (2, 450), (3, 450), (2, 450), (1, 450)]),
            "thinking": anim([(4, 520), (5, 560), (4, 520), (5, 600)]),
            "working": anim([(6, 280), (7, 420)]),
            "notification": anim([(8, 500), (9, 700)]),
            "attention": anim([(7, 400), (6, 280), (7, 500)]),
            "error": anim([(10, 500), (11, 700)]),
            "sleeping": anim([(13, 900), (14, 900), (15, 1000), (14, 900)]),
            "waking": anim([(14, 280), (13, 280), (12, 500)], loop=False),
            "humming": anim([(7, 500), (6, 280), (7, 500)]),
            "wave": anim([(8, 280), (9, 420)], loop=False),
            "shy": anim([(10, 280), (11, 420)], loop=False),
            "floppy": anim([(13, 400), (14, 400)]),
        },
        "states": {
            "idle": ["idle", "humming"],
            "thinking": ["thinking"],
            "working": ["working"],
            "notification": ["notification"],
            "attention": ["attention"],
            "error": ["error"],
            "sleeping": ["sleeping"],
            "waking": ["waking"],
            "juggling": ["working"],
            "sweeping": ["thinking"],
        },
        "reactions": {
            "tapLeft": "wave",
            "tapRight": "wave",
            "tickle": "shy",
            "drag": "floppy",
        },
        "roam": {"anim": "idle", "faces": "front"},
        "sleep": {"afterMs": 60000},
        "idlePool": {
            "anims": [{"anim": "humming", "ms": 2400, "weight": 1}],
            "everyMs": {"mean": 120000, "min": 45000, "max": 300000},
        },
        "hitBoxes": {
            "default": dict(content),
            "sleeping": dict(sleeping_box),
            "sleepingAnims": ["sleeping"],
            "byAnim": {"floppy": dict(sleeping_box)},
        },
    }
    return pack


# --- validation (local format only; not an official App import test) ---

def png_is_apng(path: Path) -> bool:
    data = path.read_bytes()
    # acTL chunk would appear after IHDR in APNG
    return b"acTL" in data


def sheet_bounds_ok(path: Path, frame_w: int, frame_h: int, cols: int, max_i: int) -> tuple[bool, str]:
    im = Image.open(path)
    rows = max_i // cols + 1
    if im.width < cols * frame_w or im.height < rows * frame_h:
        return False, f"{path.name} is {im.size}, need at least {cols*frame_w}x{rows*frame_h}"
    if im.mode not in ("RGBA", "LA"):
        # allow RGB only if we know we wrote RGBA
        return False, f"{path.name} mode {im.mode}, expected RGBA"
    return True, "ok"


def validate_pack(pack_dir: Path, pack: dict) -> list[str]:
    errors = []
    notes = []
    pj = pack_dir / "pack.json"
    raw = pj.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("pack.json has a BOM")
    if pack.get("schema") != 1:
        errors.append("schema must be 1")
    pid = pack.get("id", "")
    if not isinstance(pid, str) or not pid:
        errors.append("id missing")
    canvas = pack["canvas"]
    for k in ("w", "h"):
        if not isinstance(canvas[k], int) or canvas[k] < 1:
            errors.append(f"canvas.{k} must be a positive int")
    cb = pack["contentBox"]
    if cb["w"] <= 0 or cb["h"] <= 0:
        errors.append("contentBox w,h must be > 0")
    if "idle" not in pack.get("animations", {}):
        errors.append("missing idle animation")
    if not pack.get("states", {}).get("idle"):
        errors.append("states.idle must be non-empty")
    scale = pack.get("scale", 1)
    if not isinstance(scale, int) or scale < 1:
        errors.append("scale must be a positive int")
    sheets = {}
    for name, anim in pack["animations"].items():
        fw, fh, cols = anim["frameW"], anim["frameH"], anim["cols"]
        for key, val in (("frameW", fw), ("frameH", fh), ("cols", cols)):
            if not isinstance(val, int) or not 1 <= val <= 4096:
                errors.append(f"{name}.{key} out of range")
        box = anim["box"]
        if fw != int(np.trunc(box["w"] * scale)):
            errors.append(f"{name}: frameW {fw} != trunc(box.w {box['w']} * scale {scale})")
        if fh != int(np.trunc(box["h"] * scale)):
            errors.append(f"{name}: frameH {fh} != trunc(box.h {box['h']} * scale {scale})")
        frames = anim["frames"]
        if not frames:
            errors.append(f"{name} has no frames")
        for fr in frames:
            if not isinstance(fr["i"], int) or fr["i"] < 0:
                errors.append(f"{name} frame i must be int >= 0")
            if not isinstance(fr["ms"], int) or fr["ms"] < 1:
                errors.append(f"{name} frame ms must be int >= 1")
        sheet = pack_dir / anim["sheet"]
        if not sheet.exists():
            errors.append(f"{name}: missing sheet {anim['sheet']}")
            continue
        if png_is_apng(sheet):
            errors.append(f"{anim['sheet']} looks like APNG (rejected by the app)")
        max_i = max(fr["i"] for fr in frames)
        ok, msg = sheet_bounds_ok(sheet, fw, fh, cols, max_i)
        if not ok:
            errors.append(msg)
        im = Image.open(sheet)
        if im.width > 4096 or im.height > 4096:
            errors.append(f"{sheet.name} exceeds 4096 on a side")
        if im.size[0] * im.size[1] * 4 > 16 * 1024 * 1024:
            errors.append(f"{sheet.name} decode buffer > 16 MiB")
        arr = np.array(im.convert("RGBA"))
        if arr[:, :, 3].max() == 0:
            errors.append(f"{sheet.name} is fully transparent")
        if arr[:, :, 3].min() == 255:
            notes.append(f"{sheet.name} has no transparent pixels")
        # unused palette check skipped
        sheets[anim["sheet"]] = im
    if len(sheets) > 64:
        errors.append("more than 64 sheets")
    # json size
    if pj.stat().st_size > 256 * 1024:
        errors.append("pack.json > 256 KiB")
    return errors, notes


def make_zip(pack_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(pack_dir.iterdir()):
            if p.is_file():
                zf.write(p, p.name)
    st = zip_path.stat()
    if st.st_size > 64 * 1024 * 1024:
        raise SystemExit(f"zip is {st.st_size} bytes, over 64 MiB")
    log(f"wrote {zip_path} ({st.st_size} bytes)")


def validate_zip(zip_path: Path, pack: dict) -> list[str]:
    errors = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        if zf.comment:
            pass
        names = zf.namelist()
        # no backslash, no abs, no ..
        for n in names:
            if n.startswith("/") or "\\" in n or ".." in Path(n).parts or ":" in n:
                errors.append(f"illegal zip path {n!r}")
        packs = [n for n in names if Path(n).name == "pack.json"]
        if len(packs) != 1:
            errors.append(f"expected one pack.json, found {packs}")
        else:
            # only top-level or one folder
            parts = Path(packs[0]).parts
            if len(parts) > 2:
                errors.append("pack.json nested deeper than one folder")
        # all referenced sheets present
        prefix = str(Path(packs[0]).parent) if packs and len(Path(packs[0]).parts) > 1 else ""
        for anim in pack["animations"].values():
            want = f"{prefix}/{anim['sheet']}" if prefix else anim["sheet"]
            if want not in names:
                errors.append(f"zip missing {want}")
        expanded = sum(i.file_size for i in zf.infolist())
        if expanded > 64 * 1024 * 1024:
            errors.append("expanded zip > 64 MiB")
        if len(zf.infolist()) > 256:
            errors.append("too many zip entries")
    return errors


def composite_on(rgba: np.ndarray, bg: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (rgba.shape[1], rgba.shape[0]), bg)
    base.paste(Image.fromarray(rgba, "RGBA"), mask=Image.fromarray(rgba, "RGBA"))
    return base


def upscale(im: Image.Image, n: int) -> Image.Image:
    return im.resize((im.size[0] * n, im.size[1] * n), Image.NEAREST)


def make_previews(frames: list[np.ndarray], pack: dict, out_dir: Path, zoom: int = 4) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fw, fh = frames[0].shape[1], frames[0].shape[0]
    labels = [
        "idle 0",
        "idle 1",
        "idle 2",
        "idle 3",
        "think/write 4",
        "think/write 5",
        "work 6",
        "hum 7",
        "hand 8",
        "hand 9",
        "shy 10",
        "shy 11",
        "drowsy 12",
        "sleep 13",
        "sleep 14",
        "sleep 15",
    ]
    cols, rows = 4, 4
    cell_w, cell_h = fw * zoom + 16, fh * zoom + 36
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), PREVIEW_BG)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
        font_s = ImageFont.truetype("DejaVuSans.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
        font_s = font
    for i, fr in enumerate(frames):
        r, c = divmod(i, 4)
        tile = upscale(composite_on(fr, PREVIEW_BG), zoom)
        x, y = c * cell_w + 8, r * cell_h + 8
        sheet.paste(tile, (x, y))
        draw.text((x, y + fh * zoom + 4), labels[i], fill=(230, 230, 230), font=font_s)
    legend = (
        "idle: 0-3  thinking: 4-5  working: 6-7  notification: 8-9\n"
        "attention: 7+6  error: 10-11  sleeping: 13-15 (low)  waking: 14-13-12"
    )
    # add a legend strip
    full = Image.new("RGB", (sheet.width, sheet.height + 48), PREVIEW_BG)
    full.paste(sheet, (0, 0))
    d2 = ImageDraw.Draw(full)
    d2.text((8, sheet.height + 8), legend, fill=(230, 230, 230), font=font)
    full.save(out_dir / "contact-sheet.png")

    def anim_frames(name: str) -> list[tuple[Image.Image, int]]:
        anim = pack["animations"][name]
        out = []
        for fr in anim["frames"]:
            im = upscale(composite_on(frames[fr["i"]], PREVIEW_BG), zoom)
            out.append((im, fr["ms"]))
        return out

    def save_gif(path: Path, seq: list[tuple[Image.Image, int]], loop: int = 0) -> None:
        imgs = [im.convert("P", palette=Image.ADAPTIVE, dither=Image.NONE) for im, _ in seq]
        durations = [ms for _, ms in seq]
        imgs[0].save(
            path,
            save_all=True,
            append_images=imgs[1:],
            duration=durations,
            loop=loop,
            disposal=2,
            optimize=False,
        )

    for name in ("idle", "thinking", "working", "notification", "attention", "error", "sleeping", "waking"):
        seq = anim_frames(name)
        save_gif(out_dir / f"{name}.gif", seq, loop=0 if name != "waking" else 0)

    # Combined reel: two cycles of most states, one waking.
    reel: list[tuple[Image.Image, int]] = []
    order = [
        ("idle", 2),
        ("thinking", 2),
        ("working", 2),
        ("notification", 2),
        ("attention", 2),
        ("error", 2),
        ("sleeping", 2),
        ("waking", 1),
    ]
    for name, reps in order:
        seq = anim_frames(name)
        for _ in range(reps):
            reel.extend(seq)
    save_gif(out_dir / "preview.gif", reel, loop=0)
    log(f"previews written under {out_dir}")


def fringe_report(frames: list[np.ndarray]) -> tuple[int, int, list[int]]:
    fringe = 0
    holes = 0
    feet: list[int] = []
    for frame in frames:
        opaque = frame[:, :, 3] >= 128
        fringe += int(is_key_fringe(frame[:, :, :3], opaque).sum())
        inverse = (~opaque).astype(np.uint8)
        count, labels = cv2.connectedComponents(inverse, connectivity=4)
        if count > 1:
            border = set(np.unique(labels[0, :])) | set(np.unique(labels[-1, :]))
            border |= set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
            for index in range(1, count):
                if index not in border:
                    holes += int((labels == index).sum())
        body = largest_component(opaque)
        ys = np.where(body)[0]
        feet.append(int(ys.max()) if len(ys) else -1)
    return fringe, holes, feet


def main() -> int:
    for directory in (PACK_DIR, PREVIEW_DIR, PACKAGE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    debug = os.environ.get("QIANXIA_DEBUG") == "1"
    if debug:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    if not SOURCE.exists():
        raise SystemExit(f"missing source sheet: {SOURCE}")
    sheet = np.array(Image.open(SOURCE).convert("RGB"))
    if sheet.shape[0] != 1024 or sheet.shape[1] != 1024:
        raise SystemExit(f"expected 1024x1024 sheet, got {sheet.shape}")

    bg_ref = estimate_background(sheet)
    log(f"background estimate RGB {bg_ref.tolist()}")
    bg = background_mask(sheet, bg_ref)
    log(f"keyed background fraction {bg.mean():.3f}")

    cells = [clean_magenta_fringe(cell) for cell in extract_cells(sheet, bg)]
    if debug:
        for index, cell in enumerate(cells):
            row, col = divmod(index, 4)
            save_png(DEBUG_DIR / f"cell_{row}_{col}_rgba.png", cell)

    placed, _layout = align_frames(cells)
    small = [mode_downsample(frame) for frame in placed]
    small = [clean_magenta_fringe(frame) for frame in shared_palette(small, colors=32)]
    small = [fill_small_holes(frame) for frame in small]
    fw, fh = small[0].shape[1], small[0].shape[0]
    log(f"downsampled frame {fw}x{fh}")
    fringe, holes, feet = fringe_report(small)
    log(f"edge magenta pixels {fringe}, enclosed hole pixels {holes}")
    log(f"body foot rows {feet}")
    if fringe:
        log("magenta backdrop tint remains on the silhouette")
        return 1
    if holes:
        log("enclosed holes remain")
        return 1
    standing_feet = [feet[index] for index in STANDING]
    if len(set(standing_feet)) != 1:
        log(f"standing baselines differ: {standing_feet}")
        return 1

    idle_box = measure_content_box(small[0], use_body=True)
    sleep_union = np.zeros((fh, fw), dtype=bool)
    for index in SLEEP:
        sleep_union |= largest_component(small[index][:, :, 3] >= 128)
    ys, xs = np.where(sleep_union)
    sleep_box = {
        "x": int(xs.min()),
        "y": int(ys.min()),
        "w": int(xs.max() - xs.min() + 1),
        "h": int(ys.max() - ys.min() + 1),
    }
    body0 = largest_component(small[0][:, :, 3] >= 128)
    bys, bxs = np.where(body0)
    baseline = int(bys.max()) + 1
    anchor_x = round(float(bxs.mean()) * 2) / 2
    log(f"contentBox {idle_box}, sleep box {sleep_box}, anchor x={anchor_x}, baseline={baseline}")

    red = 0
    for frame in small:
        rgb = frame[:, :, :3]
        opaque = frame[:, :, 3] >= 128
        hue, _sat = _magenta_hue(rgb)
        red += int((opaque & (hue <= 12) & (rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 100)).sum())
    note = int((small[7][:, 48:, 3] >= 128).sum()) if small[7].shape[1] > 48 else 0
    log(f"red-clothing pixels {red}, humming-note pixels {note}")
    if red < 80 or note < 4:
        log("red clothing or the humming note was lost")
        return 1

    atlas = assemble_sheet(small, 4)
    save_png(PACK_DIR / "qianxia.png", atlas)
    pack = build_pack_json(fw, fh, idle_box, sleep_box, anchor_x, baseline)
    (PACK_DIR / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    make_zip(PACK_DIR, ZIP_PATH)
    make_previews(small, pack, PREVIEW_DIR, zoom=4)
    validator = REPO / "scripts" / "validate_pet_pack.py"
    result = subprocess.run(
        [
            sys.executable,
            str(validator),
            str(PACK_DIR),
            str(ZIP_PATH),
            "--display-name",
            DISPLAY_NAME,
        ],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
