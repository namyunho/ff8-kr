#!/usr/bin/env python3
"""원본 폰트의 글리프 인덱스 ↔ 문자 대응표를 만든다.

단일바이트 영역(0..223)은 `docs/font-analysis.md` 에서 확정했지만 224 이상
한자 영역은 미확정이다. 이 표가 없으면 MSD 를 기계로 읽을 수 없다.

자동으로 알아낼 방법이 없으므로 두 단계로 나눈다.

1. `--sheets` 로 인덱스를 붙인 판독용 시트를 낸다. 12x12 글리프를 확대해
   격자에 배치하고 각 칸에 인덱스를 적는다.
2. 사람이 읽어 `인덱스<탭>문자` 형식으로 적어 주면 `--ingest` 로 받아
   대응표 JSON 에 합친다.

**판독은 추정이다.** 확정된 항목과 구분하기 위해 출처를 함께 기록한다.
`confirmed` 는 문서에서 이미 검증된 값, `read` 는 시트 판독값이다.

    python3 scripts/build_glyph_map.py --sheets work/glyphmap
    python3 scripts/build_glyph_map.py --ingest work/glyphmap/sheet-224.tsv
    python3 scripts/build_glyph_map.py --status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 손으로 읽은 결과라 기계적으로 재생성되지 않는다. work/ 가 아니라 추적
# 경로에 둔다. 판독용 시트는 재생성되므로 work/ 에 남긴다.
MAP_PATH = PROJECT_ROOT / "data" / "glyph-map-bank0.json"

BANK0_GLYPHS = 882          # 441칸 x 2 인터리브
SHEET_COLUMNS = 16
SHEET_ROWS = 8
SHEET_SIZE = SHEET_COLUMNS * SHEET_ROWS     # 시트당 128글리프
ZOOM = 4                    # 12x12 를 48x48 로 키운다
LABEL_HEIGHT = 12
PAD = 6

# 문서에서 이미 검증된 값. 시트 판독의 기준점으로도 쓴다.
CONFIRMED = {
    74: "カ", 76: "キ", 78: "ク", 80: "ケ", 82: "コ",
    38: "ド", 150: "ワ", 138: "ル", 106: "ア", 136: "リ",
    114: "ナ", 128: "ム", 14: "グ", 223: "ー",
}


def load_map() -> dict:
    if MAP_PATH.exists():
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return {"bank": 0, "entries": {}}


def save_map(document: dict) -> None:
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def seed_confirmed(document: dict) -> int:
    added = 0
    for index, char in CONFIRMED.items():
        key = str(index)
        if document["entries"].get(key, {}).get("char") != char:
            document["entries"][key] = {"char": char, "source": "confirmed"}
            added += 1
    return added


def render_sheet(font: FT.Font, start: int, count: int, path: Path) -> None:
    """인덱스를 붙인 판독용 시트 한 장."""
    from PIL import Image, ImageDraw

    cell = FT.CELL * ZOOM
    step_x = cell + PAD
    step_y = cell + LABEL_HEIGHT + PAD
    rows = (count + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    width = PAD + SHEET_COLUMNS * step_x
    height = PAD + rows * step_y

    image = Image.new("RGB", (width, height), (14, 14, 20))
    pixels = image.load()
    draw = ImageDraw.Draw(image)

    for slot in range(count):
        index = start + slot
        if index >= BANK0_GLYPHS:
            break
        column, row = slot % SHEET_COLUMNS, slot // SHEET_COLUMNS
        left = PAD + column * step_x
        top = PAD + row * step_y + LABEL_HEIGHT

        draw.text((left, top - LABEL_HEIGHT), str(index), fill=(110, 125, 150))
        cells = font.glyph(index)
        for y in range(FT.CELL):
            for x in range(FT.CELL):
                colour = FT.SHADE[cells[y][x]]
                if colour is None:
                    continue
                for zy in range(ZOOM):
                    for zx in range(ZOOM):
                        px, py = left + x * ZOOM + zx, top + y * ZOOM + zy
                        if 0 <= px < width and 0 <= py < height:
                            pixels[px, py] = colour

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def ingest(document: dict, path: Path) -> tuple[int, list[str]]:
    """`인덱스<탭>문자` 줄을 읽어 합친다. 빈 문자는 건너뛴다."""
    added = 0
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t") if "\t" in line else line.split(None, 1)
        if len(parts) != 2:
            problems.append(f"{path.name}:{number} 형식이 아니다: {line!r}")
            continue
        raw_index, char = parts[0].strip(), parts[1].strip()
        if not raw_index.isdigit():
            problems.append(f"{path.name}:{number} 인덱스가 숫자가 아니다: {raw_index!r}")
            continue
        index = int(raw_index)
        if not (0 <= index < BANK0_GLYPHS):
            problems.append(f"{path.name}:{number} 범위 밖: {index}")
            continue
        if not char:
            continue
        existing = document["entries"].get(str(index))
        if existing and existing["source"] == "confirmed" and existing["char"] != char:
            problems.append(
                f"{path.name}:{number} 확정값과 다르다: {index} "
                f"확정 {existing['char']!r} vs 판독 {char!r}")
            continue
        document["entries"][str(index)] = {"char": char, "source": "read"}
        added += 1
    return added, problems


def status(document: dict) -> None:
    entries = document["entries"]
    confirmed = sum(1 for v in entries.values() if v["source"] == "confirmed")
    read = sum(1 for v in entries.values() if v["source"] == "read")
    known = set(int(k) for k in entries)
    single = sum(1 for i in known if i < 224)
    kanji = sum(1 for i in known if i >= 224)
    print(f"대응표 {MAP_PATH}")
    print(f"  전체 {len(entries)} / {BANK0_GLYPHS}  "
          f"(확정 {confirmed}, 판독 {read})")
    print(f"  단일바이트 영역 0..223 : {single} / 224")
    print(f"  한자 영역 224..881     : {kanji} / {BANK0_GLYPHS - 224}")
    missing = [i for i in range(224, BANK0_GLYPHS) if i not in known]
    if missing:
        head = ", ".join(str(i) for i in missing[:12])
        print(f"  미판독 첫 구간: {head} ...")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheets", type=Path,
                        help="판독용 시트를 이 디렉터리에 낸다")
    parser.add_argument("--start", type=int, default=224,
                        help="시트 시작 인덱스 (기본 224 — 한자 영역)")
    parser.add_argument("--end", type=int, default=BANK0_GLYPHS)
    parser.add_argument("--ingest", type=Path, nargs="*",
                        help="`인덱스<탭>문자` 파일을 대응표에 합친다")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    document = load_map()
    seeded = seed_confirmed(document)
    if seeded:
        save_map(document)
        print(f"확정값 {seeded}건을 대응표에 넣었다")

    if args.sheets:
        font = FT.system_font()
        made = 0
        for start in range(args.start, min(args.end, BANK0_GLYPHS), SHEET_SIZE):
            count = min(SHEET_SIZE, min(args.end, BANK0_GLYPHS) - start)
            path = args.sheets / f"sheet-{start:03d}.png"
            render_sheet(font, start, count, path)
            made += 1
            print(f"  {path}  인덱스 {start}..{start + count - 1}")
        print(f"시트 {made}장")

    if args.ingest:
        total = 0
        for path in args.ingest:
            added, problems = ingest(document, path)
            total += added
            for problem in problems:
                print(f"  경고: {problem}")
        save_map(document)
        print(f"판독값 {total}건을 합쳤다")

    if args.status or not (args.sheets or args.ingest):
        status(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
