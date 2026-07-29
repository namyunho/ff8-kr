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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogue_editor as DE                # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import text_metrics as TM                   # noqa: E402

DEFAULT_LINE_PIXELS = TM.LINE_PIXELS


def codes(text: str) -> list[str]:
    """제어 코드만 순서대로. `{b1:N}` 은 원문 글자라 코드가 아니다."""
    return TM.control_codes(text)


def cost(text: str, widths: list[int], layout: list[str] | None,
         glyphs: GT.GlyphMap) -> tuple[int, list[int], set[str]]:
    """번역문의 바이트, 줄별 픽셀, 넣을 수 없는 글자.

    한글 글리프는 아직 만들지 않았으므로 폭을 한 칸(12px)으로 잡는다.
    원본 폰트에 있는 글자는 실측 폭 테이블을 쓴다.
    """
    cheap = set(layout[:TM.SINGLE_BYTE]) if layout else set()
    known = set(layout) if layout else None

    def lookup(char: str) -> int | None:
        index = glyphs.index.get(char)
        if index is None and known is not None and char not in known:
            unknown.add(char)
        return index

    unknown: set[str] = set()
    total = TM.byte_cost(text, lookup, cheap)
    pixels = TM.line_pixels(text, widths, lookup)
    return total, pixels, unknown


def inspect(entry: dict, used: int, pixels: list[int], unknown: set[str],
            line_limit: int) -> list[tuple[str, str]]:
    """메시지 한 건의 문제 목록. `(종류, 설명)` 으로 준다."""
    text = entry["ko"]
    found: list[tuple[str, str]] = []

    want, got = codes(entry["ja"]), codes(text)
    if collections.Counter(want) != collections.Counter(got):
        found.append(("코드 누락·추가", f"원본 {want} → 번역 {got}"))
    elif want != got:
        found.append(("코드 순서", f"원본 {want} → 번역 {got}"))

    # `{b1:N}` 은 아직 못 읽은 원문 글자다. 번역문에 남아 있으면 그 필드
    # 전용 폰트의 엉뚱한 글리프가 그려진다.
    if TM.BANK1 in text:
        found.append(("구멍 남음", " ".join(TM.BANK1_TOKEN.findall(text))))

    lines = TM.line_count(text)
    if lines > max(entry["lines"], 1):
        found.append(("줄 수 초과", f"{entry['lines']}줄 → {lines}줄"))
    if used > entry["byte_budget"]:
        found.append(("메시지 예산 초과(참고)",
                      f"예산 {entry['byte_budget']}B → {used}B"))

    # 원본이 이미 상한을 넘는 줄이 3건 있다. 원본이 그렇게 그려지는 이상
    # 같은 폭까지는 번역도 안전하다고 본다.
    allowed = max(line_limit, entry["line_pixels"])
    over = [value for value in pixels if value > allowed]
    if over:
        found.append(("줄 폭 초과", f"{max(over)}px > {allowed}px"))
    if unknown:
        found.append(("없는 글자", "".join(sorted(unknown))))
    return found


def check(root: Path, layout_path: Path | None, line_limit: int,
          report: Path | None = None) -> int:
    glyphs = GT.GlyphMap.load()
    widths = DE.glyph_widths(FT.SYSFNT.read_bytes())
    layout = None
    if layout_path:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))["chars"]

    problems = collections.Counter()
    worst: dict[str, list[str]] = collections.defaultdict(list)
    budgets: list[tuple[str, int, int, int]] = []
    findings: list[dict] = []
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
            used, pixels, unknown = cost(text, widths, layout, glyphs)
            field_used += used
            field_budget += entry["byte_budget"]
            field_done += 1
            for kind, detail in inspect(entry, used, pixels, unknown,
                                        line_limit):
                problems[kind] += 1
                worst[kind].append(f"{where} {detail}")
                findings.append({"file": path.name, "field": document["field"],
                                 "name": document["name"], "id": entry["id"],
                                 "kind": kind, "detail": detail,
                                 "ja": entry["ja"], "ko": text})

        if field_done:
            budgets.append((document["name"], field_budget, field_used,
                            field_done))

    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(
            {"checked": checked, "problems": dict(problems.most_common()),
             "findings": findings}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"검사 보고서 {report}")

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
    parser.add_argument("--report", type=Path,
                        help="문제를 건별로 적은 JSON. 고칠 자리를 기계로 돌린다")
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"디렉터리가 아니다: {args.root}", file=sys.stderr)
        return 2
    return check(args.root, args.layout, args.line_pixels, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
