#!/usr/bin/env python3
"""번역문이 게임에 들어갈 수 있는지 검사한다. 기계 번역의 실수를 잡는 그물이다.

기계 번역이 저지르는 실수는 정해져 있다. **제어 코드를 빠뜨리거나 순서를 바꾸고,
줄을 더 늘리고, 창을 넘긴다.** 사람이 눈으로 잡을 수 없는 양이므로 기계로 건다.

여섯 가지를 본다.

    코드 누락    `ja` 에 있던 제어 코드가 `ko` 에 없다
    코드 순서    있기는 한데 순서가 다르다
    줄 수        `{02}` 개수가 늘었다
    바이트       원본 예산을 넘겼다
    줄 폭        한 줄이 창 폭을 넘겼다
    없는 글자    원본 폰트에도, 넣을 한글 배치에도 없다

한글 글리프의 폭과 인덱스는 **우리가 정한다.** 아직 폰트를 만들지 않았으므로
기본값은 보수적으로 잡는다. 음절 하나에 12픽셀, 2바이트다. `--layout` 으로
배치를 주면 앞 224자는 1바이트로 계산한다.

    python3 scripts/check_translation.py work/translate
    python3 scripts/check_translation.py work/translate --layout work/hangul-layout.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogue_editor as DE                # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402

TOKEN = re.compile(r"\{[^}]*\}")
BREAK = "{02}"
SINGLE_BYTE = 224
DEFAULT_WIDTH = 12                          # 한글 한 칸. 우리가 정하는 값이다
DEFAULT_LINE_PIXELS = DE.DEFAULT_LINE_PIXELS


def codes(text: str) -> list[str]:
    """제어 코드만 순서대로. `{b1:N}` 은 원문 글자라 코드가 아니다."""
    return [token for token in TOKEN.findall(text)
            if not token.startswith("{b1:")]


def cost(text: str, widths: list[int], layout: list[str] | None,
         glyphs: GT.GlyphMap) -> tuple[int, list[int], set[str]]:
    """번역문의 바이트, 줄별 픽셀, 넣을 수 없는 글자."""
    cheap = set(layout[:SINGLE_BYTE]) if layout else set()
    known = set(layout) if layout else None
    total = 0
    pixels = []
    unknown: set[str] = set()
    for token in codes(text):
        total += 2 if ":" in token else 1
    for line in text.split(BREAK):
        plain = TOKEN.sub("", line)
        width = 0
        for char in plain:
            index = glyphs.index.get(char)
            if index is not None:
                total += 1 if index < SINGLE_BYTE else 2
                width += (widths[index] if index < len(widths)
                          else DE.FALLBACK_WIDTH)
                continue
            if known is not None and char not in known:
                unknown.add(char)
            total += 1 if char in cheap else 2
            width += DEFAULT_WIDTH
        pixels.append(width)
    return total, pixels, unknown


def check(root: Path, layout_path: Path | None, line_limit: int) -> int:
    glyphs = GT.GlyphMap.load()
    widths = DE.glyph_widths(FT.SYSFNT.read_bytes())
    layout = None
    if layout_path:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))["chars"]

    problems = collections.Counter()
    worst: dict[str, list[str]] = collections.defaultdict(list)
    budgets: list[tuple[str, int, int, int]] = []
    checked = 0
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        field_used = field_budget = field_done = 0
        for entry in document["entries"]:
            text = entry.get("ko") or ""
            if not text.strip():
                continue
            checked += 1
            where = f"{document['name']}#{entry['id']}"
            want, got = codes(entry["ja"]), codes(text)
            if collections.Counter(want) != collections.Counter(got):
                problems["코드 누락·추가"] += 1
                worst["코드 누락·추가"].append(
                    f"{where} 원본 {want} → 번역 {got}")
            elif want != got:
                problems["코드 순서"] += 1
                worst["코드 순서"].append(where)

            used, pixels, unknown = cost(text, widths, layout, glyphs)
            if len(pixels) > max(entry["lines"], 1):
                problems["줄 수 초과"] += 1
                worst["줄 수 초과"].append(
                    f"{where} {entry['lines']} → {len(pixels)}")
            field_used += used
            field_budget += entry["byte_budget"]
            field_done += 1
            if used > entry["byte_budget"]:
                problems["메시지 예산 초과(참고)"] += 1
                worst["메시지 예산 초과(참고)"].append(
                    f"{where} 예산 {entry['byte_budget']} → {used}")
            over = [value for value in pixels if value > line_limit]
            if over:
                problems["줄 폭 초과"] += 1
                worst["줄 폭 초과"].append(f"{where} {max(over)}px")
            if unknown:
                problems["없는 글자"] += 1
                worst["없는 글자"].append(f"{where} {''.join(sorted(unknown))}")

        if field_done:
            budgets.append((document["name"], field_budget, field_used,
                            field_done))

    print(f"번역된 항목 {checked:,}건 검사")
    if budgets:
        over = [row for row in budgets if row[2] > row[1]]
        print(f"\n필드 바이트 총량 — 섹터 적합의 실제 기준")
        print(f"  검사한 필드 {len(budgets)}개 중 원본 총량을 넘긴 필드 {len(over)}개")
        for name, budget, used, done in sorted(
                over, key=lambda r: r[1] - r[2])[:5]:
            print(f"    {name}: {budget:,}B → {used:,}B "
                  f"(+{used - budget:,}, {done}건 번역됨)")
    if not checked:
        print("`ko` 가 비어 있다. 번역을 넣고 다시 돌린다.")
        return 0
    if not problems:
        print("문제 없음.")
        return 0
    for kind, count in problems.most_common():
        print(f"\n{kind}: {count:,}건")
        for line in worst[kind][:5]:
            print(f"    {line}")
        if len(worst[kind]) > 5:
            print(f"    … 외 {len(worst[kind]) - 5:,}건")
    print("\n메시지 단위 예산 초과는 그 자체로 실패가 아니다. 필드 전체가 배정"
          " 섹터에 들어가면 된다.")
    print("바이트·줄 폭은 한글 배치를 정하기 전 보수적 추정이다."
          " --layout 을 주면 정확해진다.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="번역 내보내기 디렉터리")
    parser.add_argument("--layout", type=Path, help="한글 배치 JSON")
    parser.add_argument("--line-pixels", type=int, default=DEFAULT_LINE_PIXELS,
                        help=f"줄 폭 상한 (기본 {DEFAULT_LINE_PIXELS})")
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"디렉터리가 아니다: {args.root}", file=sys.stderr)
        return 2
    return check(args.root, args.layout, args.line_pixels)


if __name__ == "__main__":
    raise SystemExit(main())
