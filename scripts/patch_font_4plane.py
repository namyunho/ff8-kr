#!/usr/bin/env python3
"""4중 인터리브 패치. 글자 칸을 882 에서 **1,764** 로 올린다.

## 왜

번역이 필드 대사에 그치지 않는다. 메뉴·아이템·마법·GF·튜토리얼까지 가면
서로 다른 글자가 실측 추정으로 **약 1,175종**이다. 뱅크0 하나(882칸, 부호를
빼면 828)로는 모자란다.

## 어떻게

4bpp 픽셀에 글리프를 **1비트씩 4개** 넣는다. 갈무리는 원래 1비트 픽셀 폰트라
화질 손실이 0 이다.

    평면 = (뱅크비트 ? 2 : 0) | (인덱스 & 1)
    셀   = (인덱스 & 0x3FF) >> 1          441칸 x 4 = 1,764

**텍스처는 뱅크0 것 하나만 쓴다.** 뱅크1 에서는 인코딩 공간과 폭 테이블만
빌린다. 이게 핵심이다 — 뱅크1 의 VRAM `(832,256)` 은 동영상이 지우지만
뱅크0 의 `(960,256)` 은 살아남는 것을 실기에서 확인했다.

## 패치 자리

    0x8002c420   slti 0x11 -> 0x21    CLUT 높이 32 허용
    0x8002c470   0x1c0 -> 0x370       폭표를 884바이트 복사 (1,768니블)
    0x8002c3b8   bne -> 0x8002c51c    필드 한자표 적재 차단
    0x8002c408   0x120 -> 0x3c0       CLUT 적재 x = 960
    0x8002c410   0xe0  -> 0x1d8       CLUT 적재 y = 472
    조회 7곳     0x23bc -> 0x23b1     통합 폭표의 뱅크1 시작(+441바이트)
    기준 5곳     0x3812 -> 0x763c     CLUT id 기준 = (472 << 6) | (960 >> 4)
    그리기 4곳   tpage 분기 6슬롯 교체 (아래)

## 그리기 4곳이 안전한 이유

CLUT 계산 지점에는 빈 슬롯이 없다. 그런데 tpage 분기가 통째로 필요 없어진다 —
두 뱅크가 같은 텍스처를 쓰므로. 그 6슬롯을 이렇게 바꾼다.

    lui  tp, 0xe100
    ori  tp, tp, 0x41f       뱅크0 tpage 로 고정
    lhu  at, off(prim)       아까 저장한 CLUT
    addu at, at, v0          += 뱅크비트(0x400). v0 에 이미 들어 있다
    sh   at, off(prim)
    andi idx, idx, 0x3ff

**분기 목적지가 하나도 안 움직인다.** 팔레트를 16줄 아래(y=240~255)에 두면
16 x 0x40 = 0x400 이라 뱅크 비트가 이미 제자리에 있어 시프트도 필요 없다.

`at` 을 임시로 쓴다. 어셈블러 임시 레지스터라 컴파일된 코드가 문장 사이로
값을 물고 가지 않는다.

    python3 scripts/patch_font_4plane.py --show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mips_asm import assemble                      # noqa: E402
from mips_dis import Exe, decode                   # noqa: E402

CLUT_LIMIT = 0x8002C420         # slti v0, v0, 0x11
WIDTH_COPY = 0x8002C470         # addiu t0, a2, 0x1c0
BANK_BRANCH = 0x8002C3B8        # bne t0, zero, 뱅크1 갈래
LOADER_RETURN = 0x8002C51C
BANK1_TABLE_OLD = 0x23BC        # 뱅크1 폭표 (별도)
BANK1_TABLE_NEW = 0x23B1        # 통합표 안의 뱅크1 시작 = 0x21f8 + 441

# **팔레트를 옮긴다.** 원본 CLUT `(288,224)` 아래 16줄을 평면2·3 용으로 썼다가
# 실기에서 뱅크1 글자만 노이즈로 나왔다. 그 자리는 폭 16짜리 남의 CLUT 가
# 덮는다 — 한 시점의 0 은 빈 자리가 아니다. 뱅크0 텍스처 사각형 안쪽으로
# 옮긴다. 동영상 두 편 뒤에도 100% 온전한 것이 실측된 유일한 자리다.
CLUT_X_SITE = 0x8002C408        # addiu v0, zero, 0x120   -> 288
CLUT_Y_SITE = 0x8002C410        # addiu v0, zero, 0xe0    -> 224
CLUT_VRAM_X, CLUT_VRAM_Y = 960, 472
CLUT_ID_OLD = 0x3812            # (224 << 6) | (288 >> 4)
CLUT_ID_NEW = (CLUT_VRAM_Y << 6) | (CLUT_VRAM_X >> 4)       # 0x763c

# 그리기 4곳. (분기 주소, tpage 레지, 인덱스 레지, CLUT 저장 오프셋, 프리미티브 레지)
DRAW_SITES = (
    (0x8002E870, "v1", "a2", 0x12, "a1"),
    (0x8002EA2C, "v1", "t2", 0x02, "t3"),
    (0x8002ECA0, "a3", "a2", 0x12, "s2"),
    (0x8002F0DC, "a3", "a2", 0x12, "s2"),
)

DRAW_PATCH = """
    lui   {tp}, 0xe100
    ori   {tp}, {tp}, 0x41f
    lhu   at, {off:#x}({prim})
    addu  at, at, v0
    sh    at, {off:#x}({prim})
    andi  {idx}, {idx}, 0x3ff
"""


def expect(exe: Exe, addr: int, want: int, what: str) -> None:
    got = exe.word(addr)
    if got != want:
        raise ValueError(f"{addr:#010x} 가 {got:08x} 다. {what} 이어야 한다 "
                         f"({want:08x}) — 다른 판본이거나 이미 패치됐다")


def clut_id_sites(exe: Exe) -> list[int]:
    """CLUT id 기준값 `0x3812` 를 즉치로 쓰는 곳을 전부 찾는다.

    그리기 4곳 말고 `0x80033118` 이 하나 더 있다 — 패리티(+0x40)를 안 더하는
    다섯 번째 글자 그리기 경로다. 4중 인터리브에서 제대로 도는지는 아직 확인
    안 했지만, **팔레트를 옮기면 여기도 같이 옮겨야** 색을 잃지 않는다.
    """
    out = []
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        word = int.from_bytes(exe.data[off:off + 4], "little")
        if word >> 26 in (8, 9) and (word & 0xFFFF) == CLUT_ID_OLD:
            out.append(exe.load + off - exe.HEADER)
    return out


def width_sites(exe: Exe) -> list[int]:
    """뱅크1 폭표를 가리키는 `addiu rX, rY, 0x23bc` 를 전부 찾는다."""
    out = []
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        word = int.from_bytes(exe.data[off:off + 4], "little")
        if word >> 26 == 9 and (word & 0xFFFF) == BANK1_TABLE_OLD:
            out.append(exe.load + off - exe.HEADER)
    return out


def apply(exe: Exe, show: bool) -> list[tuple[int, str]]:
    done: list[tuple[int, str]] = []

    expect(exe, CLUT_LIMIT, 0x28420011, "slti v0, v0, 0x11")
    exe.put_word(CLUT_LIMIT, int.from_bytes(
        assemble("slti v0, v0, 0x21", CLUT_LIMIT), "little"))
    done.append((CLUT_LIMIT, "CLUT 높이 제한 16 -> 32"))

    expect(exe, WIDTH_COPY, 0x24C801C0, "addiu t0, a2, 0x1c0")
    exe.put_word(WIDTH_COPY, int.from_bytes(
        assemble("addiu t0, a2, 0x370", WIDTH_COPY), "little"))
    done.append((WIDTH_COPY, "폭표 복사 452 -> 884바이트"))

    expect(exe, BANK_BRANCH, 0x15000022, "bne t0, zero, 뱅크1 갈래")
    exe.put_word(BANK_BRANCH, int.from_bytes(
        assemble(f"bne t0, zero, {LOADER_RETURN:#x}", BANK_BRANCH), "little"))
    done.append((BANK_BRANCH, "필드 한자표 적재 차단"))

    expect(exe, CLUT_X_SITE, 0x24020120, "addiu v0, zero, 0x120")
    exe.put_word(CLUT_X_SITE, int.from_bytes(
        assemble(f"addiu v0, zero, {CLUT_VRAM_X:#x}", CLUT_X_SITE), "little"))
    expect(exe, CLUT_Y_SITE, 0x240200E0, "addiu v0, zero, 0xe0")
    exe.put_word(CLUT_Y_SITE, int.from_bytes(
        assemble(f"addiu v0, zero, {CLUT_VRAM_Y:#x}", CLUT_Y_SITE), "little"))
    done.append((CLUT_X_SITE,
                 f"CLUT 적재 위치 (288,224) -> ({CLUT_VRAM_X},{CLUT_VRAM_Y})"))

    ids = clut_id_sites(exe)
    if len(ids) != 5:
        raise ValueError(f"CLUT id 기준값이 {len(ids)}곳이다. 5곳이어야 한다")
    for addr in ids:
        word = exe.word(addr)
        exe.put_word(addr, (word & ~0xFFFF) | CLUT_ID_NEW)
    done.append((ids[0],
                 f"CLUT id 기준 {len(ids)}곳 {CLUT_ID_OLD:#x} -> {CLUT_ID_NEW:#x}"))

    sites = width_sites(exe)
    if len(sites) != 7:
        raise ValueError(f"뱅크1 폭표 참조가 {len(sites)}곳이다. 7곳이어야 한다")
    for addr in sites:
        word = exe.word(addr)
        exe.put_word(addr, (word & ~0xFFFF) | BANK1_TABLE_NEW)
    done.append((sites[0], f"뱅크1 폭표 {len(sites)}곳 -> 통합표 +441"))

    for branch, tp, idx, off, prim in DRAW_SITES:
        code = assemble(DRAW_PATCH.format(tp=tp, idx=idx, off=off, prim=prim),
                        branch)
        if len(code) != 24:
            raise ValueError(f"{branch:#x}: 패치가 {len(code)}바이트다. 24 여야 한다")
        for i in range(6):
            exe.put_word(branch + i * 4,
                         int.from_bytes(code[i * 4:i * 4 + 4], "little"))
        done.append((branch, "tpage 분기 -> CLUT 에 뱅크비트 더하기"))
        if show:
            print(f"\n  {branch:#010x} 새 6슬롯")
            for i in range(6):
                a = branch + i * 4
                print(f"    {a:#010x}  {exe.word(a):08x}  "
                      f"{decode(exe.word(a), a)}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=Path("work/disc1/SLPS_018.80"))
    parser.add_argument("--output", type=Path,
                        default=Path("work/patch-4plane/SLPS_018.80"))
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    try:
        exe = Exe(str(args.exe))
        done = apply(exe, args.show)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes(exe.data))
    before = Exe(str(args.exe))
    changed = sum(1 for i in range(0, exe.size, 4)
                  if before.data[exe.HEADER + i:exe.HEADER + i + 4]
                  != exe.data[exe.HEADER + i:exe.HEADER + i + 4])
    print(f"패치 {len(done)}건, 바뀐 워드 {changed}개")
    for addr, what in done:
        print(f"  {addr:#010x}  {what}")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
