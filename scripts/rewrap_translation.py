#!/usr/bin/env python3
"""번역문을 원본과 같은 줄 구조로 다시 접는다. 조판 문제만 고친다.

기계 번역이 줄을 하나 더 넣거나 빼는 일이 잦다. 글 내용은 맞는데 `{02}` 개수만
어긋나는 것이라 **다시 번역할 필요가 없다.** 같은 글을 원본과 같은 줄 수로
다시 접으면 된다.

줄바꿈 말고 다른 제어 코드가 어긋나 있으면 손대지 않는다. 그건 조판이 아니라
내용이 빠지거나 더해진 것이고, 기계가 고칠 수 있는 종류가 아니다.

접는 방식은 **균형 분할**이다. 낱말을 N줄에 나누되 가장 넓은 줄이 가장 좁아지게
한다. 앞줄부터 채우면 마지막 줄만 짧아져 창을 넘기기 쉽다.

    python3 scripts/rewrap_translation.py work/translate
    python3 scripts/rewrap_translation.py work/translate --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogue_editor as DE                # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import text_metrics as TM                   # noqa: E402

BREAK = "{02}"


def anchors(text: str) -> list[str]:
    """줄바꿈이 아닌 제어 코드만. 이게 같아야 조판으로 고칠 수 있다."""
    return [token for token in TM.control_codes(text) if token != BREAK]


def segments(text: str) -> list[str]:
    """줄바꿈 아닌 코드를 경계로 자른다. 코드는 버리고 사이 글만 남긴다."""
    out, rest = [], text
    for token in anchors(text):
        head, _, rest = rest.partition(token)
        out.append(head)
    out.append(rest)
    return out


def balanced(words: list[str], lines: int, width) -> list[str] | None:
    """낱말을 정확히 `lines` 줄로 나눈다. 가장 넓은 줄을 가장 좁게.

    낱말 수가 줄 수보다 적으면 나눌 수 없다 — 그때는 손대지 않는다.
    """
    if lines <= 1:
        return [" ".join(words)]
    if len(words) < lines:
        return None

    # cost[i][k] = words[i:] 를 k 줄로 나눌 때의 최소 최대폭
    total = len(words)
    best: dict[tuple[int, int], tuple[int, int]] = {}

    def solve(start: int, left: int) -> tuple[int, int]:
        if (start, left) in best:
            return best[(start, left)]
        if left == 1:
            answer = (width(" ".join(words[start:])), total)
        else:
            answer = (1 << 30, start + 1)
            for cut in range(start + 1, total - left + 2):
                here = width(" ".join(words[start:cut]))
                deeper = solve(cut, left - 1)[0]
                worst = max(here, deeper)
                if worst < answer[0]:
                    answer = (worst, cut)
        best[(start, left)] = answer
        return answer

    out, position, left = [], 0, lines
    while left:
        cut = solve(position, left)[1]
        out.append(" ".join(words[position:cut] if left > 1
                            else words[position:]))
        position, left = cut, left - 1
    return out


def rewrap(root: Path, dry_run: bool, limit: int) -> int:
    glyphs = GT.GlyphMap.load()
    widths = DE.glyph_widths(FT.SYSFNT.read_bytes())

    def width(run: str) -> int:
        return sum(TM.glyph_width(char, widths, glyphs.index.get(char))
                   if not char.startswith("{") else TM.FALLBACK_WIDTH
                   for char in TM.drawn(run))

    fixed = skipped = wide = 0
    reasons: list[str] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        dirty = False
        for entry in document["entries"]:
            source, target = entry["ja"], entry.get("ko") or ""
            if not target.strip():
                continue
            where = f"{document['name']}#{entry['id']}"
            if TM.control_codes(source) == TM.control_codes(target):
                continue
            if anchors(source) != anchors(target):
                skipped += 1
                reasons.append(f"{where} 줄바꿈 말고 다른 코드가 다르다 —"
                               f" 내용 문제다")
                continue

            pieces, ok = [], True
            for want, have in zip(segments(source), segments(target)):
                lines = want.count(BREAK) + 1
                words = " ".join(have.split(BREAK)).split()
                folded = balanced(words, lines, width) if words else None
                if folded is None:
                    ok = False
                    break
                pieces.append(BREAK.join(folded))
            if not ok:
                skipped += 1
                reasons.append(f"{where} 낱말이 줄 수보다 적어 못 나눈다")
                continue

            rebuilt = pieces[0]
            for token, piece in zip(anchors(source), pieces[1:]):
                rebuilt += token + piece
            if TM.control_codes(rebuilt) != TM.control_codes(source):
                skipped += 1
                reasons.append(f"{where} 다시 접었는데도 코드가 안 맞는다")
                continue

            over = [value for value in TM.line_pixels(rebuilt, widths,
                                                      glyphs.index.get)
                    if value > max(limit, entry["line_pixels"])]
            if over:
                wide += 1
                reasons.append(f"{where} 접어도 {max(over)}px — 문장을 줄여야 한다")
            entry["ko"] = rebuilt
            fixed += 1
            dirty = True
        if dirty and not dry_run:
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8")

    print(f"다시 접은 메시지 {fixed}건"
          + (" (시험 실행, 쓰지 않음)" if dry_run else ""))
    print(f"  그중 접어도 창을 넘는 것 {wide}건 — 문장을 줄여야 한다")
    print(f"  손대지 않은 것 {skipped}건 — 조판이 아니라 내용 문제다")
    for line in reasons[:12]:
        print(f"    {line}")
    if len(reasons) > 12:
        print(f"    … 외 {len(reasons) - 12}건")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="번역 워크시트 디렉터리")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--line-pixels", type=int, default=TM.LINE_PIXELS)
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"디렉터리가 아니다: {args.root}", file=sys.stderr)
        return 2
    return rewrap(args.root, args.dry_run, args.line_pixels)


if __name__ == "__main__":
    raise SystemExit(main())
