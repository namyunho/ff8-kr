#!/usr/bin/env python3
"""폰트 뱅크1 을 한글로 되찾는다. 글자 수 한계를 882 에서 1,764 로 올린다.

## 왜 필요한가

번역문이 쓰는 서로 다른 글자는 1,092 개인데 뱅크 하나는 882 칸이다. 뱅크0 만
쓰면 상위 826 자만 담을 수 있고 264 건(3.3%)을 다시 써야 한다. 뱅크1 을 쓰면
그럴 필요가 없다.

뱅크1 은 원래 **필드마다 다른 한자표(TDW)** 가 들어가는 자리다. 필드에 들어갈
때마다 새로 적재되므로 한글을 넣어 두어도 곧 덮인다. 그런데 한국어판에서 필드
한자표는 참조될 일이 없다 — 본문이 전부 한글이기 때문이다. 그래서 **적재를
막고 그 자리를 뺏는다.**

## 어떻게

부팅 때 폰트를 읽는 자리(`sub_80011CA8`)를 보면 읽을 길이가 TOC 의 크기 필드에서
온다. 폰트 파일을 키우면 CD 읽기가 알아서 더 읽어 온다. 그 뒤에 우리 루틴을
붙여 두면 CD 읽기가 끝난 순간 루틴은 이미 RAM 에 앉아 있다. **EXE 에 빈 공간을
찾을 필요가 없다.**

    파일 배치 (base = 0x801B0000, CD 가 여기로 읽는다)
      +0        뱅크0 폰트 파일 통째 — 로더가 헤더부터 읽는다
      +A        훅 루틴
      +B        뱅크1 폰트 파일 통째 — 훅이 폭 테이블과 픽셀을 꺼내 쓴다

EXE 는 워드 **두 개**만 바뀐다.

    0x80011cdc  jal sub_8002C358      ->  jal 훅
    0x8002c3b8  bne t0,zero,뱅크1갈래  ->  bne t0,zero,0x8002c51c  (즉시 반환)

둘째가 핵심이다. 뱅크1 로 부르는 코드는 메인 EXE 에 없고 오버레이에 있다 —
`0x8002C358` 의 주소를 값으로 만드는 자리가 EXE 전체에 하나도 없다. 오버레이가
몇 개든 전부 이 함수라는 한 길목을 지나므로, 그 안에서 막으면 전부 막힌다.

    python3 scripts/patch_font_bank1.py --bank1 work/font/bank01.bin
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mips_asm import assemble, disassemble          # noqa: E402
from mips_dis import Exe                            # noqa: E402

BUFFER = 0x801B0000        # 부팅 코드가 폰트를 읽어 들이는 주소
LOADER = 0x8002C358        # 폰트 적재 함수
LOAD_IMAGE = 0x80048DA0    # libgpu LoadImage(RECT*, u_long*)
HOOK_SITE = 0x80011CDC     # 부팅의 `jal sub_8002C358`
BANK_BRANCH = 0x8002C3B8   # `bne t0, zero, 뱅크1 갈래`
LOADER_RETURN = 0x8002C51C # 함수 안의 "아무것도 안 하고 반환" 자리
BANK1_WIDTHS = 0x800823BC  # 뱅크1 폭 테이블이 놓이는 RAM 주소
COPY_WORDS = 113           # 452바이트 = sub_8002C358 이 복사하는 길이

BANK1_RECT = (832, 256, 64, 252)   # VRAM x, y, 폭(halfword), 높이

# 훅. 뱅크1 을 먼저 올리고 뱅크0 은 원래 함수에 맡긴다. 순서가 이런 이유는
# 원래 함수가 마지막에 하는 뒷정리(0x8002c0e0)를 그대로 물려받기 위해서다.
HOOK = """
    addiu sp, sp, -0x20
    sw    ra, 0x1c(sp)

    ; 뱅크1 폭 테이블 452바이트를 제자리로 옮긴다
    lui   a0, WT_HI
    ori   a0, a0, WT_LO
    lui   a1, TABLE_HI
    ori   a1, a1, TABLE_LO
    addiu a2, zero, COPY_WORDS
copy:
    lw    v0, 0x0(a0)
    addiu a2, a2, -0x1
    addiu a0, a0, 0x4
    sw    v0, 0x0(a1)
    bne   a2, zero, copy
    addiu a1, a1, 0x4

    ; 뱅크1 픽셀을 VRAM 으로. RECT 는 파일 안 것을 그대로 넘긴다
    lui   a0, RECT_HI
    ori   a0, a0, RECT_LO
    lui   a1, PIXELS_HI
    jal   LOAD_IMAGE
    ori   a1, a1, PIXELS_LO

    ; 원래 하던 뱅크0 적재. 인자를 다시 세우므로 부르는 쪽에 기대지 않는다
    lui   a0, BUFFER_HI
    jal   LOADER
    addu  a1, zero, zero

    lw    ra, 0x1c(sp)
    addiu sp, sp, 0x20
    jr    ra
    nop
