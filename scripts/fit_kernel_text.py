#!/usr/bin/env python3
"""완역을 자리에 맞춘다 — **줄이는 것은 설명뿐, 이름은 손대지 않는다.**

## 무엇이 달라졌나

`--repack` 이 들어오기 전에는 문자열마다 원래 자리를 넘을 수 없었다. 그래서
409건을 하나하나 줄였다. 지금은 제약이 **섹션 단위**다 — 한 섹션 안에서
남는 자리를 넘치는 줄에 나눠 줄 수 있다.

그러면 축약의 상당수가 **필요 없어진다.** 실제로 글자 섹션 25개 중 11개는
완역이 그대로 들어간다. 이 도구는 **완역에서 시작해, 모자란 섹션에만
필요한 만큼** 축약을 도로 얹는다.

## 이름은 건드리지 않는다

아이템·마법 이름 같은 고유명사는 줄이면 그게 곧 손실이다. 다행히 구조가
갈라 준다 — 레코드의 **+0 필드가 이름, +2 필드가 설명**이다(코드에서 읽은
`sub_801F1050` 의 쓰임과 자료로 확인했다).

    #32 필드0  ファイラ · ファイガ · ブリザド          <- 이름
        필드1  敵単体に炎属性のダメージ魔法            <- 설명

표가 하나뿐이고 간격이 2B 인 섹션(#39·#55)은 **이름과 설명이 번갈아** 든다.
그 밖의 한 필드짜리(#34 어빌리티·#35 무기)는 통째로 이름이라 안 건드린다.

    python3 scripts/fit_kernel_text.py --dry-run
    python3 scripts/fit_kernel_text.py
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                      # noqa: E402
import insert_kernel_text as IK              # noqa: E402
import kernel_offset_tables as KT            # noqa: E402
import kernel_repack as KR                   # noqa: E402
import patch_disc as PD                      # noqa: E402
import text_measure as TM                    # noqa: E402
import text_rows as TR                       # noqa: E402

FIXES = Path("data/kernel-text-fixes.json")
SHORT = Path("data/kernel-shortenings.json")
ROWS = Path("work/text/kernel-text-ko.json")


def kinds(plan: KR.Plan, index: int, messages: set[int] = frozenset()) -> dict[int, str]:
    """섹션 안 문자열의 갈래. `상대오프셋 -> "이름" | "설명"`."""
    tables = sorted(plan.tables[index], key=lambda t: t.start)
    out: dict[int, str] = {}
    if index in messages:
        # 이름이 아니라 대사인 섹션 — 고유명사가 아니므로 줄여도 된다
        at = 0
        for piece in plan.strings[index]:
            out[at] = "설명"
            at += len(piece) + 1
        return out
    if len(tables) == 1 and tables[0].stride == 2:
        # 한 줄짜리 표 — 이름과 설명이 번갈아 든다
        for k in range(tables[0].count):
            value = struct.unpack_from("<H", plan.raw, tables[0].start + k * 2)[0]
            if value not in KT.SENTINELS or (value == 0 and k == 0):
                out[value] = "이름" if k % 2 == 0 else "설명"
        return out
    for n, table in enumerate(tables):
        for k in range(table.count):
            value = struct.unpack_from("<H", plan.raw, table.start + k * table.stride)[0]
            if value in KT.SENTINELS and not (value == 0 and k == 0):
                continue
            out[value] = "이름" if n == 0 else "설명"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    # 자라기 모드에서는 예산이 **섹션이 아니라 파일 전체**다. 섹션은 필요한
    # 만큼 커지고 뒤가 밀리므로, 총합이 섹터 한도 안에만 들면 축약이 필요 없다.
    parser.add_argument("--grow", action="store_true",
                        help="섹션이 자라도 되는 경우 (예산이 파일 전체다)")
    args = parser.parse_args()

    plan = KR.survey()
    maps = TM.load_maps()
    raw_fixes = json.loads(FIXES.read_text(encoding="utf-8"))["fixes"]
    fixes = {f["was"]: f["now"] for f in raw_fixes if "slot" not in f}
    fixes_at = {(f["was"], f["slot"]): f["now"] for f in raw_fixes if "slot" in f}
    doc = json.loads(SHORT.read_text(encoding="utf-8"))
    cuts = dict(doc["by_text"])
    messages = set(doc.get("message_sections", []))
    by_slot = {(x["was"], x["slot"]): x["now"] for x in doc["by_slot"]}
    rows = json.loads(ROWS.read_text(encoding="utf-8"))
    at_offset = {r["offset"]: r for r in rows}

    def width(text: str) -> int | None:
        return TM.encoded_length(text, maps)

    picked: dict[int, str] = {}          # 오프셋 -> 최종 글자
    report = []
    stuck = 0
    for index in sorted(plan.tables):
        base = plan.secs[index][0]
        budget = (plan.secs[index][1] - base) - plan.pad[index]
        kind = kinds(plan, index, messages)
        items = []                        # (오프셋, 완역, 축약후보|None, 갈래)
        at = 0
        for piece in plan.strings[index]:
            row = at_offset.get(base + at)
            draft = ""
            if row is not None:
                draft = IK.strip_name(row.get("ko_draft") or "").strip()
                draft = fixes_at.get((draft, row.get("slot_bytes")),
                                     fixes.get(draft, draft))
            cut = by_slot.get((draft, row.get("slot_bytes"))) if row else None
            cut = cut or cuts.get(draft)
            items.append((base + at, draft, cut, kind.get(at, "이름"), len(piece)))
            at += len(piece) + 1

        def total(choice: dict[int, str]) -> int:
            n = 0
            for offset, draft, _cut, _k, raw_len in items:
                text = choice.get(offset, draft)
                if not text:
                    n += raw_len + 1
                    continue
                w = width(text)
                n += (w if w is not None else raw_len) + 1
            return n

        choice: dict[int, str] = {}
        need = total(choice)
        if args.grow:
            budget = need                    # 섹션 예산이 없다 — 총합으로 따로 본다
        # **설명만** 후보로 둔다. 아낀 바이트가 큰 것부터 — 손대는 줄을 줄인다
        cand = []
        for offset, draft, cut, k, _ in items:
            if not cut or cut == draft:
                continue
            # 이름은 안 건드린다. 다만 **띄어쓰기만 지우는 것**은 용어가 그대로라
            # 허용한다 — `{03:40} 캐논` -> `{03:40}캐논` 은 「캐논」을 안 건드린다.
            if k == "이름" and cut.replace(" ", "") != draft.replace(" ", ""):
                continue
            a, b = width(draft), width(cut)
            if a is None or b is None or b >= a:
                continue
            cand.append((a - b, offset, cut))
        cand.sort(reverse=True)
        used = 0
        for saved, offset, cut in cand:
            if need <= budget:
                break
            choice[offset] = cut
            need -= saved
            used += 1
        picked.update(choice)
        report.append((index, budget, total({}), need, used, len(cand), need <= budget))
        stuck += need > budget

    print(f"{'섹션':>5}{'자리':>8}{'완역':>8}{'맞춘뒤':>8}{'줄인줄':>7}{'후보':>6}")
    for index, budget, full, now, used, cand, ok in report:
        mark = "" if ok else f"  **{now - budget}B 모자람**"
        print(f"{index:>5}{budget:>7,}B{full:>7,}B{now:>7,}B{used:>7}{cand:>6}{mark}")
    total_cut = sum(r[4] for r in report)
    print(f"\n줄인 줄 {total_cut}건 (전에는 396건) · 자리가 모자란 섹션 {stuck}개")
    if args.grow:
        # 섹션이 자란 뒤의 파일 크기. 글자 섹션은 필요한 만큼, 나머지는 그대로.
        need = {r[0]: r[3] for r in report}
        whole = 4 + 4 * len(plan.secs)
        for index, (lo, hi) in enumerate(plan.secs):
            whole += ((need[index] + 3) // 4 * 4) if index in need else hi - lo
        room = -(-whole // 2048) * 2048
        limit = 18 * 2048
        print(f"파일 전체 {whole:,}B (섹터 {room // 2048}개) / 한도 {limit:,}B"
              f"  {'여유 ' + format(limit - whole, ',') + 'B' if whole <= limit else '**넘친다**'}")

    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    out = []
    for row in rows:
        draft = IK.strip_name(row.get("ko_draft") or "").strip()
        fixed = fixes_at.get((draft, row.get("slot_bytes")), fixes.get(draft))
        fresh = dict(row)
        if fixed:
            fresh["ko_draft"] = fixed
        cut = picked.get(row["offset"])
        fresh = TR.with_short(fresh, cut, maps)
        out.append(TR.refresh(fresh, maps))
    TR.save(ROWS, out)
    print(f"\n→ {ROWS}  (고침 {sum(1 for r in rows if IK.strip_name(r.get('ko_draft') or '').strip() in fixes)}건 반영)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
