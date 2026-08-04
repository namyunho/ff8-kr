#!/usr/bin/env python3
"""원본 글리프와 **현재 읽은 값을 나란히** 놓은 대조 시트를 낸다.

## 왜 이 형식인가

글리프만 크게 보여 주는 시트는 「이게 무슨 글자인가」를 묻는다. 처음 읽을 때는
맞지만 **검수할 때는 답이 안 나온다.** 1,500칸을 하나씩 떠올려 비교해야 한다.

원본 옆에 우리가 읽은 값을 폰트로 그려 붙이면 질문이 바뀐다 — 「이 둘이 같은
글자인가」. 어긋난 칸은 눈에 즉시 걸린다. 획 하나 차이도 나란히 놓으면 보인다.

    768  [원본]  [읽은값]     흰색이 롬, 초록이 우리 대응표
    880  [원본]  (없음)       번호가 빨강이면 읽은 값이 없다

## 두 뱅크를 다 낸다

**뱅크0** 은 인덱스가 곧 글자다. 시스템 폰트에서 바로 꺼낸다.

**뱅크1** 은 필드마다 내용이 달라 인덱스가 뜻이 없다. 모양 해시가 키이므로
`work/bank1-shapes.json` 캐시에서 꺼내고 번호 대신 키 앞자리를 적는다.

    python3 scripts/render_glyph_proof.py --bank 0
    python3 scripts/render_glyph_proof.py --bank 1
    python3 scripts/render_glyph_proof.py --bank 0 --unverified
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_bank1_map as B1                # noqa: E402
import extract_field_text as FT             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAPS = {0: ROOT / "data" / "glyph-map-bank0.json",
        1: ROOT / "data" / "glyph-map-bank1.json"}

# 렌더에 쓸 폰트. 12x12 손그림과 자형이 같지는 않지만 **같은 글자인지**를
# 가리는 데는 충분하다. 없으면 다음 것으로 넘어간다.
FONTS = ("/System/Library/Fonts/Hiragino Sans GB.ttc",
         "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
         "/System/Library/Fonts/AppleSDGothicNeo.ttc")

ZOOM = 8
PAD = 10
LABEL = 13
COLUMNS = 8
ROWS = 10
INK = {0: None, 1: (70, 70, 70), 2: (150, 150, 150), 3: (255, 255, 255)}
READ = (110, 235, 140)          # 우리가 읽은 값
NUMBER = (90, 150, 230)         # 번호
MISSING = (235, 90, 90)         # 읽은 값이 없는 칸


def pick_font(size: int):
    from PIL import ImageFont

    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("일본어를 그릴 폰트를 못 찾았다")


def bank0_rows(unverified: bool) -> list[dict]:
    table = json.loads(MAPS[0].read_text(encoding="utf-8"))["entries"]
    font = FT.system_font()
    rows = []
    for key, entry in sorted(table.items(), key=lambda kv: int(kv[0])):
        source = entry.get("source", "?")
        if unverified and source in ("confirmed", "original"):
            continue
        rows.append({"label": key, "char": entry.get("char", ""),
                     "cells": font.glyph(int(key)), "source": source})
    return rows


def bank1_rows(unverified: bool) -> list[dict]:
    cache = B1.load_cache()          # `ordered` 는 캐시 전체를 받는다
    table = json.loads(MAPS[1].read_text(encoding="utf-8"))["entries"]
    rows = []
    for key, entry in B1.ordered(cache):
        char = (table.get(key) or {}).get("char", "")
        if unverified and char:
            continue
        rows.append({"label": key[:6], "char": char,
                     "cells": B1.unpack(entry["cells"]),
                     "source": (table.get(key) or {}).get("source", "없음")})
    return rows


def draw_sheet(rows: list[dict], path: Path) -> None:
    from PIL import Image, ImageDraw

    cell = FT.CELL * ZOOM
    step_x = cell * 2 + PAD * 2         # 원본 + 읽은값 두 칸
    step_y = cell + LABEL + PAD
    lines = (len(rows) + COLUMNS - 1) // COLUMNS
    image = Image.new("RGB", (PAD + COLUMNS * step_x, PAD + lines * step_y),
                      (0, 0, 0))
    pixels, draw = image.load(), ImageDraw.Draw(image)
    glyph_font = pick_font(cell)

    for slot, row in enumerate(rows):
        column, line = slot % COLUMNS, slot // COLUMNS
        left = PAD + column * step_x
        top = PAD + line * step_y + LABEL
        draw.text((left, top - LABEL), row["label"],
                  fill=NUMBER if row["char"] else MISSING)
        for y, values in enumerate(row["cells"]):
            for x, value in enumerate(values):
                colour = INK[value]
                if colour is None:
                    continue
                for zy in range(ZOOM):
                    for zx in range(ZOOM):
                        pixels[left + x * ZOOM + zx, top + y * ZOOM + zy] = colour
        if row["char"]:
            draw.text((left + cell + PAD, top), row["char"],
                      font=glyph_font, fill=READ)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", type=int, choices=(0, 1), default=0)
    parser.add_argument("--out", type=Path, default=Path("data/glyph-sheets"))
    parser.add_argument("--unverified", action="store_true",
                        help="아직 확정 안 된 것만 낸다")
    args = parser.parse_args()

    rows = (bank0_rows if args.bank == 0 else bank1_rows)(args.unverified)
    if not rows:
        print("낼 것이 없다")
        return 0
    per_page = COLUMNS * ROWS
    pages = 0
    for start in range(0, len(rows), per_page):
        path = args.out / f"proof-bank{args.bank}-{start:04d}.png"
        draw_sheet(rows[start:start + per_page], path)
        pages += 1
    filled = sum(1 for r in rows if r["char"])
    print(f"뱅크{args.bank}  {len(rows)}칸 (읽은 값 있음 {filled}, "
          f"없음 {len(rows) - filled})")
    print(f"대조 시트 {pages}장 → {args.out}/proof-bank{args.bank}-*.png")
    print("흰색이 롬 원본, 초록이 우리 대응표다. 번호가 빨강이면 읽은 값이 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
