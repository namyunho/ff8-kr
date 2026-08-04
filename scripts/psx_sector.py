#!/usr/bin/env python3
"""raw 2352바이트 섹터의 EDC/ECC 를 계산한다. Mode 2 Form 1 전용이다.

원본을 고쳐 쓰려면 바꾼 섹터의 오류 정정 부호를 다시 만들어야 한다. 잘못 만들면
실기가 섹터를 읽지 못한다. **바꾸지 않은 섹터는 손대지 않는다**(AGENTS 불변식 4).

Mode 2 Form 1 의 배치다. Mode 1 과 달리 EDC 뒤에 예약 8바이트가 없다.

    0      12   동기 패턴
    12      4   헤더 (분, 초, 섹터, 모드)
    16      8   서브헤더 (4바이트가 두 번 복제된다)
    24   2048   사용자 데이터
    2072    4   EDC
    2076  172   ECC P
    2248  104   ECC Q

EDC 는 오프셋 16..2071 에 걸린 CRC 이며 다항식은 `0x8001801B` 다. ECC 는 12..2075
를 읽는데 **Form 1 에서는 헤더 4바이트를 0 으로 두고** 계산한다.

구현이 맞는지는 원본으로 검산한다. 원본 섹터에서 EDC/ECC 를 다시 계산해 원래
값과 한 바이트도 다르지 않아야 한다. 이 검산을 통과하기 전에는 아무것도 쓰지
않는다.

    python3 scripts/psx_sector.py --verify 5000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RAW_SECTOR = 2352
HEADER_AT = 12
SUBHEADER_AT = 16
USER_AT = 24
USER_SIZE = 2048
EDC_AT = 2072
ECC_P_AT = 2076
ECC_Q_AT = 2248
MODE2 = 2
FORM2_FLAG = 0x20               # 서브헤더 submode 비트. Form 2 는 규칙이 다르다


def _build_tables() -> tuple[list[int], list[int], list[int]]:
    """EDC 표와 ECC 의 전진·후진 표. GF(2^8), 생성 다항식 0x11D."""
    edc_table, forward, backward = [], [0] * 256, [0] * 256
    for value in range(256):
        stepped = ((value << 1) ^ (0x11D if value & 0x80 else 0)) & 0xFF
        forward[value] = stepped
        backward[value ^ stepped] = value
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xD8018001 if crc & 1 else 0)
        edc_table.append(crc)
    return edc_table, forward, backward


_EDC_TABLE, _FORWARD, _BACKWARD = _build_tables()


def edc(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ _EDC_TABLE[(value ^ byte) & 0xFF]
    return value & 0xFFFFFFFF


def _ecc_block(source: memoryview, major_count: int, minor_count: int,
               major_mult: int, minor_inc: int, out: bytearray, at: int) -> None:
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            value = source[index]
            index += minor_inc
            if index >= size:
                index -= size
            a ^= value
            b ^= value
            a = _FORWARD[a]
        a = _BACKWARD[_FORWARD[a] ^ b]
        out[at + major] = a
        out[at + major + major_count] = a ^ b


def rebuild(sector: bytes) -> bytes:
    """EDC 와 ECC 를 다시 계산한 섹터를 돌려준다. 원본은 건드리지 않는다."""
    out = bytearray(sector)
    out[EDC_AT:EDC_AT + 4] = edc(bytes(out[SUBHEADER_AT:EDC_AT])).to_bytes(4, "little")
    keep = bytes(out[HEADER_AT:HEADER_AT + 4])
    out[HEADER_AT:HEADER_AT + 4] = b"\x00" * 4
    view = memoryview(out)[HEADER_AT:]
    _ecc_block(view, 86, 24, 2, 86, out, ECC_P_AT)
    _ecc_block(view, 52, 43, 86, 88, out, ECC_Q_AT)
    out[HEADER_AT:HEADER_AT + 4] = keep
    return bytes(out)


def is_form1(sector: bytes) -> bool:
    return (len(sector) == RAW_SECTOR
            and sector[HEADER_AT + 3] == MODE2
            and not sector[SUBHEADER_AT + 2] & FORM2_FLAG)


def verify(count: int, start: int) -> int:
    import extract_field_text as FT

    checked = skipped = 0
    bad_edc = bad_ecc = 0
    with FT.BIN_PATH.open("rb") as handle:
        handle.seek(start * RAW_SECTOR)
        for _ in range(count):
            raw = handle.read(RAW_SECTOR)
            if len(raw) < RAW_SECTOR or not is_form1(raw):
                skipped += 1
                continue
            checked += 1
            made = rebuild(raw)
            if made[EDC_AT:EDC_AT + 4] != raw[EDC_AT:EDC_AT + 4]:
                bad_edc += 1
            if made[ECC_P_AT:] != raw[ECC_P_AT:]:
                bad_ecc += 1
    print(f"Form 1 섹터 {checked:,}개 검사 (건너뜀 {skipped:,})")
    print(f"  EDC 불일치 {bad_edc}")
    print(f"  ECC 불일치 {bad_ecc}")
    if checked and not (bad_edc or bad_ecc):
        print("원본을 그대로 재현한다. 쓰기 경로에 쓸 수 있다.")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", type=int, metavar="N",
                        help="원본 섹터 N개로 검산한다")
    parser.add_argument("--start", type=int, default=0, help="시작 LBA")
    args = parser.parse_args()
    if not args.verify:
        parser.print_help()
        return 0
    return verify(args.verify, args.start)


if __name__ == "__main__":
    raise SystemExit(main())
