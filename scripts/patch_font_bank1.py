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
온다. 폰트 파일을 조금 키워 뒤에 우리 루틴을 붙여 두면, CD 읽기가 끝난 순간
루틴은 이미 RAM 에 앉아 있다. **EXE 에 빈 공간을 찾을 필요가 없다.**

`0x801B0000` 은 폰트 전용이 아니라 **공용 적재 버퍼**다. 실행 중 RAM 을 읽어
보면 부팅이 끝난 뒤 그 자리에 다른 데이터가 들어와 있다. 그래서 두 뱅크를 한
파일에 담아 34KB 를 더 쓰지 않는다. 대신 **CD 를 두 번 읽어 같은 버퍼를
재사용**한다. 뱅크0 을 VRAM 에 올린 뒤 그 버퍼에 뱅크1 을 덮어 읽는 것이다.

    버퍼 (0x801B0000)
      +0x0000  뱅크0 파일           -> VRAM x=960 에 올린 뒤 볼일이 끝난다
      +0x8800  훅 루틴              <- 섹터 경계 뒤. 뱅크1 읽기가 여기까지 안 온다
      (그 뒤 뱅크1 파일을 +0x0000 에 덮어 읽는다)

훅을 0x8800 에 두는 이유가 핵심이다. 뱅크1 파일은 17섹터=34,816바이트라 아무리
많이 읽어도 `0x801B87FF` 에서 멈춘다. 스톡도 이미 17섹터를 읽으므로 **늘어나는
RAM 은 훅 길이뿐**이다. "그 자리가 비어 있을 것"이라는 가정이 사라진다.

## EXE 는 워드 두 개만 바뀐다

    0x80011cdc  jal sub_8002C358      ->  jal 훅
    0x8002c3b8  bne t0,zero,뱅크1갈래  ->  bne t0,zero,0x8002c51c  (즉시 반환)

둘째가 핵심이다. 뱅크1 로 부르는 코드는 메인 EXE 에 없고 오버레이에 있다 —
`0x8002C358` 의 주소를 값으로 만드는 자리가 EXE 전체에 하나도 없다. 오버레이가
몇 개든 전부 이 함수라는 한 길목을 지나므로, 그 안에서 막으면 전부 막힌다.

    python3 scripts/patch_font_bank1.py --show
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

BUFFER = 0x801B0000        # 부팅 코드가 폰트를 읽어 들이는 공용 버퍼
LOADER = 0x8002C358        # 폰트 적재 함수
LOAD_IMAGE = 0x80048DA0    # libgpu LoadImage(RECT*, u_long*)
CD_READ = 0x800386B0       # 읽기 요청(lba, 바이트, 목적지, 0)
CD_POLL = 0x8003924C       # 끝날 때까지 0 이 아닌 값을 돌려준다
HOOK_SITE = 0x80011CDC     # 부팅의 `jal sub_8002C358`
BANK_BRANCH = 0x8002C3B8   # `bne t0, zero, 뱅크1 갈래`
LOADER_RETURN = 0x8002C51C # 함수 안의 "아무것도 안 하고 반환" 자리
BANK1_WIDTHS = 0x800823BC  # 뱅크1 폭 테이블이 놓이는 RAM 주소
COPY_WORDS = 113           # 452바이트 = sub_8002C358 이 복사하는 길이

SECTOR = 2048
HOOK_AT = 0x8800           # 17섹터 뒤. 뱅크1 읽기가 여기까지 오지 못한다
BANK1_RECT = (832, 256, 64, 252)   # VRAM x, y, 폭(halfword), 높이
TAIL_LBA = 311_369         # 어떤 파일 범위에도 TOC 참조에도 없는 첫 섹터

