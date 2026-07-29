#!/usr/bin/env python3
"""번역문이 요구하는 한글 음절 수를 센다. M5 의 마지막 전제를 판정한다.

뱅크0 은 882칸이다. 그중 숫자·라틴·부호에 내줘야 하는 자리를 빼면 한글이 쓸 수
있는 칸이 나오고, 번역문이 쓰는 **서로 다른 음절 수**가 그 안에 들어가야 한다.
이 판정은 번역이 있어야만 할 수 있다. 번역이 진행되는 중에도 돌려 추세를 본다.

인덱스 `0..223` 은 1바이트, `224` 이상은 2바이트로 실린다. 빈도 상위 음절을
앞자리에 놓으면 본문이 작아지므로 배치 후보도 함께 낸다.

    python3 scripts/count_korean_syllables.py work/translate
    python3 scripts/count_korean_syllables.py work/translate --layout work/hangul-layout.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                     # noqa: E402

TOKEN = re.compile(r"\{[^}]*\}")
HANGUL = re.compile(r"[가-힣]")
BANK0_SLOTS = 882
SINGLE_BYTE = 224


def collect(root: Path) -> tuple[collections.Counter, dict]:
    """번역된 `ko` 만 모은다. 제어 코드는 글자가 아니므로 뺀다."""
    syllables = collections.Counter()
    other = collections.Counter()
    texts: list[str] = []
    stats = {"files": 0, "entries": 0, "translated": 0}
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        stats["files"] += 1
        for entry in document["entries"]:
            stats["entries"] += 1
            text = entry.get("ko") or ""
            if not text.strip():
                continue
            stats["translated"] += 1
            plain = TOKEN.sub("", text)
            texts.append("".join(c for c in plain if HANGUL.match(c)))
            for char in plain:
                if HANGUL.match(char):
                    syllables[char] += 1
                elif not char.isspace():
                    other[char] += 1
    return syllables, {"stats": stats, "other": other, "texts": texts}


def report(root: Path, layout: Path | None) -> int:
    syllables, extra = collect(root)
    stats = extra["stats"]
    other = extra["other"]
    texts = extra["texts"]

    print(f"파일 {stats['files']}개 / 항목 {stats['entries']:,}건 "
          f"중 번역됨 {stats['translated']:,}건")
    if not stats["translated"]:
        print("번역된 항목이 없다. `ko` 를 채운 뒤 다시 돌린다.")
        return 0
    if stats["translated"] < stats["entries"]:
        share = stats["translated"] / stats["entries"]
        print(f"  **{share:.1%} 만 번역된 중간 집계다.** 음절 수는 더 늘어난다.")

    glyphs = GT.GlyphMap.load()
    keep = {char for char in other if char in glyphs.index}
    unknown = {char: count for char, count in other.items()
               if char not in glyphs.index}

    total = sum(syllables.values())
    need = len(syllables) + len(keep) + len(unknown)
    print()
    print(f"서로 다른 한글 음절 : {len(syllables):,}")
    print(f"한글이 아닌 글자     : {len(keep)}종(원본 폰트에 있음) "
          f"+ {len(unknown)}종(없음)")
    print(f"필요한 칸 합계       : {need:,} / {BANK0_SLOTS}")
    print("  →", "들어간다" if need <= BANK0_SLOTS
          else f"모자란다. {need - BANK0_SLOTS}칸 초과")

    # 한글이 아닌 글자도 칸을 먹는다. 칸이 모자랄 때 무엇을 버릴지 정하려면
    # 어떤 글자를 몇 번 쓰는지가 보여야 한다. 한 번만 쓰인 부호는 버리는 값이
    # 크지 않으므로 먼저 눈에 띄게 한다.
    if other:
        rows = sorted(other.items(), key=lambda row: -row[1])
        shown = " ".join(f"{char}({count:,})" for char, count in rows[:24])
        print(f"  쓰는 부호·숫자·라틴 : {shown}")
        rare = [char for char, count in rows if count <= 2]
        if rare:
            print(f"  두 번 이하만 쓰인 글자 {len(rare)}종: {''.join(rare)}")
    if unknown:
        head = "".join(sorted(unknown))[:40]
        print(f"  **폰트에 없는 글자를 번역문이 쓴다**: {head}")
        print("    뱅크0 에 넣으려면 그만큼 음절 칸을 내줘야 한다.")

    ranked = [char for char, _ in syllables.most_common()]
    covered = sum(syllables[c] for c in ranked[:SINGLE_BYTE])
    print()
    print(f"빈도 쏠림 — 가장 잦은 {SINGLE_BYTE}자가 본문의 {covered / total:.1%}")
    print(f"  이 배치면 글자당 평균 {2 - covered / total:.2f}바이트")

    # 칸이 모자라도 곧바로 실패는 아니다. 한국어 음절 빈도는 극단적으로
    # 쏠려 있어서 잘려 나가는 것은 거의 안 쓰이는 음절이다. 서브셋으로 갈 때
    # **실제로 손봐야 하는 메시지가 몇 건인지**가 판정의 근거가 된다.
    room = BANK0_SLOTS - len(keep) - len(unknown)
    if len(syllables) > room:
        dropped = set(ranked[room:])
        touched = sum(1 for text in texts
                      if any(char in dropped for char in text))
        share = sum(syllables[c] for c in ranked[:room]) / total
        print()
        print(f"서브셋 판정 — 한글에 쓸 수 있는 칸 {room}")
        print(f"  상위 {room}자가 본문의 {share:.3%} 를 덮는다")
        print(f"  잘리는 음절 {len(dropped):,}종을 쓰는 메시지 "
              f"{touched:,} / {len(texts):,} ({touched / max(len(texts), 1):.1%})")
        print("  이만큼만 다시 쓰면 뱅크0 하나로 간다. 고칠 자리는"
              " check_translation.py --layout 의 '없는 글자' 가 짚는다.")

    if layout:
        order = ranked[:SINGLE_BYTE] + sorted(keep | set(unknown)) + \
            ranked[SINGLE_BYTE:]
        layout.parent.mkdir(parents=True, exist_ok=True)
        layout.write_text(json.dumps(
            {"note": "인덱스 순서대로 배치할 문자. 앞 224자는 1바이트로 실린다.",
             "chars": order[:BANK0_SLOTS]},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\n배치 후보 {layout}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="번역 내보내기 디렉터리")
    parser.add_argument("--layout", type=Path,
                        help="빈도순 배치 후보를 낸다")
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"디렉터리가 아니다: {args.root}", file=sys.stderr)
        return 2
    return report(args.root, args.layout)


if __name__ == "__main__":
    raise SystemExit(main())
