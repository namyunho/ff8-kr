#!/usr/bin/env python3
"""오버레이 조각을 조사용 PS-X EXE 로 감싼다.

IMG TOC 의 오버레이는 헤더 없는 raw MIPS 코드라 그대로 열면 base 가 0 이 된다.
`scripts/build_ida_db.py` 가 읽을 수 있도록 올바른 load address 를 가진
PS-X EXE 헤더를 붙인다. 원본은 수정하지 않고 새 파일을 만든다.

base 를 모르면 `--solve` 로 역산한다. `jal` 은 절대 주소를 인코딩하므로,
올바른 base 에서는 내부 `jal` 타깃이 함수 프롤로그(`addiu $sp, $sp, -N`)에
집중된다. 잘못된 base 에서는 적중률이 몇 퍼센트로 떨어진다.

    python3 scripts/psx_disc.py extract --index 12
    python3 scripts/wrap_psx_overlay.py work/extracted/img_012_lba97933.bin --solve
    python3 scripts/wrap_psx_overlay.py work/extracted/img_012_lba97933.bin \\
        --base 0x801E4000
    python3 scripts/build_ida_db.py work/overlay/img_012_lba97933.psxexe --force
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEADER_SIZE = 0x800
# 부트 EXE 는 0x80010000..0x801A0800 을 쓴다. 오버레이는 그 위에 올라간다.
SEARCH_LOW = 0x801A0000
SEARCH_HIGH = 0x80200000
SEARCH_STEP = 0x800


def jal_targets(payload: bytes) -> list[int]:
    out = []
    for offset in range(0, len(payload) - 4, 4):
        word = struct.unpack_from("<I", payload, offset)[0]
        if word >> 26 == 3:                       # jal
            out.append(((word & 0x03FFFFFF) << 2) | 0x80000000)
    return out


def is_prologue(word: int) -> bool:
    """addiu $sp, $sp, -N"""
    if word >> 26 != 9:
        return False
    if (word >> 21) & 31 != 29 or (word >> 16) & 31 != 29:
        return False
    return bool(word & 0x8000)


def solve_base(payload: bytes, minimum_internal: int = 20) -> list[tuple]:
    targets = jal_targets(payload)
    scored = []
    for base in range(SEARCH_LOW, SEARCH_HIGH, SEARCH_STEP):
        internal = [t for t in targets if base <= t < base + len(payload)]
        if len(internal) < minimum_internal:
            continue
        hits = 0
        for target in internal:
            offset = target - base
            if offset + 4 > len(payload):
                continue
            if is_prologue(struct.unpack_from("<I", payload, offset)[0]):
                hits += 1
        scored.append((hits / len(internal), base, len(internal)))
    scored.sort(reverse=True)
    return scored


def build_header(base: int, entry: int, size: int) -> bytes:
    header = bytearray(HEADER_SIZE)
    header[0:8] = b"PS-X EXE"
    struct.pack_into("<4I", header, 0x10, entry, 0, base, size)
    region = b"Sony Computer Entertainment Inc. for Japan area"
    header[0x4C:0x4C + len(region)] = region
    return bytes(header)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--base", type=lambda v: int(v, 0),
                        help="load address. 생략하면 --solve 결과가 필요하다")
    parser.add_argument("--entry", type=lambda v: int(v, 0),
                        help="entry point (기본값은 base)")
    parser.add_argument("--solve", action="store_true",
                        help="base 후보를 역산해 보여주고 끝낸다")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = args.input.read_bytes()
    if len(payload) % 4:
        payload += b"\x00" * (4 - len(payload) % 4)

    if args.solve or args.base is None:
        scored = solve_base(payload)
        if not scored:
            print("내부 jal 이 부족해 base 를 역산할 수 없다. "
                  "코드 오버레이가 아니거나 조각이 너무 작다.")
            return 1
        print(f"{'base':>12} {'내부jal':>8} {'프롤로그적중':>12}")
        for rate, base, count in scored[:8]:
            print(f"  0x{base:08X} {count:>8} {rate:>11.1%}")
        top = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        print()
        if top[0] >= 0.4 and top[0] > runner_up * 3:
            print(f"판정: base = 0x{top[1]:08X} "
                  f"(적중 {top[0]:.1%}, 차순위 {runner_up:.1%})")
        else:
            print("판정 보류: 1순위와 2순위가 충분히 벌어지지 않는다. "
                  "다른 근거로 확인한다.")
        if args.solve:
            return 0
        args.base = top[1]

    entry = args.entry if args.entry is not None else args.base
    output = args.output or (PROJECT_ROOT / "work" / "overlay" /
                             f"{args.input.stem}.psxexe")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_header(args.base, entry, len(payload)) + payload)
    print(f"{output}  base 0x{args.base:08X}  entry 0x{entry:08X}  "
          f"text 0x{len(payload):X} ({len(payload):,}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
