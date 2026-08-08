#!/usr/bin/env python3
"""`kernel.bin` 의 문자열을 **누가 가리키는지** 찾아낸다.

## 왜 이것이 먼저인가

문자열을 옮기려면 그것을 가리키는 값을 전부 고쳐야 한다. 하나라도 놓치면
그 항목만 조용히 어긋난다 — 실제로 **키스티스 이름이 안 뜬 적이 있다**
(`docs/lessons.md` 21번).

그래서 이 도구의 산출물은 「표를 찾았다」가 아니라 **「문자열 N개 중 몇 개가
가리켜지는지 증명했다」**는 숫자다. 100%가 아니면 그 섹션은 옮기지 않는다.

## 무엇을 찾는가

메뉴 오버레이의 `sub_801F1050`(0x801f1050)이 쓰는 모양을 코드에서 읽었다.

    sll  $a1, 1            ; 색인 x 2
    addu $v1, $a0, $a1
    addiu $v1, 2
    lhu  $v0, 0($v1)       ; u16 을 읽어
    addu $v0, $a0, $v0     ; **base 를 더한다** — 상대 오프셋이다

`kernel.bin` 도 같은 꼴이다. 글자 섹션에는 표가 없고, **구조체 섹션의 레코드
안에 u16 상대 오프셋**이 들어 있다. 그래서 찾는 것은 세 값이다.

    (표가 시작하는 자리, 레코드 간격, 레코드 안에서의 위치)

## 오름차순을 가정하지 않는다

이것 때문에 두 번 놓쳤다. 캐릭터 이름 표(섹션 #6)는 11칸인데 그중 둘이
`0xFFFF` 다 — **스콜과 리노아는 이름을 세이브에서 읽기 때문**이다. 오름차순
판별은 거기서 끊긴다. 그래서 **빈칸(0xFFFF 따위)을 건너뛰고, 「모든 문자열을
빠짐없이 한 번씩 덮는가」로만** 판정한다.

    python3 scripts/kernel_offset_tables.py            찾은 표와 덮은 비율
    python3 scripts/kernel_offset_tables.py --section 37
"""

from __future__ import annotations

import argparse
import collections
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT              # noqa: E402
import insert_kernel_text as IK              # noqa: E402
import patch_disc as PD                      # noqa: E402
import patch_overlay_clut as OC              # noqa: E402

TRANSLATION = Path("work/text/kernel-text-ko.json")

# 「문자열이 없다」를 뜻하는 값. 표 한가운데에 섞여 나온다.
SENTINELS = frozenset({0x0000, 0xFFFF, 0xFFFE, 0xFFFD})


@dataclass(frozen=True)
class Table:
    """글자 섹션 하나를 가리키는 표 한 벌."""

    section: int          # 가리켜지는 글자 섹션
    start: int            # 표 첫 칸의 파일 오프셋
    stride: int           # 레코드 간격
    count: int            # 읽은 칸 수
    covered: int          # 그중 문자열을 가리킨 칸 수
    host: int | None      # 표가 든 섹션


def load() -> tuple[bytes, list[tuple[int, int]], dict[int, list[int]]]:
    lba, size = next((l, s) for i, l, s in OC.entries() if i == IK.TOC_INDEX)
    raw = bytes(PD.read_user(FT.BIN_PATH, lba, size))
    secs = list(IK.sections(raw))
    rows = json.loads(TRANSLATION.read_text(encoding="utf-8"))
    starts: dict[int, list[int]] = collections.defaultdict(list)
    for row in rows:
        index = row["section"]
        starts[index].append(row["offset"] - secs[index][0])
    return raw, secs, {k: sorted(v) for k, v in starts.items()}


MAX_STRIDE = 260


