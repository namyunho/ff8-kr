#!/usr/bin/env python3
"""뱅크0 글리프를 사람이 검수할 수 있게 시트와 판독지로 낸다.

## 왜 필요한가

뱅크0 대응표는 구멍이 없다. 그런데 880자 중 **`confirmed` 는 14자뿐**이고
나머지는 12x12 픽셀을 눈으로 읽은 값이다. 원본이 쓰는 849종의 89%가 미검증인
셈이다. 이 상태의 원문으로 번역을 돌리면 그만큼 어긋난다.

그래서 뱅크1 과 같은 방식으로 검수 자료를 낸다. **다른 점은 이미 읽은 값이
있다는 것**이라, 새로 읽는 것이 아니라 **확인하거나 정정하는** 형태다.

## 문맥을 반드시 함께 본다

12x12 에서 획 하나는 눈으로 안 갈린다. 뱅크1 85종을 읽었을 때 문맥이 눈을
여덟 곳 뒤집었다. 그래서 글리프만 보여 주지 않고 **그 글자가 실제로 쓰인
자리의 앞뒤**를 함께 싣는다.

## 내는 것

    data/glyph-sheets/bank0-NNN.png    확대한 글리프 (칸마다 인덱스·현재읽음·빈도)
    work/glyph-audit/bank0-worksheet.tsv

판독지는 `인덱스<탭>글자` 다. 현재 읽은 값이 이미 적혀 있으니 **맞으면 그대로
두고, 틀리면 고치고, 확신이 없으면 비운다.** 비운 것은 미검증으로 남는다.

    python3 scripts/build_bank0_sheets.py
    python3 scripts/build_bank0_sheets.py --unused        # 원본이 안 쓰는 것까지
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_bank1_map as B1                # noqa: E402
import extract_field_text as FT             # noqa: E402
import extract_menu_text as EM              # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "data" / "glyph-map-bank0.json"
FIELDS = ROOT / "work" / "text" / "field-messages.json"
BANK1 = 0x400
WINDOW = 6                  # 앞뒤로 볼 글자 수
SAMPLES = 3                 # 인덱스마다 실을 문맥 줄 수


def gather(glyphs: GT.GlyphMap):
    """원본을 훑어 인덱스마다 `(빈도, 문맥 몇 줄)` 을 모은다.

    문맥의 글자는 **현재 대응표로 푼 것**이다. 옆 글자도 틀렸을 수 있으니
    한 줄만 보고 판단하지 말고 여러 줄을 견줘야 한다.
    """
    count: collections.Counter = collections.Counter()
    context: dict[int, list[str]] = collections.defaultdict(list)

    def take(tokens):
        only = [t for t in tokens if t[0] == "g"]
        for position, token in enumerate(only):
            if token[1] & BANK1:
                continue
            index = token[1]
            count[index] += 1
            if len(context[index]) >= SAMPLES:
                continue
            window = only[max(0, position - WINDOW):position + WINDOW + 1]
            context[index].append("".join(
                "▮" if other is token else
                ("▪" if other[1] & BANK1 else glyphs.char.get(other[1], "·"))
                for other in window))

    document = json.loads(FIELDS.read_text(encoding="utf-8"))
    for row in document["fields"]:
        try:
            msd = FT.msd_section(FT.load_entry(row["field"]))
            starts = FT.message_offsets(msd)
        except Exception:                   # noqa: BLE001
            continue
        for start in starts:
            take(FT.tokenize(msd, start))

    for _sub, lba, size in EM.BLOCKS:
        blob = PD.read_user(FT.BIN_PATH, lba, size)
        for _group, off in EM.groups(blob):
            rels, why = EM.entries(blob, off)
            if why:
                continue
            for rel in rels:
                stop = blob.find(b"\x00", off + rel)
                if stop < 0:
                    continue
                take(FT.tokenize(bytes(blob[off + rel:stop]) + b"\x00", 0))
    return count, context


def sheets(font, rows, out: Path) -> None:
    from PIL import Image, ImageDraw

    cell = FT.CELL * B1.ZOOM
    step_x, step_y = cell + B1.PAD, cell + B1.LABEL * 2 + B1.PAD
    per_page = B1.SHEET_COLUMNS * B1.SHEET_ROWS
    out.mkdir(parents=True, exist_ok=True)
    pages = 0
    for start in range(0, len(rows), per_page):
        page = rows[start:start + per_page]
        lines = (len(page) + B1.SHEET_COLUMNS - 1) // B1.SHEET_COLUMNS
        image = Image.new("RGB", (B1.PAD + B1.SHEET_COLUMNS * step_x,
                                  B1.PAD + lines * step_y), (0, 0, 0))
        pixels, draw = image.load(), ImageDraw.Draw(image)
        for slot, row in enumerate(page):
            column, line = slot % B1.SHEET_COLUMNS, slot // B1.SHEET_COLUMNS
            left = B1.PAD + column * step_x
            top = B1.PAD + line * step_y + B1.LABEL * 2
            draw.text((left, top - B1.LABEL * 2),
                      f"{row['인덱스']}  {row['현재']}", fill=(120, 200, 255))
            draw.text((left, top - B1.LABEL), f"{row['빈도']:,}회",
                      fill=(120, 140, 170))
            for y, values in enumerate(font.glyph(row["인덱스"])):
                for x, value in enumerate(values):
                    colour = B1.INK[value]
                    if colour is None:
                        continue
                    for zy in range(B1.ZOOM):
                        for zx in range(B1.ZOOM):
                            pixels[left + x * B1.ZOOM + zx,
                                   top + y * B1.ZOOM + zy] = colour
        path = out / f"bank0-{start:03d}.png"
        image.save(path)
        pages += 1
    print(f"시트 {pages}장 → {out}/bank0-*.png")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheets", type=Path, default=Path("data/glyph-sheets"))
    parser.add_argument("--worksheet", type=Path,
                        default=Path("work/glyph-audit/bank0-worksheet.tsv"))
    parser.add_argument("--unused", action="store_true",
                        help="원본이 한 번도 안 쓰는 글자까지 낸다")
    args = parser.parse_args()

    if not FT.SYSFNT.exists():
        print(f"시스템 폰트가 없다: {FT.SYSFNT}", file=sys.stderr)
        return 2

    glyphs = GT.GlyphMap.load()
    table = json.loads(MAP_PATH.read_text(encoding="utf-8"))["entries"]
    print("원본을 훑어 빈도와 문맥을 모은다 …")
    count, context = gather(glyphs)

    rows = []
    for key, entry in table.items():
        index = int(key)
        used = count.get(index, 0)
        if not used and not args.unused:
            continue
        rows.append({"인덱스": index, "현재": entry.get("char", ""),
                     "출처": entry.get("source", "?"), "빈도": used,
                     "문맥": context.get(index, [])})
    rows.sort(key=lambda r: -r["빈도"])

    firm = sum(1 for r in rows if r["출처"] in ("confirmed", "original"))
    print(f"뱅크0 {len(rows)}자  (확정 {firm}자, 미검증 {len(rows) - firm}자)")

    sheets(FT.system_font(), rows, args.sheets)

    args.worksheet.parent.mkdir(parents=True, exist_ok=True)
    with args.worksheet.open("w", encoding="utf-8") as handle:
        handle.write(
            "# 뱅크0 검수. `인덱스<탭>글자` 다.\n"
            "# 현재 읽은 값이 적혀 있다 — **맞으면 그대로, 틀리면 고치고,\n"
            "# 확신이 없으면 비운다.** 비운 것은 미검증으로 남는다.\n"
            "# ▮ 가 이 글자다. ▪ 는 뱅크1 글자라 여기서는 안 보인다.\n"
            "# 문맥의 옆 글자도 틀렸을 수 있으니 여러 줄을 견준다.\n")
        for row in rows:
            handle.write(f"#\n#   {row['빈도']:,}회, 출처 {row['출처']}\n")
            for line in row["문맥"]:
                handle.write(f"#   {line}\n")
            handle.write(f"{row['인덱스']}\t{row['현재']}\n")
    print(f"판독지 {len(rows)}칸 → {args.worksheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