"""


def parse_font(data: bytes, name: str) -> dict[str, int]:
    """폰트 컨테이너에서 폭 테이블·RECT·픽셀의 위치를 꺼낸다.

    `sub_8002C358` 이 읽는 순서를 그대로 따라간다. 짐작하지 않고 파일이 말하는
    대로 읽어야 폰트 생성기가 배치를 바꿔도 따라간다.
    """
    if len(data) < 0x20:
        raise ValueError(f"{name}: 너무 짧다 ({len(data)}바이트)")
    word = lambda off: int.from_bytes(data[off:off + 4], "little")  # noqa: E731

    widths, chunk = word(0), word(4)
    if not chunk:
        raise ValueError(f"{name}: 이미지 청크 오프셋이 0 이라 로더가 그냥 반환한다")
    image = chunk + 8 + word(chunk + 8)
    rect = image + 4
    pixels = image + 12
    if pixels > len(data):
        raise ValueError(f"{name}: 픽셀 시작 {pixels:#x} 가 파일 끝을 넘는다")
    if rect % 4:
        raise ValueError(f"{name}: RECT 가 {rect:#x} 로 4정렬이 아니다")
    if widths % 4:
        raise ValueError(f"{name}: 폭 테이블이 {widths:#x} 로 4정렬이 아니다")

    shape = struct.unpack_from("<4H", data, rect)
    return {"widths": widths, "rect": rect, "pixels": pixels,
            "shape": shape, "bytes": len(data)}


def build_hook(base: int, font: dict[str, int], bank1_at: int) -> bytes:
    def hi(addr: int) -> int:
        return (addr >> 16) & 0xFFFF

    def lo(addr: int) -> int:
        return addr & 0xFFFF

    widths = BUFFER + bank1_at + font["widths"]
    rect = BUFFER + bank1_at + font["rect"]
    pixels = BUFFER + bank1_at + font["pixels"]
    return assemble(HOOK, base, {
        "WT_HI": hi(widths), "WT_LO": lo(widths),
        "TABLE_HI": hi(BANK1_WIDTHS), "TABLE_LO": lo(BANK1_WIDTHS),
        "RECT_HI": hi(rect), "RECT_LO": lo(rect),
        "PIXELS_HI": hi(pixels), "PIXELS_LO": lo(pixels),
        "BUFFER_HI": hi(BUFFER),
        "COPY_WORDS": COPY_WORDS,
        "LOAD_IMAGE": LOAD_IMAGE, "LOADER": LOADER,
    })


def verify_merged(merged: bytes, bank0: bytes, bank1: bytes,
                  plan: dict[str, int]) -> list[str]:
    """훅이 짚는 주소가 실제로 그 바이트를 가리키는지 본다.

    주소 산술이 한 칸만 어긋나도 글리프 대신 쓰레기가 올라가는데, 그때 화면에
    보이는 것만으로는 원인을 좁히기 어렵다. 만들 때 확인해 둔다.
    """
    def at(addr: int) -> int:
        return addr - BUFFER

    problems: list[str] = []

    def want(label: str, got: object, expected: object) -> None:
        if got != expected:
            problems.append(f"{label}: {got!r} (기대 {expected!r})")

    want("뱅크0 이 파일 머리에 그대로", merged[:len(bank0)], bank0)
    want("폭표 원본", merged[at(plan["bank1_widths_src"]):
                        at(plan["bank1_widths_src"]) + 16], bank1[8:24])
    want("RECT", struct.unpack_from("<4H", merged, at(plan["bank1_rect_src"])),
         BANK1_RECT)
    want("픽셀 길이", len(merged) - at(plan["bank1_pixels_src"]),
         BANK1_RECT[2] * BANK1_RECT[3] * 2)
    want("훅 4정렬", plan["hook"] % 4, 0)
    if plan["hook"] + plan["hook_bytes"] > plan["bank1_at"]:
        problems.append("훅이 뱅크1 데이터를 침범한다")
    return problems


def check_exe(exe: Exe) -> None:
    """패치 자리가 우리가 아는 그 명령인지 확인한다.

    다른 판본이나 이미 손댄 EXE 에 덮어쓰면 조용히 망가진다. 원본 워드를
    확인하고 다르면 멈춘다.
    """
    expected = {HOOK_SITE: 0x0C00B0D6, BANK_BRANCH: 0x15000022}
    for addr, want in expected.items():
        got = exe.word(addr)
        if got != want:
            raise ValueError(
                f"{addr:#010x} 가 예상과 다르다: {got:08x} (기대 {want:08x}) — "
                "다른 판본이거나 이미 패치된 EXE 다")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank0", type=Path, default=Path("work/font/bank00.bin"))
    parser.add_argument("--bank1", type=Path, default=Path("work/font/bank01.bin"))
    parser.add_argument("--exe", type=Path, default=Path("work/disc1/SLPS_018.80"))
    parser.add_argument("--output", type=Path, default=Path("work/patch"))
    parser.add_argument("--show", action="store_true",
                        help="조립한 훅을 도로 해체해 보여 준다")
    args = parser.parse_args()

    for path in (args.bank0, args.bank1, args.exe):
        if not path.exists():
            print(f"없는 파일: {path}", file=sys.stderr)
            return 2

    bank0 = args.bank0.read_bytes()
    bank1 = args.bank1.read_bytes()
    try:
        head = parse_font(bank0, str(args.bank0))
        tail = parse_font(bank1, str(args.bank1))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    if tail["shape"] != BANK1_RECT:
        print(f"{args.bank1}: RECT 가 {tail['shape']} 다. 뱅크1 은 {BANK1_RECT} "
              "이어야 한다 — 로더가 덮어쓰지 않는 경로라 파일 값이 그대로 쓰인다.",
              file=sys.stderr)
        return 1

    # 훅 길이를 알아야 뱅크1 위치가 정해지고, 뱅크1 위치를 알아야 훅을 조립할
    # 수 있다. 길이는 명령 수로 정해지므로 한 번 재 보고 다시 조립한다.
    hook_at = (len(bank0) + 3) & ~3
    probe = build_hook(BUFFER + hook_at, tail, hook_at + 0x1000)
    bank1_at = (hook_at + len(probe) + 3) & ~3
    hook = build_hook(BUFFER + hook_at, tail, bank1_at)
    if len(hook) != len(probe):
        print("훅 길이가 흔들린다 — 조립기가 이상하다", file=sys.stderr)
        return 1

    merged = bytearray(bank0)
    merged += bytes(hook_at - len(merged))
    merged += hook
    merged += bytes(bank1_at - len(merged))
    merged += bank1

    try:
        exe = Exe(str(args.exe))
        check_exe(exe)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1

    hook_addr = BUFFER + hook_at
    exe.put_word(HOOK_SITE, int.from_bytes(
        assemble(f"jal {hook_addr:#x}", HOOK_SITE), "little"))
    exe.put_word(BANK_BRANCH, int.from_bytes(
        assemble(f"bne t0, zero, {LOADER_RETURN:#x}", BANK_BRANCH), "little"))

    args.output.mkdir(parents=True, exist_ok=True)
    font_out = args.output / "font-bank01.bin"
    exe_out = args.output / args.exe.name
    font_out.write_bytes(bytes(merged))
    exe_out.write_bytes(bytes(exe.data))

    plan = {
        "buffer": BUFFER,
        "font_bytes": len(merged),
        "font_sectors": -(-len(merged) // 2048),
        "hook": hook_addr,
        "hook_bytes": len(hook),
        "bank1_at": BUFFER + bank1_at,
        "bank1_widths_src": BUFFER + bank1_at + tail["widths"],
        "bank1_rect_src": BUFFER + bank1_at + tail["rect"],
        "bank1_pixels_src": BUFFER + bank1_at + tail["pixels"],
        "buffer_end": BUFFER + len(merged),
        "exe_patches": {
            f"{HOOK_SITE:#010x}": f"jal {hook_addr:#010x}",
            f"{BANK_BRANCH:#010x}": f"bne t0, zero, {LOADER_RETURN:#010x}",
        },
    }
    problems = verify_merged(bytes(merged), bank0, bank1, plan)
    if problems:
        print("만든 파일이 스스로와 어긋난다:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    (args.output / "bank1-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    original = args.bank0.stat().st_size
    print(f"폰트 파일 {len(merged):,}바이트 ({plan['font_sectors']}섹터) "
          f"— 원본 {original:,}바이트(17섹터)에서 늘어난다")
    print(f"  버퍼   {BUFFER:#010x} ~ {BUFFER + len(merged):#010x}")
    print(f"  훅     {hook_addr:#010x}  {len(hook)}바이트 "
          f"({len(hook) // 4}명령)")
    print(f"  뱅크1  {BUFFER + bank1_at:#010x}  폭표 "
          f"{plan['bank1_widths_src']:#010x}  픽셀 "
          f"{plan['bank1_pixels_src']:#010x}")
    print(f"  RECT   x={tail['shape'][0]} y={tail['shape'][1]} "
          f"w={tail['shape'][2]} h={tail['shape'][3]}")
    print("EXE 패치 2곳")
    for where, what in plan["exe_patches"].items():
        print(f"  {where}  {what}")
    print(f"→ {font_out}")
    print(f"→ {exe_out}")

    if args.show:
        print("\n조립한 훅을 도로 해체한 것")
        for index, text in enumerate(disassemble(hook, hook_addr)):
            word = int.from_bytes(hook[index * 4:index * 4 + 4], "little")
            print(f"  {hook_addr + index * 4:#010x}  {word:08x}  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
