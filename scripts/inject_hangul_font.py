#!/usr/bin/env python3
"""원본 폰트의 한자 구간만 한글로 바꿔 사본 디스크에 써넣는다.

**폐기됨 (2026-08-08).** 장면 단위 시험용 초기 도구다. 배치를
`work/patched/hangul-layout.json` 에 따로 써내는데, 그 파일은 154칸짜리
옛 판이고 `patch_disc.py` 는 더 이상 읽지 않는다. 정본은
`data/glyph-layout.json` 하나뿐이다. 이 도구를 다시 쓸 일이 있으면 먼저
정본을 읽도록 고쳐야 한다.

**전체를 한글로 덮지 않는다.** 인덱스 `0..223` 은 가나·숫자·라틴·부호라 메뉴와
아직 번역하지 않은 대사가 그대로 읽혀야 한다. 한글은 `224` 이후 한자 자리에
넣는다. 658칸이 남으므로 장면 단위 시험에는 넉넉하다.

한글이 224 이후에 놓이므로 한 글자가 2바이트로 실린다. 전체 번역을 넣을 때는
자주 쓰는 음절을 1바이트 구간으로 옮기는 편이 낫다(`docs/roadmap.md` M5).

글리프 하나는 4bpp 텍스처에 이렇게 들어간다. 셀 하나에 글리프 둘이 인터리브
되므로 **짝수 인덱스는 니블의 하위 2비트, 홀수는 상위 2비트**다.

    셀 = 인덱스 >> 1,  (cx, cy) = (셀 % 21 * 12, 셀 // 21 * 12)
    바이트 = 픽셀시작 + (cy + y) * stride + ((cx + x) >> 1)
    니블   = 상위 if (cx + x) 홀수 else 하위

    python3 scripts/inject_hangul_font.py --chars-from work/translate-slice/slice.json
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BH              # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402

FONT_LBA = 849                              # IMG TOC #130
FONT_BYTES = 33764
BASE_INDEX = 224                            # 여기부터 한자 자리다
TOKEN = re.compile(r"\{[^}]*\}")
LAYOUT_PATH = PD.PATCH_DIR / "hangul-layout.json"


def needed_chars(path: Path) -> list[str]:
    """번역 계획에서 원본 폰트에 없는 글자만 순서대로 모은다."""
    plan = json.loads(path.read_text(encoding="utf-8"))
    glyphs = GT.GlyphMap.load()
    seen: dict[str, None] = {}
    for messages in plan.values():
        for text in messages.values():
            for char in TOKEN.sub("", text):
                if char not in glyphs.index:
                    seen.setdefault(char, None)
    return sorted(seen)


def put_glyph(texture: bytearray, offset: int, stride: int,
              index: int, cell: list[list[int]], level: int) -> None:
    cx = (index >> 1) % BH.COLS * BH.CELL
    cy = (index >> 1) // BH.COLS * BH.CELL
    high = index & 1
    for y in range(BH.CELL):
        for x in range(BH.CELL):
            value = level if cell[y][x] else 0
            px, py = cx + x, cy + y
            at = offset + py * stride + (px >> 1)
            packed = texture[at]
            nibble = (packed >> 4) if (px & 1) else (packed & 0xF)
            nibble = ((nibble & 0x3) | (value << 2)) if high \
                else ((nibble & 0xC) | value)
            texture[at] = ((packed & 0x0F) | (nibble << 4)) if (px & 1) \
                else ((packed & 0xF0) | nibble)


def build(chars: list[str], ttf: Path, size: int, level: int) -> tuple[bytes, list[str]]:
    blob = bytearray(FT.SYSFNT.read_bytes())
    table_offset, tim_offset = struct.unpack_from("<II", blob, 0)
    clut_block = tim_offset + 8
    clut_size = struct.unpack_from("<I", blob, clut_block)[0]
    image_block = clut_block + clut_size
    pixels_at = image_block + 12
    width = struct.unpack_from("<H", blob, image_block + 8)[0] * 4
    stride = width // 2

    cells = BH.rasterize(ttf, size, chars)
    for slot, char in enumerate(chars):
        index = BASE_INDEX + slot
        put_glyph(blob, pixels_at, stride, index, cells[char], level)
        # 폭 테이블도 함께 고친다. 잉크 폭 + 1 이 진행폭이다.
        advance = min(BH.ink_width(cells[char]) + 1, 0xF)
        position = table_offset + (index >> 1)
        packed = blob[position]
        blob[position] = ((packed & 0x0F) | (advance << 4)) if (index & 1) \
            else ((packed & 0xF0) | advance)
    return bytes(blob), chars


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chars-from", type=Path, required=True,
                        help="번역 계획 JSON. 여기 쓰인 글자만 넣는다")
    parser.add_argument("--ttf", type=Path, default=BH.DEFAULT_TTF)
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--level", type=int, default=3, choices=range(1, 4),
                        help="획의 밝기 단계 (기본 3 = 가장 밝음)")
    parser.add_argument("--dry-run", action="store_true", help="쓰지 않는다")
    args = parser.parse_args()

    chars = needed_chars(args.chars_from)
    room = BH.PER_BANK - BASE_INDEX
    print(f"필요한 글자 {len(chars)}자 → 인덱스 {BASE_INDEX}..{BASE_INDEX + len(chars) - 1}"
          f" (자리 {room}칸)")
    if len(chars) > room:
        print(f"자리가 모자란다. {len(chars) - room}자 초과", file=sys.stderr)
        return 1

    blob, order = build(chars, args.ttf, args.size, args.level)
    if len(blob) != FONT_BYTES:
        print(f"폰트 크기가 달라졌다: {len(blob)} != {FONT_BYTES}", file=sys.stderr)
        return 1

    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT_PATH.write_text(json.dumps(
        {"base": BASE_INDEX, "chars": order}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"배치 {LAYOUT_PATH}")

    if args.dry_run:
        print("--dry-run 이므로 쓰지 않는다.")
        return 0
    if not PD.PATCH_BIN.exists():
        print("먼저 patch_disc.py --init 으로 사본을 만든다.", file=sys.stderr)
        return 2
    touched = PD.write_user(PD.PATCH_BIN, FONT_LBA, blob)
    print(f"LBA {FONT_LBA} 에 폰트를 썼다. 섹터 {touched}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
