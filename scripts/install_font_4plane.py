#!/usr/bin/env python3
"""4중 인터리브 폰트와 패치한 EXE 를 디스크 사본에 설치한다.

폰트는 **원래 자리(LBA 849)에 그대로** 들어간다. 34,204바이트로 원본(33,764)
보다 크지만 그 자리에 배정된 17섹터(34,816)를 넘지 않는다. 그래서 LBA 는 안
옮기고 **TOC 의 크기 필드 4바이트만** 고친다.

EXE 는 `patch_font_4plane.py` 가 만든 것을 쓴다 — 바뀐 워드 34개다.

    python3 scripts/install_font_4plane.py --dry-run
    python3 scripts/install_font_4plane.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_disc as PD                     # noqa: E402
import psx_sector as PS                     # noqa: E402

TOC_LBA = 826
FONT_ENTRY = 130
FONT_LBA = 849
FONT_SECTORS = 17                           # 원본에 배정된 섹터 수
EXE_LBA = 24


def toc_entry(path: Path, index: int) -> tuple[int, int]:
    table = PD.read_user(path, TOC_LBA, PS.USER_SIZE)
    return struct.unpack_from("<II", table, index * 8)


def set_toc_entry(path: Path, index: int, lba: int, size: int) -> None:
    table = bytearray(PD.read_user(path, TOC_LBA, PS.USER_SIZE))
    struct.pack_into("<II", table, index * 8, lba, size)
    PD.write_user(path, TOC_LBA, bytes(table))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # `build_font_4plane.py` 의 출력과 같은 자리를 가리켜야 한다. 한때 여기가
    # work/font-4plane/font.bin (옛 배치로 구운 것) 을 가리켜, 새 배치로 구운
    # 폰트를 두고도 옛 폰트를 설치할 뻔했다 — `docs/lessons.md` 16번과 같은 부류다.
    parser.add_argument("--font", type=Path,
                        default=Path("work/font-all/font.bin"))
    parser.add_argument("--exe", type=Path,
                        default=Path("work/patch-4plane/SLPS_018.80"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for path in (args.font, args.exe):
        if not path.exists():
            print(f"없는 파일: {path}", file=sys.stderr)
            return 2
    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}\n"
              "  python3 scripts/patch_disc.py --init", file=sys.stderr)
        return 2

    font = args.font.read_bytes()
    exe = args.exe.read_bytes()
    room = FONT_SECTORS * PS.USER_SIZE
    if len(font) > room:
        print(f"폰트가 {len(font):,}바이트다. LBA {FONT_LBA} 에 배정된 "
              f"{FONT_SECTORS}섹터({room:,}바이트)를 넘는다.", file=sys.stderr)
        return 1

    old_lba, old_size = toc_entry(PD.PATCH_BIN, FONT_ENTRY)
    print(f"TOC #{FONT_ENTRY}  LBA {old_lba:,} / {old_size:,}바이트")
    print(f"  -> LBA {FONT_LBA:,} / {len(font):,}바이트  "
          f"(LBA 는 그대로, 크기만 바뀐다)")
    print(f"폰트  {len(font):,}바이트 = {-(-len(font) // PS.USER_SIZE)}섹터 "
          f"/ 배정 {FONT_SECTORS}섹터")
    print(f"EXE   {len(exe):,}바이트")
    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    print(f"폰트 {PD.write_user(PD.PATCH_BIN, FONT_LBA, font)}섹터를 썼다")
    print(f"EXE {PD.write_user(PD.PATCH_BIN, EXE_LBA, exe)}섹터를 썼다")
    set_toc_entry(PD.PATCH_BIN, FONT_ENTRY, FONT_LBA, len(font))

    problems = []
    if toc_entry(PD.PATCH_BIN, FONT_ENTRY) != (FONT_LBA, len(font)):
        problems.append("TOC 가 쓴 대로 안 읽힌다")
    if PD.read_user(PD.PATCH_BIN, FONT_LBA, len(font)) != font:
        problems.append("폰트가 쓴 대로 안 읽힌다")
    if PD.read_user(PD.PATCH_BIN, EXE_LBA, len(exe)) != exe:
        problems.append("EXE 가 쓴 대로 안 읽힌다")
    if problems:
        print("\n되읽기가 어긋난다:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("\n되읽기 확인 — TOC, 폰트, EXE 모두 일치")
    print(f"→ {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