HOOK = """
    addiu sp, sp, -0x20
    sw    ra, 0x1c(sp)

    ; 뱅크0 — 원래 하던 일. 버퍼에는 아직 뱅크0 파일이 들어 있다.
    ; 인자를 다시 세우므로 부르는 쪽의 지연 슬롯에 기대지 않는다
    lui   a0, BUFFER_HI
    jal   LOADER
    addu  a1, zero, zero

    ; 뱅크1 파일을 같은 버퍼로 덮어 읽는다
    lui   a0, LBA_HI
    ori   a0, a0, LBA_LO
    ori   a1, zero, SIZE_LO
    lui   a2, BUFFER_HI
    jal   CD_READ
    addu  a3, zero, zero
wait:
    jal   CD_POLL
    nop
    bne   v0, zero, wait
    nop

    ; 뱅크1 폭 테이블 452바이트를 제자리로 옮긴다
    lui   a0, BUFFER_HI
    ori   a0, a0, WT_LO
    lui   a1, TABLE_HI
    ori   a1, a1, TABLE_LO
    ori   a2, zero, COPY_WORDS
copy:
    lw    v0, 0x0(a0)
    addiu a2, a2, -0x1
    addiu a0, a0, 0x4
    sw    v0, 0x0(a1)
    bne   a2, zero, copy
    addiu a1, a1, 0x4

    ; 뱅크1 픽셀을 VRAM 으로. RECT 는 파일 안 것을 그대로 넘긴다
    lui   a0, BUFFER_HI
    ori   a0, a0, RECT_LO
    lui   a1, BUFFER_HI
    jal   LOAD_IMAGE
    ori   a1, a1, PIXELS_LO

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

    def word(off: int) -> int:
        return int.from_bytes(data[off:off + 4], "little")

    widths, chunk = word(0), word(4)
    if not chunk:
        raise ValueError(f"{name}: 이미지 청크 오프셋이 0 이라 로더가 그냥 반환한다")
    image = chunk + 8 + word(chunk + 8)
    rect, pixels = image + 4, image + 12
    if pixels > len(data):
        raise ValueError(f"{name}: 픽셀 시작 {pixels:#x} 가 파일 끝을 넘는다")
    for label, off in (("RECT", rect), ("폭 테이블", widths), ("픽셀", pixels)):
        if off % 4:
            raise ValueError(f"{name}: {label} 가 {off:#x} 로 4정렬이 아니다")

    return {"widths": widths, "rect": rect, "pixels": pixels,
            "shape": struct.unpack_from("<4H", data, rect), "bytes": len(data)}


def build_hook(font: dict[str, int], lba: int, size: int) -> bytes:
    """훅을 조립한다. 뱅크1 의 오프셋은 파일이 말하는 대로 쓴다."""
    if lba >> 32:
        raise ValueError(f"LBA {lba} 가 32비트를 넘는다")
    if size >= 0x10000:
        raise ValueError(f"뱅크1 파일 {size:,}바이트는 16비트 즉시값에 안 들어간다")
    return assemble(HOOK, BUFFER + HOOK_AT, {
        "BUFFER_HI": BUFFER >> 16,
        "LBA_HI": lba >> 16, "LBA_LO": lba & 0xFFFF,
        "SIZE_LO": size,
        "WT_LO": font["widths"], "RECT_LO": font["rect"],
        "PIXELS_LO": font["pixels"],
        "TABLE_HI": BANK1_WIDTHS >> 16, "TABLE_LO": BANK1_WIDTHS & 0xFFFF,
        "COPY_WORDS": COPY_WORDS,
        "LOAD_IMAGE": LOAD_IMAGE, "LOADER": LOADER,
        "CD_READ": CD_READ, "CD_POLL": CD_POLL,
    })


def check_exe(exe: Exe) -> None:
    """패치 자리가 우리가 아는 그 명령인지 확인한다.

    다른 판본이나 이미 손댄 EXE 에 덮어쓰면 조용히 망가진다.
    """
    for addr, want in ((HOOK_SITE, 0x0C00B0D6), (BANK_BRANCH, 0x15000022)):
        got = exe.word(addr)
        if got != want:
            raise ValueError(
                f"{addr:#010x} 가 예상과 다르다: {got:08x} (기대 {want:08x}) — "
                "다른 판본이거나 이미 패치된 EXE 다")


def verify(head: bytes, bank0: bytes, bank1: bytes, hook: bytes,
           font: dict[str, int]) -> list[str]:
    """만든 파일이 스스로와 맞는지 본다.

    주소 산술이 한 칸만 어긋나도 글리프 대신 쓰레기가 올라가는데, 화면만 보고는
    원인을 좁히기 어렵다. 만들 때 확인해 둔다.
    """
    problems: list[str] = []
    if head[:len(bank0)] != bank0:
        problems.append("뱅크0 이 파일 머리에 그대로 있지 않다")
    if head[HOOK_AT:HOOK_AT + len(hook)] != hook:
        problems.append(f"훅이 +{HOOK_AT:#x} 에 없다")
    if HOOK_AT < len(bank0):
        problems.append(f"훅 자리 {HOOK_AT:#x} 가 뱅크0({len(bank0):#x})을 침범한다")
    read_end = -(-len(bank1) // SECTOR) * SECTOR
    if read_end > HOOK_AT:
        problems.append(f"뱅크1 읽기가 {read_end:#x} 까지 와 훅({HOOK_AT:#x})을 덮는다")
    if font["shape"] != BANK1_RECT:
        problems.append(f"뱅크1 RECT 가 {font['shape']} 다 (기대 {BANK1_RECT})")
    if len(bank1) - font["pixels"] != BANK1_RECT[2] * BANK1_RECT[3] * 2:
        problems.append("뱅크1 픽셀 길이가 RECT 와 안 맞는다")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank0", type=Path, default=Path("work/font/bank00.bin"))
    parser.add_argument("--bank1", type=Path, default=Path("work/font/bank01.bin"))
    parser.add_argument("--exe", type=Path, default=Path("work/disc1/SLPS_018.80"))
    parser.add_argument("--output", type=Path, default=Path("work/patch"))
    parser.add_argument("--lba", type=int, default=TAIL_LBA,
                        help="두 파일을 놓을 첫 섹터 (설치기와 같아야 한다)")
    parser.add_argument("--show", action="store_true",
                        help="조립한 훅을 도로 해체해 보여 준다")
    args = parser.parse_args()

    for path in (args.bank0, args.bank1, args.exe):
        if not path.exists():
            print(f"없는 파일: {path}", file=sys.stderr)
            return 2

    bank0, bank1 = args.bank0.read_bytes(), args.bank1.read_bytes()
    try:
        parse_font(bank0, str(args.bank0))
        font = parse_font(bank1, str(args.bank1))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    # 파일 A 는 TOC #130 자리에, 파일 B 는 그 바로 뒤에 놓는다.
    head_sectors = -(-(HOOK_AT + len(HOOK.split())) // SECTOR)   # 대략값, 아래서 확정
    bank1_lba = args.lba + max(head_sectors, -(-HOOK_AT // SECTOR) + 1)
    hook = build_hook(font, bank1_lba, len(bank1))
    head = bytearray(bank0)
    head += bytes(HOOK_AT - len(head))
    head += hook
    # 파일 A 가 섹터 몇 개인지 확정되면 파일 B 의 LBA 도 확정된다. 어긋나면
    # 다시 조립한다 — 훅 안에 그 LBA 가 박혀 있기 때문이다.
    settled = args.lba + -(-len(head) // SECTOR)
    if settled != bank1_lba:
        hook = build_hook(font, settled, len(bank1))
        head = bytearray(bank0) + bytes(HOOK_AT - len(bank0)) + hook
        bank1_lba = settled

    problems = verify(bytes(head), bank0, bank1, hook, font)
    if problems:
        print("만든 파일이 스스로와 어긋난다:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    try:
        exe = Exe(str(args.exe))
        check_exe(exe)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1

    hook_addr = BUFFER + HOOK_AT
    exe.put_word(HOOK_SITE, int.from_bytes(
        assemble(f"jal {hook_addr:#x}", HOOK_SITE), "little"))
    exe.put_word(BANK_BRANCH, int.from_bytes(
        assemble(f"bne t0, zero, {LOADER_RETURN:#x}", BANK_BRANCH), "little"))

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "font-head.bin").write_bytes(bytes(head))
    (args.output / "font-bank1.bin").write_bytes(bank1)
    (args.output / args.exe.name).write_bytes(bytes(exe.data))

    plan = {
        "buffer": BUFFER,
        "head_lba": args.lba,
        "head_bytes": len(head),
        "head_sectors": -(-len(head) // SECTOR),
        "bank1_lba": bank1_lba,
        "bank1_bytes": len(bank1),
        "bank1_sectors": -(-len(bank1) // SECTOR),
        "hook": hook_addr,
        "hook_bytes": len(hook),
        "exe_patches": {
            f"{HOOK_SITE:#010x}": f"jal {hook_addr:#010x}",
            f"{BANK_BRANCH:#010x}": f"bne t0, zero, {LOADER_RETURN:#010x}",
        },
    }
    (args.output / "bank1-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"파일 A (TOC #130)  LBA {args.lba:,}  {len(head):,}바이트 "
          f"({plan['head_sectors']}섹터)  = 뱅크0 + 훅")
    print(f"파일 B (훅이 읽는다) LBA {bank1_lba:,}  {len(bank1):,}바이트 "
          f"({plan['bank1_sectors']}섹터)  = 뱅크1")
    print(f"  훅   {hook_addr:#010x}  {len(hook)}바이트 ({len(hook) // 4}명령)")
    print(f"  버퍼 {BUFFER:#010x} ~ {BUFFER + len(head):#010x}  "
          f"— 스톡 17섹터({BUFFER + 17 * SECTOR:#010x})보다 "
          f"{BUFFER + len(head) - (BUFFER + 17 * SECTOR):+,}바이트")
    print("EXE 패치 2곳")
    for where, what in plan["exe_patches"].items():
        print(f"  {where}  {what}")
    print(f"→ {args.output}/")

    if args.show:
        print("\n조립한 훅을 도로 해체한 것")
        for index, text in enumerate(disassemble(hook, hook_addr)):
            word = int.from_bytes(hook[index * 4:index * 4 + 4], "little")
            print(f"  {hook_addr + index * 4:#010x}  {word:08x}  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
