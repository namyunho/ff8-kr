#!/usr/bin/env python3
"""필드 전용 폰트(뱅크1)의 글리프 ↔ 문자 대응표를 만든다.

뱅크1 은 필드마다 내용이 다르다. **같은 인덱스가 필드마다 다른 글자**이므로
인덱스로 표를 만들 수 없다. 대신 **비트맵 자체를 키로 삼는다.** 같은 글자는
어느 필드에 실려도 비트맵이 같아, 8,346회 등장이 820개 도형으로 줄어든다.

키는 12x12 셀 값을 그대로 해싱한 값의 앞 12자리다. 인덱스가 아니라 내용이므로
필드가 달라도 그대로 통한다.

뱅크0 과 마찬가지로 자동으로 알아낼 방법이 없어 두 단계로 나눈다.

1. `--sheets` 로 판독용 시트를 낸다. 자주 쓰이는 도형부터 앞에 놓는다.
2. 사람이 읽어 `키<탭>문자` 로 적어 주면 `--ingest` 로 받는다.

    python3 scripts/build_bank1_map.py --scan
    python3 scripts/build_bank1_map.py --sheets work/bank1
    python3 scripts/build_bank1_map.py --ingest data/glyph-sheets/bank1-000.tsv
    python3 scripts/build_bank1_map.py --status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_text_db as DB                  # noqa: E402
import extract_field_text as FT             # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = PROJECT_ROOT / "data" / "glyph-map-bank1.json"
CACHE_PATH = PROJECT_ROOT / "work" / "bank1-shapes.json"

KEY_LENGTH = 12
BANK1 = 0x400
SHEET_COLUMNS = 8
SHEET_ROWS = 6
ZOOM = 12
LABEL = 14
PAD = 8
INK = {0: None, 1: (70, 70, 70), 2: (150, 150, 150), 3: (255, 255, 255)}


def shape_key(cells: list[list[int]]) -> str:
    raw = bytes(value for row in cells for value in row)
    return hashlib.md5(raw).hexdigest()[:KEY_LENGTH]


def pack(cells: list[list[int]]) -> str:
    return bytes(value for row in cells for value in row).hex()


def unpack(packed: str) -> list[list[int]]:
    raw = bytes.fromhex(packed)
    return [list(raw[y * FT.CELL:(y + 1) * FT.CELL]) for y in range(FT.CELL)]


def contexts(msd: bytes, glyphs) -> dict[int, list[str]]:
    """뱅크1 슬롯이 실제로 쓰인 자리의 앞뒤 글자. 글리프만 보는 것보다 정확하다."""
    import glyph_text as GT

    found: dict[int, list[str]] = {}
    for offset in FT.message_offsets(msd):
        tokens = [t for t in FT.tokenize(msd, offset) if t[0] == "g"]
        for position, token in enumerate(tokens):
            if not token[1] & BANK1:
                continue
            slot = token[1] & ~BANK1
            if len(found.get(slot, ())) >= 3:
                continue
            window = tokens[max(0, position - 5):position + 6]
            text = "".join(
                "▮" if other is token else
                ("▪" if other[1] & BANK1 else glyphs.char.get(other[1], "?"))
                for other in window)
            found.setdefault(slot, []).append(text)
    return found


def scan() -> dict:
    """모든 필드의 뱅크1 글리프를 모아 비트맵으로 묶는다."""
    import glyph_text as GT

    glyphs = GT.GlyphMap.load()
    shapes: dict[str, dict] = {}
    fields = missing = 0
    for index, (lba, size) in enumerate(FT.field_list()):
        try:
            dat = FT.load_entry(index)
        except Exception:
            continue
        if DB.dat_pointers(dat) is None:
            continue
        msd = FT.msd_section(dat)
        if len(msd) < 4:
            continue
        counts: dict[int, int] = {}
        for offset in FT.message_offsets(msd):
            for token in FT.tokenize(msd, offset):
                if token[0] == "g" and token[1] & BANK1:
                    slot = token[1] & ~BANK1
                    counts[slot] = counts.get(slot, 0) + 1
        if not counts:
            continue
        fields += 1
        try:
            font = FT.field_font(FT.load_entry(index - 1))
        except Exception:
            font = None
        if font is None:
            missing += 1
            continue
        samples = contexts(msd, glyphs)
        for slot, count in counts.items():
            cells = font.glyph(slot)
            key = shape_key(cells)
            entry = shapes.setdefault(key, {"cells": pack(cells), "count": 0,
                                            "fields": [], "context": []})
            entry["count"] += count
            if index not in entry["fields"]:
                entry["fields"].append(index)
            for text in samples.get(slot, ()):
                if text not in entry["context"] and len(entry["context"]) < 6:
                    entry["context"].append(text)
    return {"fields": fields, "missing_tdw": missing, "shapes": shapes}


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        print(f"먼저 --scan 을 돌려 {CACHE_PATH} 를 만든다.", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def load_map() -> dict:
    if MAP_PATH.exists():
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return {"bank": 1, "note": "키는 12x12 셀 값의 해시 앞 12자리다. "
                               "뱅크1 인덱스는 필드마다 뜻이 달라 키가 될 수 없다.",
            "entries": {}}


def save_map(document: dict) -> None:
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def ordered(cache: dict) -> list[tuple[str, dict]]:
    """자주 쓰이는 도형부터. 먼저 읽을수록 이득이 크다."""
    return sorted(cache["shapes"].items(),
                  key=lambda item: (-item[1]["count"], item[0]))


def render_sheets(cache: dict, known: dict, out: Path) -> None:
    from PIL import Image, ImageDraw

    todo = [(key, entry) for key, entry in ordered(cache) if key not in known]
    cell = FT.CELL * ZOOM
    step_x, step_y = cell + PAD, cell + LABEL * 2 + PAD
    per_page = SHEET_COLUMNS * SHEET_ROWS
    out.mkdir(parents=True, exist_ok=True)
    for number, start in enumerate(range(0, len(todo), per_page)):
        page = todo[start:start + per_page]
        rows = (len(page) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
        image = Image.new("RGB", (PAD + SHEET_COLUMNS * step_x,
                                  PAD + rows * step_y), (0, 0, 0))
        pixels, draw = image.load(), ImageDraw.Draw(image)
        for slot, (key, entry) in enumerate(page):
            column, row = slot % SHEET_COLUMNS, slot // SHEET_COLUMNS
            left = PAD + column * step_x
            top = PAD + row * step_y + LABEL * 2
            draw.text((left, top - LABEL * 2), key, fill=(120, 200, 255))
            draw.text((left, top - LABEL), f"{entry['count']}회", fill=(120, 140, 170))
            for y, line in enumerate(unpack(entry["cells"])):
                for x, value in enumerate(line):
                    colour = INK[value]
                    if colour is None:
                        continue
                    for zy in range(ZOOM):
                        for zx in range(ZOOM):
                            pixels[left + x * ZOOM + zx, top + y * ZOOM + zy] = colour
        path = out / f"bank1-{start:03d}.png"
        image.save(path)
        print(f"  {path}  {len(page)}개")
    print(f"시트 {(len(todo) + per_page - 1) // per_page}장, 미판독 {len(todo)}개")


def ingest(document: dict, cache: dict, path: Path) -> tuple[int, list[str]]:
    added, problems = 0, []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t") if "\t" in line else line.split(None, 1)
        if len(parts) != 2:
            problems.append(f"{path.name}:{number} 형식이 아니다: {line!r}")
            continue
        key, char = parts[0].strip(), parts[1].strip()
        if key not in cache["shapes"]:
            problems.append(f"{path.name}:{number} 없는 키: {key}")
            continue
        if not char:
            continue
        document["entries"][key] = {"char": char, "source": "read"}
        added += 1
    return added, problems


def status(document: dict, cache: dict) -> None:
    shapes = cache["shapes"]
    known = document["entries"]
    total = sum(entry["count"] for entry in shapes.values())
    covered = sum(entry["count"] for key, entry in shapes.items() if key in known)
    print(f"대응표 {MAP_PATH}")
    print(f"  도형 {len(known)} / {len(shapes)} 판독")
    print(f"  등장 횟수 기준 {covered:,} / {total:,} = {covered / total:.1%}")
    todo = [(key, entry) for key, entry in ordered(cache) if key not in known]
    if todo:
        head = ", ".join(f"{key}({entry['count']}회)" for key, entry in todo[:5])
        print(f"  가장 자주 쓰이는 미판독: {head}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scan", action="store_true",
                        help="필드를 훑어 뱅크1 도형을 모은다")
    parser.add_argument("--sheets", type=Path, help="판독용 시트를 낸다")
    parser.add_argument("--ingest", type=Path, nargs="*",
                        help="`키<탭>문자` 파일을 합친다")
    parser.add_argument("--identify", nargs="*", metavar="필드:슬롯=문자",
                        help="원문을 아는 자리를 바로 등록한다. 예 296:2=痛")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--template", action="store_true",
                        help="문맥을 곁들인 판독용 TSV 뼈대를 낸다")
    parser.add_argument("--template-count", type=int, default=48,
                        help="뼈대에 담을 도형 수")
    args = parser.parse_args()

    if args.scan:
        cache = scan()
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"뱅크1 을 쓰는 필드 {cache['fields']}개 "
              f"(TDW 없음 {cache['missing_tdw']}개)")
        print(f"서로 다른 도형 {len(cache['shapes'])}개 → {CACHE_PATH}")
        return 0

    document = load_map()
    cache = load_cache()

    if args.identify:
        added = 0
        for item in args.identify:
            place, _, char = item.partition("=")
            field, _, slot = place.partition(":")
            font = FT.field_font(FT.load_entry(int(field) - 1))
            if font is None:
                print(f"  경고: 필드 {field} 에 TDW 가 없다")
                continue
            key = shape_key(font.glyph(int(slot)))
            if key not in cache["shapes"]:
                print(f"  경고: {item} 의 도형이 목록에 없다 ({key})")
                continue
            before = document["entries"].get(key, {}).get("char")
            if before and before != char:
                print(f"  경고: {item} 는 이미 {before!r} 로 읽혀 있다. 덮어쓴다")
            document["entries"][key] = {"char": char, "source": "original"}
            print(f"  {item}  → {key}")
            added += 1
        save_map(document)
        print(f"원문 대조로 {added}건을 등록했다")

    if args.sheets:
        render_sheets(cache, document["entries"], args.sheets)
    if args.ingest:
        total = 0
        for path in args.ingest:
            added, problems = ingest(document, cache, path)
            total += added
            for problem in problems:
                print(f"  경고: {problem}")
        save_map(document)
        print(f"판독값 {total}건을 합쳤다")
    if args.template:
        todo = [(key, entry) for key, entry in ordered(cache)
                if key not in document["entries"]]
        lines = ["# 뱅크1 판독. `키<탭>문자` 로 적는다. 확신이 없으면 비워 둔다.",
                 "# ▮ 가 이 글자이고 ▪ 는 아직 못 읽은 다른 뱅크1 글자다."]
        for key, entry in todo[:args.template_count]:
            for text in entry["context"][:3]:
                lines.append(f"#   {text}")
            lines.append(f"#   {entry['count']}회, 필드 {len(entry['fields'])}개")
            lines.append(f"{key}\t")
        print("\n".join(lines))
        return 0

    if args.status or not (args.sheets or args.ingest):
        status(document, cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
