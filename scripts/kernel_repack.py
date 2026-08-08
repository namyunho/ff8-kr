#!/usr/bin/env python3
"""`kernel.bin` 의 글자 섹션을 다시 짜고 **오프셋 표를 함께 고친다**.

## 무엇이 달라지는가

제자리 삽입(`insert_kernel_text.rebuild`)은 문자열마다 원래 자리를 지킨다.
그래서 원문보다 긴 번역은 못 넣는다. 여기서는 자리를 옮기되, 그 문자열을
**가리키는 u16 을 전부 새 값으로 고친다**.

가리키는 자리는 `kernel_offset_tables` 가 찾는다 — 문자열 1,320개가 모두
덮인 것을 확인한 뒤에만 쓴다.

## 게임을 켜기 전에 통과해야 하는 두 검산

지난번에 이걸 안 해서 사용자가 깨진 판을 돌렸다. 그때 내가 건 검사는
크기·널 개수·차례였는데, **그건 내가 내 모델에서 뽑은 불변식**이라 모델이
틀렸다는 것을 잡을 수 없었다.

    항등 검산  아무것도 안 옮기고 돌리면 파일이 **바이트 단위로 원본과 같은가**
    밀기 검산  전부 1바이트씩 밀면 표의 모든 값이 **정확히 1씩** 움직이는가

항등 검산이 좋은 이유는 **내 모델이 맞는지에 기대지 않기 때문**이다. 파일이
재현되는지만 본다. 표를 하나라도 잘못 알고 있으면 여기서 죽는다.

    python3 scripts/kernel_repack.py --selftest
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import insert_kernel_text as IK              # noqa: E402
import kernel_offset_tables as KT            # noqa: E402

SPACE = IK.SPACE


@dataclass(frozen=True)
class Plan:
    """다시 짜기에 필요한 것 전부. 파일 하나에서 한 번 뽑는다."""

    raw: bytes
    secs: list[tuple[int, int]]
    tables: dict[int, list[KT.Table]]        # 글자 섹션 -> 그것을 가리키는 표들
    strings: dict[int, list[bytes]]          # 글자 섹션 -> 문자열들 (차례대로)
    pad: dict[int, int]                      # 글자 섹션 -> 꼬리 정렬 패딩 길이


def survey() -> Plan:
    raw, secs, starts = KT.load()
    cache = KT.fields(raw, secs)
    tables: dict[int, list[KT.Table]] = {}
    strings: dict[int, list[bytes]] = {}
    pad: dict[int, int] = {}
    for index, rel in starts.items():
        found = KT.discover(raw, secs, index, rel, cache)
        if not found:
            continue                        # 못 찾은 섹션은 손대지 않는다
        tables[index] = found
        a, b = secs[index]
        parts, tail = IK.split(raw[a:b])
        strings[index] = parts
        pad[index] = tail
    return Plan(raw, secs, tables, strings, pad)


def lay_out(plan: Plan, index: int, fresh: list[bytes]) -> tuple[bytes, dict[int, int]] | None:
    """섹션 하나를 새로 깐다. `(몸통, 옛 상대오프셋 -> 새 상대오프셋)`.

    자리가 모자라면 `None`. 남는 자리는 **마지막 문자열 뒤에 공백**으로 붙인다
    — 널로 메우면 개수가 늘고, 마지막 널 뒤에 두면 없던 문자열이 하나 생긴다.
    """
    a, b = plan.secs[index]
    tail = plan.pad[index]
    budget = (b - a) - tail
    need = sum(len(p) + 1 for p in fresh)
    if need > budget:
        return None

    fresh = list(fresh)
    fresh[-1] += bytes([SPACE]) * (budget - need)

    move: dict[int, int] = {}
    at = old = 0
    for new_piece, old_piece in zip(fresh, plan.strings[index]):
        move[old] = at
        old += len(old_piece) + 1
        at += len(new_piece) + 1
    body = b"".join(p + b"\x00" for p in fresh) + b"\x00" * tail
    assert len(body) == b - a
    return body, move


SECTORS = 2048
ALIGN = 4


def grow(plan: Plan, replace: dict[int, list[bytes]],
         limit: int | None = None) -> tuple[bytes, dict[int, int]]:
    """**섹션이 자라도 되는** 재배치. `(바이트, 섹션 -> 새 시작)`.

    글자 섹션이 필요한 만큼 커지고 뒤 섹션이 밀린다. 그러면 고칠 것이 둘 는다.

        머리의 56칸 섹션 오프셋 표    섹션이 어디서 시작하는지
        표가 들어 있는 자리          구조체 섹션도 함께 밀리므로

    문자열 오프셋 표의 **값**은 섹션 기준 상대라 안 바뀐다 — 바뀌는 것은 그
    표가 파일 어디에 있느냐다.

    **섹션 주소를 절대값으로 들고 있는 곳이 없는지 미리 셌다.** 머리 표 밖에
    4바이트 정렬 u32 로 섹션 시작을 가리키는 자리는 0곳이다.
    """
    bodies: list[bytes] = []
    moves: dict[int, dict[int, int]] = {}
    for index, (a, b) in enumerate(plan.secs):
        if index not in plan.tables:
            bodies.append(plan.raw[a:b])
            continue
        fresh = list(replace.get(index, plan.strings[index]))
        need = sum(len(p) + 1 for p in fresh)
        size = (need + ALIGN - 1) // ALIGN * ALIGN
        move: dict[int, int] = {}
        at = old = 0
        for new_piece, old_piece in zip(fresh, plan.strings[index]):
            move[old] = at
            old += len(old_piece) + 1
            at += len(new_piece) + 1
        moves[index] = move
        bodies.append(b"".join(p + b"\x00" for p in fresh) + b"\x00" * (size - need))

    head = 4 + 4 * len(plan.secs)
    offsets: list[int] = []
    at = head
    for body in bodies:
        offsets.append(at)
        at += len(body)
    if limit is not None and at > limit:
        raise ValueError(f"{at:,}B 가 되어 {limit:,}B 를 넘는다")

    out = bytearray(struct.pack("<I", len(plan.secs)))
    for value in offsets:
        out += struct.pack("<I", value)
    for body in bodies:
        out += body

    # 표는 구조체 섹션 안에 있고 그 섹션도 밀렸다 — 새 자리에서 값을 고친다
    for index, move in moves.items():
        for table in plan.tables[index]:
            was_host = plan.secs[table.host][0]
            shift = offsets[table.host] - was_host
            for k in range(table.count):
                spot = table.start + k * table.stride
                value = struct.unpack_from("<H", plan.raw, spot)[0]
                if value in move:
                    struct.pack_into("<H", out, spot + shift, move[value])
    return bytes(out), {i: o for i, o in enumerate(offsets)}


def apply(plan: Plan, replace: dict[int, list[bytes]]) -> tuple[bytes, list[int]]:
    """새 문자열을 넣고 표까지 고친 파일. `(바이트, 못 넣은 섹션들)`."""
    raw = bytearray(plan.raw)
    moves: dict[int, dict[int, int]] = {}
    refused: list[int] = []
    for index in sorted(plan.tables):
        fresh = replace.get(index, plan.strings[index])
        laid = lay_out(plan, index, fresh)
        if laid is None:
            refused.append(index)
            continue
        body, move = laid
        a, b = plan.secs[index]
        raw[a:b] = body
        moves[index] = move

    # **표를 고친다.** 옛 상대 오프셋을 새 것으로 바꾼다. 빈칸은 그대로 둔다.
    for index, move in moves.items():
        for table in plan.tables[index]:
            for k in range(table.count):
                at = table.start + k * table.stride
                value = struct.unpack_from("<H", plan.raw, at)[0]
                if value in move:
                    struct.pack_into("<H", raw, at, move[value])
    return bytes(raw), refused


def selftest(plan: Plan) -> int:
    """게임을 켜기 전에 도는 두 검산."""
    bad = 0

    # 1) 항등 — 아무것도 안 옮기면 파일이 그대로여야 한다
    same, refused = apply(plan, {})
    if same == plan.raw and not refused:
        print("  통과  항등 검산 — 안 옮기면 파일이 바이트 단위로 같다")
    else:
        bad += 1
        where = [i for i in range(len(same)) if same[i] != plan.raw[i]]
        print(f"  **실패**  항등 검산 — {len(where)}바이트 다르다 "
              f"(처음 {where[:6]}), 못 깐 섹션 {refused}")

    # 2) 밀기 — 문자열 하나를 줄이면 뒤가 전부 밀린다. 그 뒤에 **게임이 하듯
    #    표를 따라가서** 같은 문자열이 나오는지 본다. 내 move 표와 맞춰 보면
    #    같은 믿음을 두 번 확인하는 것이라 소용이 없다.
    moved = ok = 0
    for index, parts in plan.strings.items():
        if len(parts) < 2 or not parts[0]:
            continue                        # 줄일 것이 없다
        fresh = [parts[0][:-1]] + list(parts[1:])
        if lay_out(plan, index, fresh) is None:
            continue
        shifted, _ = apply(plan, {index: fresh})
        moved += 1
        # 남는 자리는 마지막 문자열 뒤에 공백으로 붙으므로 꼬리 공백은 뗀다
        pad = bytes([SPACE])
        want = [(p[:-1] if k == 0 else p).rstrip(pad) for k, p in enumerate(parts)]
        if [g.rstrip(pad) for g in deref(plan, shifted, index)] == want:
            ok += 1
        else:
            print(f"  **실패**  밀기 검산 #{index} — 표를 따라가니 다른 글자가 나온다")
    print(f"  {'통과' if ok == moved and moved else '**실패**'}  밀기 검산 — "
          f"섹션 {ok}/{moved}개가 표를 따라가도 같은 글자를 낸다")
    bad += ok != moved or not moved

    # 3) 자라기 항등 — **섹션을 밀 수 있는 길로도** 안 바꾸면 파일이 그대로여야 한다
    same, offsets = grow(plan, {})
    if same == plan.raw:
        print("  통과  자라기 항등 검산 — 섹션을 미는 길로도 파일이 그대로다")
    else:
        bad += 1
        where = [i for i in range(min(len(same), len(plan.raw)))
                 if same[i] != plan.raw[i]]
        print(f"  **실패**  자라기 항등 검산 — 크기 {len(plan.raw):,}->{len(same):,}, "
              f"{len(where)}바이트 다르다 (처음 {where[:6]})")

    # 4) 자라기 — 한 섹션을 늘리면 뒤가 밀리고, 그래도 표가 같은 글자를 낸다
    grew = fine = 0
    for index, parts in plan.strings.items():
        if not parts:
            continue
        fresh = list(parts)
        fresh[0] = fresh[0] + bytes([SPACE]) * 8       # 8바이트 늘려 본다
        data, offsets = grow(plan, {index: fresh})
        grew += 1
        want = [p.rstrip(bytes([SPACE])) for p in fresh]
        got = [g.rstrip(bytes([SPACE])) for g in deref(plan, data, index, offsets)]
        if got == want:
            fine += 1
        else:
            print(f"  **실패**  자라기 검산 #{index} — 민 뒤 표가 다른 글자를 낸다")
    print(f"  {'통과' if fine == grew and grew else '**실패**'}  자라기 검산 — "
          f"섹션 {fine}/{grew}개가 8바이트 늘려도 표가 같은 글자를 낸다")
    bad += fine != grew or not grew
    return bad


def deref(plan: Plan, data: bytes, index: int,
          offsets: dict[int, int] | None = None) -> list[bytes]:
    """**게임이 하듯** 표를 따라가 문자열을 꺼낸다.

    차례는 **원본에서 배우고**(어느 칸이 몇 번째 문자열인지), 값은 **새
    파일에서 읽는다.** 이 둘을 안 가르면 새 파일에서 옛 값을 읽으려 든다 —
    실제로 그렇게 짰다가 25개 섹션이 전부 틀렸다고 나왔다.

    `offsets` 는 섹션이 밀렸을 때의 새 시작. 없으면 안 밀린 것이다.
    """
    base = plan.secs[index][0] if offsets is None else offsets[index]
    order: dict[int, int] = {}               # 옛 상대오프셋 -> 문자열 차례
    at = 0
    for k, piece in enumerate(plan.strings[index]):
        order[at] = k
        at += len(piece) + 1

    out: list[bytes | None] = [None] * len(plan.strings[index])
    for table in plan.tables[index]:
        shift = 0 if offsets is None else offsets[table.host] - plan.secs[table.host][0]
        for k in range(table.count):
            old_spot = table.start + k * table.stride
            was = struct.unpack_from("<H", plan.raw, old_spot)[0]
            if was not in order:
                continue
            now = struct.unpack_from("<H", data, old_spot + shift)[0]
            end = data.find(b"\x00", base + now)
            out[order[was]] = data[base + now:end]
    return [b"" if v is None else v for v in out]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    plan = survey()
    covered = sum(len(v) for v in plan.strings.values())
    print(f"글자 섹션 {len(plan.tables)}개  문자열 {covered:,}개  "
          f"표 {sum(len(v) for v in plan.tables.values())}벌")
    if not args.selftest:
        print("--selftest 로 항등·밀기 검산을 돌린다")
        return 0
    return 1 if selftest(plan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
