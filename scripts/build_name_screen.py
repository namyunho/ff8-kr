#!/usr/bin/env python3
"""이름 입력 화면을 한국어로 짠다. 글자판과 기본 이름과 표기 통일까지.

## 세 가지를 한다

1. **기본 이름** — 이름을 붙일 수 있는 21개 자리(메뉴 서브1 그룹1)에 정본 표기를
   넣는다. `data/nameable-entities.json` 이 정본이다
2. **글자판** — 그 이름들을 칠 수 있는 음절을 순서대로 깐다
3. **표기 통일** — 이미 번역된 곳의 옛 표기를 정본으로 바꾼다

## 글자판 한 줄은 5바이트 고정이다

원본 36줄이 예외 없이 정확히 5바이트이고 빈칸은 `0x5f`(공백)다. 게임이 줄을
**바이트로 센다**는 뜻이므로 2바이트 글자를 놓을 수 없다. 그래서 배치
(`build_layout_all.py`)가 이름 음절을 1바이트 구간에 미리 넣어 둔다.

## 두 페이지에 똑같이 넣는다

탭(`カタカナ`/`ひらがな`)이 어느 페이지를 여는지는 코드를 읽어야 안다. 탭 차례와
줄 차례가 서로 반대라 헷갈리기 쉽다.

    탭   id0 カタカナ   id2 ひらがな
    줄   id54~88 히라가나면   id92~126 가타카나면

**양쪽에 같은 것을 깔면 그 문제가 사라진다.** 56자는 한 페이지 90칸에 들어가므로
어느 탭을 눌러도 같은 글자판이 나온다.

## 영문자 페이지는 건드리지 않는다

`id130~152` 는 못 박은 인덱스만 쓰므로 번역을 비워 두면 원본 바이트가 그대로
남고 화면에도 원래 글자가 나온다. **비워 두는 것이 곧 보존이다.**

    python3 scripts/build_name_screen.py --dry-run
    python3 scripts/build_name_screen.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MENU = Path("work/text/menu-messages.json")
NAMES = Path("data/nameable-entities.json")
FIELDS = Path("work/translate")

CELL = 5                                    # 한 칸에 5바이트
COLS, ROWS = 3, 6                           # 화면은 3열 x 6행 = 18칸
PAGE_IDS = {0: range(92, 128, 2), 2: range(54, 90, 2)}   # 탭 -> 그 면의 항목 id

# 옛 표기 -> 정본. 이미 번역된 곳을 맞춘다.
# 옛 표기 -> 정본. **정본은 data/nameable-entities.json 이다.**
# 한때 여기서 케찰코아틀->케차코틀 처럼 표기를 바꿨는데, 그것은 사용자가 고른
# 이름이 아니었다. 표기를 바꾸려면 이 표가 아니라 **정본 파일을 고친다** —
# 그래야 배치 생성기가 보호할 음절도 같이 바뀐다.
RESPELL = {
    "스콜": "스퀄", "안젤로": "앙겔로",
}


def syllables(document: dict) -> list[str]:
    """이름 차례대로 음절을 모은다. 중복은 처음 나온 자리에 둔다."""
    out: list[str] = []
    for name in document["order"]:
        for char in document["korean"][name]:
            if char not in out:
                out.append(char)
    return out


def by_column(cells: list[str]) -> list[str]:
    """열 우선으로 적은 18칸을 **행 우선** 데이터 차례로 옮긴다.

    화면은 3열 x 6행이고 데이터는 행 우선이다 — `id54, 56, 58` 이 첫 행의 세
    열이다. 원본 히라가나면을 그렇게 놓으면 첫 열이 세로로 `あ か さ た な は`
    가 되어 오십음순과 맞는다. 이것이 행 우선의 근거다.

    이름은 세로로 읽혀야 하므로 열 우선으로 적고 여기서 뒤집는다.

        적은 차례  0 1 2 3 4 5 | 6 7 8 …      (열을 따라 내려간다)
        데이터     0 6 12 | 1 7 13 | …        (행을 따라 간다)
    """
    out = []
    for row in range(ROWS):
        for col in range(COLS):
            index = col * ROWS + row
            text = cells[index] if index < len(cells) else ""
            out.append(text.ljust(CELL))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    document = json.loads(NAMES.read_text(encoding="utf-8"))
    menu = json.loads(MENU.read_text(encoding="utf-8"))
    index = {(r["sub"], r["group"], r["id"]): r for r in menu}

    pages = document["pages"]
    changed = 0
    # 기본 이름
    for slot, japanese in document["menu_slots"].items():
        row = index.get((1, 1, int(slot)))
        korean = document["japanese"][japanese]
        if row is None:
            print(f"  경고: 메뉴에 id{slot} 이 없다", file=sys.stderr)
            continue
        if row["ja"] != japanese:
            print(f"  경고: id{slot} 이 {row['ja']!r} 다. {japanese!r} 여야 한다",
                  file=sys.stderr)
            continue
        if row.get("ko") != korean:
            row["ko"] = korean
            changed += 1
    # 글자판 두 면
    for key in ("character", "gf"):
        page = pages[key]
        cells = page["cells"]
        long = [c for c in cells if len(c) > CELL]
        if long:
            print(f"{key}: 한 칸 {CELL}자를 넘는다 {long}", file=sys.stderr)
            return 1
        lines = by_column(cells)
        ids = list(PAGE_IDS[page["tab"]])
        print(f"\n{key}  탭 id{page['tab']}  항목 {ids[0]}~{ids[-1]}")
        for r in range(ROWS):
            print("    " + "  ".join(f"{lines[r * COLS + c]!r}"
                                     for c in range(COLS)))
        for line, slot in zip(lines, ids):
            row = index.get((1, 1, slot))
            if row is None:
                continue
            if row.get("ko") != line:
                row["ko"] = line
                changed += 1
    # 탭 이름
    for slot, label in pages["tabs"].items():
        row = index.get((1, 1, int(slot)))
        if row is not None and row.get("ko") != label:
            row["ko"] = label
            changed += 1

    print(f"\n메뉴에서 바꾼 항목 {changed}건")

    # 표기 통일
    respelled = 0
    files: list[tuple[Path, dict]] = []
    for path in sorted(FIELDS.glob("*.json")):
        if path.name == "manifest.json":
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        hit = False
        for entry in doc.get("entries", []):
            korean = entry.get("ko", "")
            for old, new in RESPELL.items():
                if old in korean:
                    korean = korean.replace(old, new)
                    respelled += 1
                    hit = True
            entry["ko"] = korean
        if hit:
            files.append((path, doc))
    for row in menu:
        korean = row.get("ko", "")
        for old, new in RESPELL.items():
            if old in korean:
                korean = korean.replace(old, new)
                respelled += 1
        row["ko"] = korean
    print(f"표기를 정본으로 바꾼 곳 {respelled}건")

    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0
    MENU.write_text(json.dumps(menu, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    for path, doc in files:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"→ {MENU} 외 {len(files)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
