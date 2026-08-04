#!/usr/bin/env python3
"""완성형 글리프에서 자모를 역으로 뽑아 조합이 되는지 잰다.

뱅크0 은 882칸이고 번역문이 쓰는 음절은 1,080종이다. 칸이 모자란다.

렌더러가 폭 0 글리프를 같은 자리에 겹쳐 그리고(`sub_8002EE90` 의 `x += width`),
글리프 배경이 CLUT `0x0000` 이라 뒤엣것이 앞엣것을 지우지 않는다. 그러면
**자모를 겹쳐 음절을 만들 수 있고 882칸으로 완성형 전체를 덮는다.**

남은 의문은 **12×12 에서 조합한 글자가 읽히는가** 하나다. 이건 실기 없이
답이 나온다. 갈무리는 완성형 폰트이므로 **음절 비트맵에서 자모를 역으로 뽑아**
다시 겹쳐 원본과 대조하면 된다.

뽑는 방법은 교집합이다. 같은 초성·같은 중성유형을 쓰는 음절들의 **공통 픽셀**이
곧 그 초성이다. 중성·종성도 같은 방식으로 뽑는다. 남는 픽셀이 있으면 그만큼
조합이 원본과 어긋난 것이다.

    python3 scripts/jamo_compose.py
    python3 scripts/jamo_compose.py --render work/font/jamo-check.png
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402

BASE = 0xAC00
LEAD, VOWEL, TAIL = 19, 21, 28              # 초성 · 중성 · 종성(없음 포함)

# 중성은 세로기둥형(ㅏㅑㅓㅕㅣ), 가로기둥형(ㅗㅛㅜㅠㅡ), 섞임형으로 나뉘고
# 초성이 그에 따라 다른 자리에 온다. 조합형 폰트가 초성을 여러 벌 두는 이유다.
UPRIGHT = {0, 1, 2, 3, 4, 5, 6, 7, 20}      # ㅏㅐㅑㅒㅓㅔㅕㅖ ㅣ
WIDE = {8, 12, 13, 17, 18}                  # ㅗㅛㅜㅠㅡ


def vowel_kind(vowel: int) -> int:
    """초성이 앉는 자리를 가르는 분류. 0=세로기둥 1=가로기둥 2=섞임."""
    if vowel in UPRIGHT:
        return 0
    if vowel in WIDE:
        return 1
    return 2


def decompose(char: str) -> tuple[int, int, int]:
    code = ord(char) - BASE
    return (code // (VOWEL * TAIL), (code // TAIL) % VOWEL, code % TAIL)


def bitmap(cell: list[list[int]]) -> frozenset[tuple[int, int]]:
    return frozenset((x, y) for y, row in enumerate(cell)
                     for x, value in enumerate(row) if value)


def common(shapes: list[frozenset]) -> frozenset:
    """교집합. 그 자리에 늘 있는 픽셀이 곧 그 자모다."""
    out = shapes[0]
    for shape in shapes[1:]:
        out = out & shape
    return out


def build(cells: dict[str, list[list[int]]]) -> tuple[dict, dict]:
    """자모 조각을 뽑는다. 키는 (종류, 자모, 문맥)."""
    groups: dict[tuple, list[frozenset]] = collections.defaultdict(list)
    for char, cell in cells.items():
        lead, vowel, tail = decompose(char)
        shape = bitmap(cell)
        groups[("L", lead, vowel_kind(vowel), bool(tail))].append(shape)
        groups[("V", vowel, bool(tail))].append(shape)
        if tail:
            groups[("T", tail, vowel_kind(vowel))].append(shape)

    pieces = {key: common(shapes) for key, shapes in groups.items()}
    return pieces, groups


def check(pieces: dict, cells: dict[str, list[list[int]]]) -> dict:
    """조합 결과를 원본과 대조한다. 빠진 픽셀과 튄 픽셀을 따로 센다."""
    exact = 0
    misses = collections.Counter()
    worst: list[tuple[int, str]] = []
    for char, cell in cells.items():
        lead, vowel, tail = decompose(char)
        made = pieces[("L", lead, vowel_kind(vowel), bool(tail))]
        made = made | pieces[("V", vowel, bool(tail))]
        if tail:
            made = made | pieces[("T", tail, vowel_kind(vowel))]
        want = bitmap(cell)
        gap = len(want - made) + len(made - want)
        if gap == 0:
            exact += 1
        misses[gap] += 1
        worst.append((gap, char))
    worst.sort(reverse=True)
    return {"exact": exact, "total": len(cells), "misses": misses,
            "worst": worst[:12], "pieces": len(pieces)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ttf", type=Path, default=BF.DEFAULT_TTF)
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--chars", type=Path,
                        help="검사할 음절 목록 JSON (없으면 완성형 전체)")
    args = parser.parse_args()

    if args.chars:
        import json
        chars = json.loads(args.chars.read_text(encoding="utf-8"))["chars"]
        chars = [c for c in chars if BASE <= ord(c) <= 0xD7A3]
    else:
        chars = [chr(BASE + i) for i in range(LEAD * VOWEL * TAIL)]
    print(f"음절 {len(chars):,}자를 {args.size}px 로 래스터라이즈한다")

    cells = BF.rasterize(args.ttf, args.size, chars)
    pieces, _ = build(cells)
    result = check(pieces, cells)

    print()
    print(f"뽑아낸 자모 조각 : {result['pieces']:,}개")
    print(f"원본과 완전 일치 : {result['exact']:,} / {result['total']:,} "
          f"({result['exact'] / result['total']:.1%})")
    rows = sorted(result["misses"].items())
    print("어긋난 픽셀 수 분포:",
          ", ".join(f"{gap}px {count:,}자" for gap, count in rows[:8]))
    if result["worst"] and result["worst"][0][0]:
        head = " ".join(f"{char}({gap})" for gap, char in result["worst"][:10])
        print(f"가장 많이 어긋난 글자: {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
