#!/usr/bin/env python3
"""PCSX-Redux 의 웹 API 로 실행 중인 RAM 과 VRAM 을 통째로 받는다.

GDB 스텁보다 이쪽이 낫다. 한 번에 전부 오므로 패킷을 쪼갤 일이 없고, 무엇보다
**VRAM 전체(1024×512×2 = 1MB)** 를 받을 수 있다. 정적 TIM RECT 로 추정하던
VRAM 점유(R3)를 실제 화면 메모리로 확인할 수 있다는 뜻이다.

    /api/v1/cpu/ram/raw    2,097,152바이트  RAM 2MB
    /api/v1/gpu/vram/raw   1,048,576바이트  VRAM 1024×512 halfword

브레이크포인트는 `Dynarec CPU` 를 끄고 GDB 쪽(`psx_gdb.py`)을 쓴다. 동적
재컴파일러는 명령 단위 검사를 지나치므로 `Z` 패킷이 `OK` 를 돌려주고도
동작하지 않는다.

    python3 scripts/psx_live.py --ram 0x800823BC 64
    python3 scripts/psx_live.py --vram 960 256 64 8
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
RAM_BASE = 0x80000000
VRAM_W, VRAM_H = 1024, 512


def fetch(path: str, timeout: float = 10.0) -> bytes:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=timeout) as reply:
        return reply.read()


def ram(addr: int, length: int) -> bytes:
    blob = fetch("cpu/ram/raw")
    offset = addr - RAM_BASE if addr >= RAM_BASE else addr
    return blob[offset:offset + length]


def vram_cells(x: int, y: int, width: int, height: int) -> list[list[int]]:
    """halfword 단위 구간. 폰트 슬롯은 64 x 252 다."""
    blob = fetch("gpu/vram/raw")
    out = []
    for row in range(height):
        start = ((y + row) * VRAM_W + x) * 2
        out.append([int.from_bytes(blob[start + i * 2:start + i * 2 + 2],
                                   "little") for i in range(width)])
    return out


def occupancy(step: int = 16) -> None:
    """VRAM 을 격자로 훑어 어디가 비었는지 본다. R3 의 정본이 된다."""
    blob = fetch("gpu/vram/raw")
    print(f"VRAM {VRAM_W}x{VRAM_H} — {step}칸마다 0 이 아닌 halfword 의 비율")
    print("      " + "".join(f"{x:<4}" for x in range(0, VRAM_W, step * 8)))
    for y in range(0, VRAM_H, step):
        row = []
        for x in range(0, VRAM_W, step):
            used = 0
            for dy in range(step):
                start = ((y + dy) * VRAM_W + x) * 2
                chunk = blob[start:start + step * 2]
                used += sum(1 for i in range(0, len(chunk), 2)
                            if chunk[i] or chunk[i + 1])
            share = used / (step * step)
            row.append(" " if share < 0.01 else
                       "." if share < 0.25 else
                       "o" if share < 0.75 else "#")
        print(f"  {y:>3} {''.join(row)}")
    print("  (공백=빈칸, .=성김, o=반, #=꽉참)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ram", nargs=2, metavar=("주소", "길이"))
    parser.add_argument("--vram", nargs=4, metavar=("x", "y", "폭", "높이"))
    parser.add_argument("--map", action="store_true",
                        help="VRAM 점유 지도를 그린다")
    parser.add_argument("--step", type=int, default=16)
    args = parser.parse_args()

    try:
        if args.ram:
            addr, length = int(args.ram[0], 0), int(args.ram[1], 0)
            data = ram(addr, length)
            for offset in range(0, len(data), 16):
                print(f"  {addr + offset:#010x}  "
                      f"{data[offset:offset + 16].hex(' ')}")
            print(f"  0 아닌 바이트 {sum(1 for b in data if b)} / {len(data)}")
        if args.vram:
            x, y, w, h = (int(v, 0) for v in args.vram)
            for index, row in enumerate(vram_cells(x, y, w, h)):
                filled = sum(1 for v in row if v)
                print(f"  y={y + index:<4} 0 아닌 halfword {filled:>3}/{len(row)}"
                      f"  {' '.join(f'{v:04x}' for v in row[:8])}")
        if args.map:
            occupancy(args.step)
    except OSError as error:
        print(f"웹 API 에 붙지 못했다: {error}", file=sys.stderr)
        print("PCSX-Redux 의 Enable Web Server 를 켜고 포트를 확인한다.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
