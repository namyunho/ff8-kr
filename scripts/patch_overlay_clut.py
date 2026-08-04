#!/usr/bin/env python3
"""오버레이 안의 글자 팔레트 상수를 새 자리로 옮긴다.

## 왜 따로 필요한가

글자를 그리는 코드가 부트 EXE 에만 있는 것이 아니다. **오버레이가 자기 코드로
그린다.** `patch_font_4plane.py` 는 EXE 만 고치므로 팔레트를 옮기면 오버레이가
옛 자리를 가리킨 채 남는다. 거기엔 아무것도 없어서 **글자가 투명하게** —
즉 아무것도 안 그려진 것처럼 — 나온다.

타이틀 화면에서 커서만 나오고 메뉴 글자가 통째로 안 보이던 것이 이것이다.

    801f16f0  andi   v0, a2, 0x1
    801f16f4  beq    v0, zero, 0x801f1700
    801f16f8  addiu  a0, zero, 0x3812      <- 짝수 글리프
    801f16fc  addiu  a0, zero, 0x3852      <- 홀수 글리프
    801f1708  sll    v0, a3, 7             <- 테마 x 0x80
    801f170c  addu   v0, a0, v0

## 짝을 보고 고른다

`0x3812` 라는 값 자체는 우연히도 자주 나온다(`ori k0, v1, 0x3812` 처럼). 진짜
그리기 코드는 **짝수용 바로 뒤에 홀수용이 붙어** 있고 둘 다 `addiu rX, zero, …`
꼴이다. 그 짝만 고친다 — 우연히 맞은 바이트를 건드리면 엉뚱한 데가 망가진다.

전수로 훑으면 134개 엔트리 중 짝을 이룬 곳은 둘뿐이다(TOC #4, #26).

    python3 scripts/patch_overlay_clut.py --dry-run
    python3 scripts/patch_overlay_clut.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import patch_disc as PD                     # noqa: E402
import patch_font_4plane as PF              # noqa: E402
from mips_dis import decode                 # noqa: E402

TOC_LBA = 826
EXE_PATCHED = Path("work/patch-4plane/SLPS_018.80")
EVEN, ODD = 0x3812, 0x3852
ADDIU = 9


def entries() -> list[tuple[int, int, int]]:
    """IMG 목차. `(색인, LBA, 크기)`."""
    raw = PD.read_user(FT.BIN_PATH, TOC_LBA, 2048)
    out = []
    for i in range(0, 2048, 8):
        lba, size = struct.unpack_from("<II", raw, i)
        if lba and size and lba < 400_000:
            out.append((i // 8, lba, size))
    return out


def pairs(data: bytes) -> list[int]:
    """`addiu rX, zero, 0x3812` 바로 뒤에 `…0x3852` 가 오는 자리의 오프셋."""
    out = []
    for off in range(0, len(data) - 8, 4):
        a = int.from_bytes(data[off:off + 4], "little")
        b = int.from_bytes(data[off + 4:off + 8], "little")
        if (a >> 26) != ADDIU or (b >> 26) != ADDIU:
            continue
        if (a & 0xFFFF) != EVEN or (b & 0xFFFF) != ODD:
            continue
        if ((a >> 21) & 0x1F) or ((b >> 21) & 0x1F):    # rs 가 zero 여야 한다
            continue
        out.append(off)
    return out


# 그림자 훅 두 곳. 루틴 주소는 상수로 박지 않고 **서명으로 찾는다** —
# 앞선 루틴 길이가 바뀌면 밀리기 때문이다. 세 번째 워드가 서로 다르다.
#
#   menumain.ovl   `addiu v1, s1, 0x14`   보폭 20바이트
#   메뉴 모듈       `addiu v1, t4, 0x18`   보폭 24바이트, prim 이 t4
SHADOW_HOOKS = (
    ("menumain.ovl", 4, PF.OVL_HOOK, PF.OVL_LOOP, 0x26230014),
    ("메뉴 모듈", PF.MOD_ARCHIVE, PF.MOD_HOOK, PF.MOD_LOOP, 0x25830018),
)
SIG_HEAD = (0x3C018008, 0x8C2121E0)                 # lui at / lw at, 0x21e0(at)


def find_shadow(exe: "Exe", third: int) -> int:
    """패치된 EXE 에서 그림자 루틴을 서명으로 찾는다."""
    want = SIG_HEAD + (third,)
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        addr = exe.load + off - exe.HEADER
        if all(exe.word(addr + i * 4) == w for i, w in enumerate(want)):
            return addr
    raise ValueError(f"EXE 에 그림자 루틴({third:08x})이 없다 — --no-shadow 인가")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}", file=sys.stderr)
        return 2
    new_even, new_odd = PF.CLUT_IDS[EVEN], PF.CLUT_IDS[ODD]
    print(f"짝수 {EVEN:#x} -> {new_even:#x}   홀수 {ODD:#x} -> {new_odd:#x}")

    total = 0
    for index, lba, size in entries():
        data = bytearray(PD.read_user(PD.PATCH_BIN, lba, size))
        spots = pairs(bytes(data))
        if not spots:
            continue
        print(f"\nTOC #{index}  LBA {lba:,}  {size:,}B  — 짝 {len(spots)}곳")
        for off in spots:
            for delta, value in ((0, new_even), (4, new_odd)):
                word = int.from_bytes(data[off + delta:off + delta + 4], "little")
                before = decode(word, 0)
                word = (word & ~0xFFFF) | value
                struct.pack_into("<I", data, off + delta, word)
                print(f"    +{off + delta:>8,}  {before:<28} -> {decode(word, 0)}")
            total += 1
        if args.dry_run:
            continue
        PD.write_user(PD.PATCH_BIN, lba, bytes(data))
        back = PD.read_user(PD.PATCH_BIN, lba, size)
        if back[:len(data)] != bytes(data):
            print("    쓴 대로 안 읽힌다", file=sys.stderr)
            return 1
        print("    썼다. 되읽기 일치")

    # 그림자 훅 — menumain.ovl 과 메뉴 모듈
    from mips_dis import Exe

    try:
        exe = Exe(str(EXE_PATCHED))
    except FileNotFoundError:
        exe = None
    for name, archive, hook, loop, third in SHADOW_HOOKS:
        if exe is None:
            print(f"\n{name} 그림자 훅 건너뜀 — 패치된 EXE 가 없다")
            continue
        try:
            where = find_shadow(exe, third)
        except ValueError as error:
            print(f"\n{name} 그림자 훅 건너뜀 — {error}")
            continue
        lba, size = next((l, s) for i, l, s in entries() if i == archive)
        data = bytearray(PD.read_user(PD.PATCH_BIN, lba, size))
        was = int.from_bytes(data[hook:hook + 4], "little")
        if was != PF.jump(loop):
            print(f"\n**{name} +{hook:,} 가 {was:08x} 다. "
                  f"{PF.jump(loop):08x} 여야 한다**", file=sys.stderr)
            return 1
        struct.pack_into("<I", data, hook, PF.jump(where))
        print(f"\n{name}  +{hook:,}  j {loop:#x} -> j {where:#x}  (그림자)")
        if args.dry_run:
            continue
        PD.write_user(PD.PATCH_BIN, lba, bytes(data))
        if PD.read_user(PD.PATCH_BIN, lba, size)[:len(data)] != bytes(data):
            print("    쓴 대로 안 읽힌다", file=sys.stderr)
            return 1
        print("    썼다. 되읽기 일치")

    print(f"\n고친 짝 {total}곳")
    if args.dry_run:
        print("--dry-run 이라 쓰지 않았다")
    elif not total:
        print("고칠 것이 없다 — 이미 옮겼거나 판본이 다르다", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