def fields(raw: bytes, secs) -> list[tuple[int, int, int, int, tuple[int, ...]]]:
    """구조체 섹션을 **레코드 배열**로 보고, 필드마다 값을 뽑는다.

    `(섹션, 시작, 간격, 필드, 값들)`.

    **구조 제약을 건다.** 표는 「등차로 값이 몇 개 맞는 자리」가 아니라
    **레코드 배열의 한 필드**다. 그래서 셋을 요구한다.

        1. 레코드 간격이 섹션 크기를 **정확히 나눈다** (배열이 섹션을 꽉 채운다)
        2. 표가 섹션 **첫 레코드**에서 시작한다
        3. 그 필드의 값이 **전부** 문자열 오프셋이거나 빈칸이다

    이걸 안 걸었다가 「#31 이 표 24벌로 덮인다」는 헛것을 봤다 — 2개씩 우연히
    맞은 자리를 탐욕적으로 주워 담은 것이었다. 우연은 규칙을 못 흉내 낸다.
    """
    out = []
    for index, (start, end) in enumerate(secs):
        size = end - start
        if size < 4:
            continue
        for stride in range(2, min(size, MAX_STRIDE) + 1, 2):
            if size % stride:
                continue
            count = size // stride
            if count < 2:
                continue
            for field in range(0, stride - 1, 2):
                values = tuple(struct.unpack_from("<H", raw, start + i * stride + field)
                               for i in range(count))
                out.append((index, start, stride, field, tuple(v[0] for v in values)))
    return out


def discover(raw: bytes, secs, section: int, rel: list[int],
             table_cache: list | None = None) -> list[Table]:
    """이 섹션의 문자열을 덮는 필드들. **합쳐서** 전부를 덮어야 한다."""
    valid = set(rel)
    # **한 글자 섹션은 한 레코드 배열이 가리킨다.** 레코드 크기는 그 배열의
    # 성질이라 필드마다 다를 수 없다. 그래서 (섹션, 간격) 으로 묶고, 그 묶음
    # **안에서만** 필드를 모아 전부 덮이는지 본다. 이걸 안 걸면 간격 4B 필드와
    # 8B 필드를 섞어 덮어 놓고 「찾았다」고 하게 된다 — 그건 우연이다.
    groups: dict[tuple[int, int], list] = collections.defaultdict(list)
    for host, start, stride, field, values in (table_cache or fields(raw, secs)):
        covered = {v for v in values if v in valid}
        if not covered:
            continue
        if any(v not in valid and v not in SENTINELS for v in values):
            continue                      # 이 필드는 오프셋 표가 아니다
        groups[(host, stride)].append((start + field, len(values), covered))

    best: list[Table] | None = None
    for (host, stride), items in groups.items():
        if set().union(*(c for _, _, c in items)) != valid:
            continue
        items.sort(key=lambda x: x[0])
        picked: list[Table] = []
        left = set(valid)
        for at, count, covered in items:
            fresh = covered & left
            if not fresh:
                continue
            left -= fresh
            picked.append(Table(section, at, stride, count, len(fresh), host))
        # 필드 수가 적은 쪽이 참일 가능성이 높다 (이름 + 설명이면 둘)
        if best is None or len(picked) < len(best):
            best = picked
    return best or []


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--section", type=int, help="이 글자 섹션만 본다")
    args = parser.parse_args()

    raw, secs, starts = load()
    cache = fields(raw, secs)
    targets = [args.section] if args.section is not None else sorted(starts)
    proven = missing = 0
    print(f"kernel.bin {len(raw):,}B  글자 섹션 {len(starts)}개\n")
    print(f"{'섹션':>5}{'문자열':>7}  표")
    for index in targets:
        rel = starts[index]
        tables = discover(raw, secs, index, rel, cache)
        if tables:
            proven += len(rel)
            note = " + ".join(f"#{t.host}@{t.start:#x}/{t.stride}B({t.covered})"
                              for t in tables[:4])
            if len(tables) > 4:
                note += f" + {len(tables) - 4}벌 더"
        else:
            missing += len(rel)
            note = "**못 찾음 — 이 섹션은 옮기면 안 된다**"
        print(f"{index:>5}{len(rel):>7}  {note}")

    total = proven + missing
    print(f"\n가리켜짐이 증명된 문자열 {proven:,} / {total:,}"
          f"  ({proven * 100 / total:.1f}%)")
    if missing:
        print(f"**{missing}개가 남았다.** 이 섹션들은 자리를 옮기지 않는다 — "
              f"표를 못 찾은 것은 「없다」가 아니라 「아직 못 봤다」다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
