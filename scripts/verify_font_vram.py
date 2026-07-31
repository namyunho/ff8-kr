#!/usr/bin/env python3
"""실행 중인 VRAM 을 폰트 파일과 바이트 단위로 대조한다.

뱅크1 패치가 먹었는지 판정하는 데 화면을 보는 것은 약한 증거다. 글자가 이상해
보여도 그것이 적재 실패인지 배치표가 달라서인지 구별되지 않는다. **VRAM 을
직접 읽어 폰트 파일과 맞춰 보면** 그 구별이 필요 없다.

    뱅크0  VRAM x=960..1023, y=256..507   (스톡 경로가 올린다)
    뱅크1  VRAM x=832..895,  y=256..507   (우리 훅이 올린다)

한 행은 64 halfword 다. 4bpp 라 256픽셀이 128바이트에 들어간다.

    python3 scripts/verify_font_vram.py
    python3 scripts/verify_font_vram.py --ram      RAM 쪽 폭 테이블도 본다
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psx_live as LIVE                     # noqa: E402
from patch_font_bank1 import parse_font     # noqa: E402

VRAM_W = 1024
ROW_HALFWORDS = 64
HEIGHT = 252
BANKS = ((0, 960, Path("work/font/bank00.bin"), 0x800821F8),
         (1, 832, Path("work/font/bank01.bin"), 0x800823BC))
WIDTH_BYTES = 452


def vram_rows(blob: bytes, x: int, y: int) -> bytes:
    """그 자리의 픽셀을 파일에 실린 것과 같은 순서로 편다."""
    out = bytearray()
    for row in range(HEIGHT):
        start = ((y + row) * VRAM_W + x) * 2
        out += blob[start:start + ROW_HALFWORDS * 2]
    return bytes(out)


def compare(name: str, got: bytes, want: bytes) -> bool:
    if got == want:
        print(f"  ✔ {name}: {len(want):,}바이트 전부 일치")
        return True
    if not any(got):
        print(f"  ✗ {name}: 전부 0 — 아무것도 올라오지 않았다")
        return False
    same = sum(1 for a, b in zip(got, want) if a == b)
    first = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
    print(f"  ✗ {name}: {same:,}/{len(want):,}바이트만 같다 "
          f"({same / len(want):.1%}), 처음 어긋난 곳 +{first:#x}")
    print(f"      VRAM {got[first:first + 12].hex(' ')}")
    print(f"      파일 {want[first:first + 12].hex(' ')}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--y", type=int, default=256)
    parser.add_argument("--ram", action="store_true",
                        help="폭 테이블이 RAM 에 올라왔는지도 본다")
    args = parser.parse_args()

    try:
        vram = LIVE.fetch("gpu/vram/raw")
    except OSError as error:
        print(f"에뮬레이터에 붙지 못했다: {error}\n"
              "  PCSX-Redux 에서 Enable Web Server 를 켠다", file=sys.stderr)
        return 2

    good = True
    print("VRAM 과 폰트 파일 대조")
    for index, x, path, table in BANKS:
        if not path.exists():
            print(f"  - 뱅크{index}: {path} 가 없다 (건너뜀)")
            continue
        data = path.read_bytes()
        want = data[parse_font(data, str(path))["pixels"]:]
        good &= compare(f"뱅크{index} 픽셀 (x={x})",
                        vram_rows(vram, x, args.y), want[:HEIGHT * ROW_HALFWORDS * 2])

    if args.ram:
        try:
            ram = LIVE.fetch("cpu/ram/raw")
        except OSError as error:
            print(f"RAM 을 못 읽었다: {error}", file=sys.stderr)
            return 2
        print("RAM 의 폭 테이블 대조")
        for index, _, path, table in BANKS:
            if not path.exists():
                continue
            data = path.read_bytes()
            start = parse_font(data, str(path))["widths"]
            good &= compare(f"뱅크{index} 폭 테이블 ({table:#010x})",
                            ram[table - 0x80000000:table - 0x80000000 + WIDTH_BYTES],
                            data[start:start + WIDTH_BYTES])

    print("\n뱅크1 패치가 먹었다" if good else "\n어긋난 것이 있다")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
