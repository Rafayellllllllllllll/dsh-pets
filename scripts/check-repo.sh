#!/bin/sh
# 检查仓库结构和未完成宠物的状态句。
# 不校验图集几何，也不代替 DSH Go App 的导入校验。
set -eu

CDPATH= cd -- "$(dirname "$0")/.."

fail=0
say_ok() { printf 'OK   %s\n' "$1"; }
say_fail() { printf 'FAIL %s\n' "$1" >&2; fail=1; }

need() {
  if [ -s "$1" ]; then
    say_ok "present $1"
  else
    say_fail "missing or empty: $1"
  fi
}

need README.md
need AGENTS.md
need .gitignore
need .gitattributes
need docs/spec.md
need docs/pack.skeleton.json
need pets/README.md
need pets/qianxia/README.md
need pets/qianxia/source/qianxia-4x4.jpg

found_lic=0
for lic in LICENSE LICENSE.md LICENSE.txt COPYING COPYING.md; do
  if [ -e "$lic" ]; then
    say_fail "repository license file is not allowed: $lic"
    found_lic=1
  fi
done
if [ "$found_lic" -eq 0 ]; then
  say_ok "no repository-level license file"
fi

secrets=$(find . -path ./.git -prune -o -type f \( \
  -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \
  -o -name 'id_rsa' -o -name 'id_rsa.pub' -o -name 'credentials.json' \
  -o -name 'secrets.json' \
  \) -print)
if [ -n "$secrets" ]; then
  printf '%s\n' "$secrets" >&2
  say_fail "secret-like files present"
else
  say_ok "no secret-like files"
fi

if python3 - << 'PY'
import pathlib
import sys

needles = [
    "\u5df2\u83b7\u6388\u6743",
    "\u672a\u83b7\u6388\u6743",
    "\u6c34\u5370",
    "\u5077\u6765",
    "\u5077\u7684",
    "@qq.com",
]
files = [
    path
    for path in [
        pathlib.Path(".gitignore"),
        pathlib.Path(".gitattributes"),
        *pathlib.Path(".").glob("*.md"),
        *pathlib.Path("docs").rglob("*"),
        *pathlib.Path("pets").rglob("*"),
        *pathlib.Path("scripts").rglob("*"),
    ]
    if path.is_file()
]
skip = {pathlib.Path("scripts/check-repo.sh")}
bad = False
for path in files:
    if path in skip or path.suffix.lower() in {".jpg", ".jpeg", ".png", ".zip"}:
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for needle in needles:
        if needle in text:
            print(f"{path}: contains forbidden text", file=sys.stderr)
            bad = True
            break
sys.exit(1 if bad else 0)
PY
then
  say_ok "no forbidden wording or personal address"
else
  say_fail "forbidden wording or personal address"
fi

found_pet=0
for dir in pets/*/; do
  [ -d "$dir" ] || continue
  found_pet=1
  name=${dir#pets/}
  name=${name%/}
  if [ ! -s "${dir}README.md" ]; then
    say_fail "$name is missing README.md"
    continue
  fi
  if [ -f "${dir}pack.json" ]; then
    if grep -q '尚不可导入' "${dir}README.md"; then
      say_fail "$name has pack.json but README still says 尚不可导入"
    else
      say_ok "$name has pack.json and README no longer says 尚不可导入"
    fi
  else
    if grep -q '尚不可导入' "${dir}README.md"; then
      say_ok "$name is unfinished and marked 尚不可导入"
    else
      say_fail "$name has no pack.json; README must include 尚不可导入"
    fi
  fi
  if [ ! -f "${dir}pack.json" ] && find "$dir" -name '*.zip' -type f | grep -q .; then
    say_fail "$name has a zip but no pack.json"
  fi
done
if [ "$found_pet" -eq 0 ]; then
  say_fail "no pet directories under pets/"
fi

if python3 - << 'PY'
import hashlib
import json
import pathlib
import struct
import sys

root = pathlib.Path(".")
raw = (root / "pets/qianxia/source/qianxia-4x4.jpg").read_bytes()
expect = "3f4b27a673b00243f0c9da16a5e1d3770a85467fa8854fd4eae05b7fa20684f9"
digest = hashlib.sha256(raw).hexdigest()
if digest != expect:
    sys.exit("sha256 mismatch: " + digest)
if raw[:3] != b"\xff\xd8\xff":
    sys.exit("source is not a JPEG")

data = raw
index = 2
width = height = None
while index < len(data) - 8:
    if data[index] != 0xFF:
        index += 1
        continue
    marker = data[index + 1]
    if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
        index += 2
        continue
    if marker == 0xDA:
        break
    seglen = struct.unpack(">H", data[index + 2 : index + 4])[0]
    if marker in (0xC0, 0xC1, 0xC2):
        height, width = struct.unpack(">HH", data[index + 5 : index + 9])
        break
    index += 2 + seglen
if (width, height) != (1024, 1024):
    sys.exit(f"unexpected dimensions: {width}x{height}")
if len(raw) != 306567:
    sys.exit(f"unexpected byte length: {len(raw)}")
print(
    f"OK   qianxia source JPEG 1024x1024 bytes {len(raw)} sha256 {digest}"
)

skeleton = json.loads(
    (root / "docs/pack.skeleton.json").read_text(encoding="utf-8")
)
if skeleton.get("schema") != 1:
    sys.exit("skeleton schema is not 1")
idle = skeleton.get("animations", {}).get("idle")
if not idle:
    sys.exit("skeleton has no idle animation")
if not skeleton.get("states", {}).get("idle"):
    sys.exit("skeleton states.idle is empty")
if "license" in skeleton or "licence" in skeleton:
    sys.exit("skeleton must not declare a license")
scale = skeleton["scale"]
box = idle["box"]
if idle["frameW"] != int(box["w"] * scale) or idle["frameH"] != int(
    box["h"] * scale
):
    sys.exit("skeleton frame size does not match box x scale")
print("OK   pack skeleton schema 1, idle present, no license field")
PY
then
  :
else
  say_fail "source image or skeleton check failed"
fi

if [ "$fail" -ne 0 ]; then
  printf 'check-repo: failed\n' >&2
  exit 1
fi
printf 'check-repo: passed\n'
