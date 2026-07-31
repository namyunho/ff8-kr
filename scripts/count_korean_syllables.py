#!/usr/bin/env python3
"""번역문이 요구하는 한글 음절 수를 센다. M5 의 마지막 전제를 판정한다.

뱅크 하나는 882칸이다. 그중 숫자·라틴·부호에 내줘야 하는 자리를 빼면 한글이 쓸
수 있는 칸이 나오고, 번역문이 쓰는 **서로 다른 음절 수**가 그 안에 들어가야
한다. 이 판정은 번역이 있어야만 할 수 있다. 번역 도중에도 돌려 추세를 본다.

뱅크1 을 되찾으면 1,764칸이 된다(`docs/font-analysis.md` 의 "뱅크1 재활용").
`--banks 2` 로 그 배치를 낸다. 뱅크0 하나로 가면 서브셋이 되어 몇 건을 다시
써야 하는지가 함께 나온다.

인덱스 `0..223` 은 1바이트, `224` 이상은 2바이트로 실린다. 빈도 상위 음절을
앞자리에 놓으면 본문이 작아지므로 배치 후보도 함께 낸다.

    python3 scripts/count_korean_syllables.py work/translate
    python3 scripts/count_korean_syllables.py work/translate --layout work/hangul-layout.json --banks 2
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402
import glyph_text as GT                     # noqa: E402

TOKEN = re.compile(r"\{[^}]*\}")
HANGUL = re.compile(r"[가-힣]")
PER_BANK = 882              # 441셀 x 2 — 셀 하나에 글리프 둘이 인터리브된다
SINGLE_BYTE = 224


def collect(root: Path) -> tuple[collections.Counter, dict]:
    """번역된 `ko` 만 모은다. 제어 코드는 글자가 아니므로 뺀다.

    한글이 아닌 글자는 두 갈래로 나눠 센다. **그 필드의 원문이 이미 쓰는
    글자는 뱅크0 칸을 먹지 않는다.** 원문이 쓴다는 것은 그 필드의 폰트가
    그 글리프를 가지고 있다는 뜻이고, 뱅크0 에 없다면 필드 전용 폰트
    (뱅크1)에서 온 것이다. 뱅크1 은 우리가 건드리지 않으므로 한국어판에서도
    그대로 그려진다.

    이 구분을 안 하면 `■ □ ◆ ◎ 【 】` 처럼 원문이 쓰는 도형이 전부 새 칸을
    요구하는 것으로 계산돼 판정이 과다 계상된다.
    """
    syllables = collections.Counter()
    other = collections.Counter()
    borrowed = collections.Counter()
    texts: list[str] = []
    stats = {"files": 0, "entries": 0, "translated": 0}
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        stats["files"] += 1
        native = {char for entry in document["entries"]
                  for char in TOKEN.sub("", entry["ja"])}
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
                elif char != "\n":
                    # 공백도 글리프다(뱅크0 인덱스 63). 세지 않으면 배치에서
                    # 빠지고, 어절마다 쓰이므로 인코딩이 전부 깨진다.
                    (borrowed if char in native else other)[char] += 1
    return syllables, {"stats": stats, "other": other, "texts": texts,
                       "borrowed": borrowed}


def choose(ranked: list[str], texts: list[str], room: int) -> set[str]:
    """칸에 넣을 음절을 고른다. **빈도순이 최적이 아니다.**

    목적은 빈도를 최대화하는 것이 아니라 **손봐야 할 메시지를 최소화**하는
    것이다. 이 둘은 다르다 — 드문 음절 둘이 같은 메시지에 있으면 둘 다 버려도
    깨지는 메시지는 하나뿐이다. 버릴 것을 고를 때 같은 메시지에 몰린 것부터
    버리면 피해가 뭉친다.

    그래서 욕심 알고리즘으로 하나씩 버린다. 버렸을 때 **새로 깨지는 메시지가
    가장 적은** 음절을 고르고, 같으면 덜 쓰이는 쪽을 버린다.
    """
    if len(ranked) <= room:
        return set(ranked)

    where: dict[str, list[int]] = collections.defaultdict(list)
    for index, text in enumerate(texts):
        for char in set(text):
            where[char].append(index)
    rank = {char: index for index, char in enumerate(ranked)}

    broken = [0] * len(texts)               # 메시지마다 버려진 음절이 몇 개인가
    kept = set(ranked)
    for _ in range(len(ranked) - room):
        best, best_cost = None, None
        # 덜 쓰이는 것부터 본다. 비용이 0 이면 더 볼 것도 없다.
        for char in reversed(ranked):
            if char not in kept:
                continue
            cost = sum(1 for index in where[char] if broken[index] == 0)
            if best_cost is None or cost < best_cost:
                best, best_cost = char, cost
                if cost == 0:
                    break
        kept.discard(best)
        for index in where[best]:
            broken[index] += 1
    return kept


def report(root: Path, layout: Path | None, banks: int = 1) -> int:
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
    borrowed = extra["borrowed"]

    # **폰트가 못 담는 음절에는 칸을 주지 않는다.** 배치를 "번역문이 쓰는
    # 음절" 로만 만들면 기계 번역의 오타까지 칸을 받아 스스로를 정당화한다.
    # 그런 음절은 화면에서 어차피 빈칸이 되므로 고쳐야 할 자리이지 담을
    # 대상이 아니다. 정본은 번역문이 아니라 폰트다.
    unwritable = {char: count for char, count in syllables.items()
                  if char not in BF.covered()}
    for char in unwritable:
        del syllables[char]

    # 원문이 이미 쓰는 글자라도 **뱅크0 에 있으면 칸이 필요하다.** 우리가
    # 뱅크0 을 한글로 갈아엎기 때문이다. 뱅크0 에 없으면서 원문이 쓰는
    # 글자는 필드 전용 폰트(뱅크1)에서 온다.
    #
    # **뱅크1 을 우리가 가져가면 그것도 공짜가 아니다.** `patch_font_bank1.py`
    # 가 필드 한자표 적재를 막으므로 그 글리프는 아예 존재하지 않게 된다.
    # 뱅크 수에 따라 판정이 갈리는 자리다 — 놓치면 인코딩 단계에서야
    # `ost■□◆◎【】` 같은 글자로 터진다.
    for char, count in borrowed.items():
        if char in glyphs.index or banks >= 2:
            other[char] += count
    field_font = ({} if banks >= 2 else
                  {char: count for char, count in borrowed.items()
                   if char not in glyphs.index})

    keep = {char for char in other if char in glyphs.index}
    unknown = {char: count for char, count in other.items()
               if char not in glyphs.index}

    total = sum(syllables.values())
    need = len(syllables) + len(keep) + len(unknown)
    slots = banks * PER_BANK
    print()
    print(f"서로 다른 한글 음절 : {len(syllables):,}")
    print(f"한글이 아닌 글자     : {len(keep)}종(원본 폰트에 있음) "
          f"+ {len(unknown)}종(없음)")
    print(f"필요한 칸 합계       : {need:,} / {slots:,} (뱅크 {banks}개)")
    print("  →", "들어간다" if need <= slots
          else f"모자란다. {need - slots}칸 초과")

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
    if field_font:
        head = "".join(sorted(field_font))
        print(f"  필드 전용 폰트에서 오는 글자 {len(field_font)}종: {head}")
        print("    원문이 그 자리에 쓰던 것이라 칸을 새로 내주지 않아도 된다.")
    if unknown:
        head = "".join(sorted(unknown))[:40]
        print(f"  **어디에도 없는 글자를 번역문이 쓴다**: {head}")
        print("    쓰려면 음절 칸을 내줘야 한다. 뱅크0 에 있는 것으로 바꾸는 편이 낫다.")

    if unwritable:
        head = "".join(sorted(unwritable))
        print()
        print(f"  **폰트에 없는 음절 {len(unwritable)}종** (연 "
              f"{sum(unwritable.values())}회) — 칸을 주지 않았다")
        print(f"    {head}")
        print("    기계 번역의 오타다. 화면에서 빈칸이 되므로 고쳐야 한다.")

    ranked = [char for char, _ in syllables.most_common()]
    covered = sum(syllables[c] for c in ranked[:SINGLE_BYTE])
    print()
    print(f"빈도 쏠림 — 가장 잦은 {SINGLE_BYTE}자가 본문의 {covered / total:.1%}")
    print(f"  이 배치면 글자당 평균 {2 - covered / total:.2f}바이트")

    # 칸이 모자라도 곧바로 실패는 아니다. 한국어 음절 빈도는 극단적으로
    # 쏠려 있어서 잘려 나가는 것은 거의 안 쓰이는 음절이다. 서브셋으로 갈 때
    # **실제로 손봐야 하는 메시지가 몇 건인지**가 판정의 근거가 된다.
    room = banks * PER_BANK - len(keep) - len(unknown)
    print(f"쓸 수 있는 칸 {banks * PER_BANK:,} (뱅크 {banks}개) — "
          f"부호·숫자·라틴에 {len(keep) + len(unknown)}칸을 내주고 "
          f"한글 몫 {room:,}")
    if len(syllables) > room:
        chosen = choose(ranked, texts, room)
        dropped = set(ranked) - chosen
        touched = sum(1 for text in texts
                      if any(char in dropped for char in text))
        plain = sum(1 for text in texts
                    if any(char in set(ranked[room:]) for char in text))
        share = sum(syllables[c] for c in chosen) / total
        print()
        print(f"서브셋 판정 — 한글에 쓸 수 있는 칸 {room}")
        print(f"  고른 {room}자가 본문의 {share:.3%} 를 덮는다")
        print(f"  잘리는 음절 {len(dropped):,}종을 쓰는 메시지 "
              f"{touched:,} / {len(texts):,} ({touched / max(len(texts), 1):.1%})")
        print(f"  빈도순으로 자르면 {plain:,}건이다 — 같은 메시지에 몰린 것부터"
              f" 버려 {plain - touched}건을 줄였다")
        print("  이만큼만 다시 쓰면 뱅크0 하나로 간다. 고칠 자리는"
              " check_translation.py --layout 의 '없는 글자' 가 짚는다.")

    if layout:
        chosen = choose(ranked, texts, room)
        # 1바이트 자리(0..223)는 "상위 224 **음절**" 이 아니라 **상위 224
        # 글자**여야 한다. 부호를 뒤로 몰면 공백(45,655회)과 「」(13,736회)가
        # 2바이트로 실려 본문이 수십 KB 늘어난다.
        weight = collections.Counter(syllables)
        weight.update(other)
        order = sorted(chosen | keep | set(unknown),
                       key=lambda char: -weight[char])
        layout.parent.mkdir(parents=True, exist_ok=True)
        chars = order[:banks * PER_BANK]
        layout.write_text(json.dumps(
            {"note": f"인덱스 순서대로 배치할 문자. 앞 {SINGLE_BYTE}자는 1바이트로"
                     f" 실린다. 뱅크 {banks}개 x {PER_BANK}칸.",
             "banks": banks,
             "chars": chars},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for bank in range(banks):
            part = chars[bank * PER_BANK:(bank + 1) * PER_BANK]
            if part:
                print(f"  뱅크{bank}  {len(part):>3}자  {''.join(part[:14])}…")
        print(f"배치 후보 {layout}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="번역 내보내기 디렉터리")
    parser.add_argument("--layout", type=Path,
                        help="빈도순 배치 후보를 낸다")
    parser.add_argument("--banks", type=int, default=1,
                        help="쓸 수 있는 폰트 뱅크 수 (기본 1). 뱅크1 을 되찾으면 2")
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"디렉터리가 아니다: {args.root}", file=sys.stderr)
        return 2
    return report(args.root, args.layout, args.banks)


if __name__ == "__main__":
    raise SystemExit(main())
