#!/usr/bin/env python3
"""CD 읽기 호출을 전수 수집해 TOC 인덱스 ↔ RAM 목적지 대응을 뽑는다.

FF8 의 모듈 적재는 세 계층이다.

    sub_80011E10          IMG TOC(LBA 826, 1072B = 134엔트리)를 RAM 0x80099400 에 적재
    각 로더 스텁          0x80099400 + 8N 에서 (u32 LBA, u32 size) 를 읽어
    sub_800386B0/EC/7C    → sub_800385E4 로 읽기 요청
    sub_8003924C          상태 머신을 idle 까지 펌프

따라서 "어느 TOC 엔트리가 RAM 어디로 가는가" 는 호출 지점의 `$a0/$a1/$a2`
설정 코드를 읽으면 나온다. Hex-Rays 는 이 패턴에서 `lw $a0` 를 통째로
누락하므로 디컴파일 결과를 쓰지 않고 직접 추적한다.

`--archives` 는 오버레이 묶음의 정적 표를 그대로 덤프한다. 이쪽은 호출 지점이
아니라 EXE 안의 표가 정본이며 오버레이 이름 문자열까지 들어 있다.

    python3 scripts/trace_module_loader.py work/disc1/SLPS_018.80
    python3 scripts/trace_module_loader.py work/disc1/SLPS_018.80 --archives
    python3 scripts/trace_module_loader.py work/extracted/img_004_lba97859.bin \\
        --base 0x801F0000
    python3 scripts/trace_module_loader.py work/disc1/SLPS_018.80 --json out.json
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

# TOC 가 적재되는 RAM 주소. sub_80011E10 의 `lui $a2,0x8009 / ori $a2,0x9400`.
TOC_BASE = 0x80099400
TOC_ENTRIES = 134           # 부트스트랩 크기 1072 / 8
TOC_SPAN = TOC_ENTRIES * 8

# 부트 EXE 안의 CD 읽기 진입점. 오버레이도 이 절대 주소로 호출한다.
READERS = {
    0x800385E4: "request",
    0x8003867C: "read_a",
    0x800386B0: "read",
    0x800386EC: "read_ex",
}

_SP = 29
_PROLOGUE_SCAN = 400        # 함수 시작을 뒤로 찾을 최대 명령 수
_FALLBACK_WINDOW = 40       # 프롤로그를 못 찾았을 때 되짚을 명령 수

# 부트 EXE 안의 정적 표.
#   sub_80036024 가 `off_80054340[2 * archive]` 로 목적지를 읽는다.
#   여기서 archive 번호는 TOC 인덱스 - 4 다 (기준이 0x80099420 = TOC#4).
ARCHIVE_DEST_TABLE = 0x80054340
SUB_DIRECTORY = 0x800543F8
ARCHIVE_DEST_COUNT = (SUB_DIRECTORY - ARCHIVE_DEST_TABLE) // 8      # 23
ARCHIVE_TOC_ORIGIN = 4
SUB_DIRECTORY_COUNT = 48
SECTOR = 2048


class Image:
    """base 주소를 가진 읽기 전용 코드 이미지."""

    def __init__(self, data: bytes, base: int):
        self.data = data
        self.base = base

    def __contains__(self, addr: int) -> bool:
        return self.base <= addr < self.base + len(self.data)

    def word(self, addr: int) -> int | None:
        if addr not in self or (addr - self.base) + 4 > len(self.data):
            return None
        return struct.unpack_from("<I", self.data, addr - self.base)[0]

    def cstring(self, addr: int, limit: int = 64) -> str | None:
        if addr not in self:
            return None
        start = addr - self.base
        end = self.data.find(b"\x00", start, start + limit)
        if end < 0:
            return None
        try:
            return self.data[start:end].decode("ascii")
        except UnicodeDecodeError:
            return None


# 레지스터 값은 (종류, 값) 또는 None(미상)이다.
#   ('imm', v)      확정 상수
#   ('toc', n)      TOC 엔트리 n 의 LBA 슬롯에서 읽은 값
#   ('tocsize', n)  TOC 엔트리 n 의 size 슬롯에서 읽은 값
#   ('mem', addr)   주소는 알지만 내용이 파일에 없는 값


def _toc_slot(ea: int):
    if not (TOC_BASE <= ea < TOC_BASE + TOC_SPAN):
        return None
    offset = ea - TOC_BASE
    return ("toc", offset // 8) if offset % 8 == 0 else ("tocsize", offset // 8)


def emulate(img: Image, start: int, end: int) -> list:
    """start..end 를 앞으로 실행하며 레지스터 값을 추적한다.

    분기는 따라가지 않는다. 인자 설정은 호출 직전 직선 구간에서 끝나므로
    이 근사로 충분하며, 확신할 수 없는 값은 None 으로 남긴다.
    """
    reg: list = [None] * 32
    reg[0] = ("imm", 0)
    addr = start
    while addr <= end:
        word = img.word(addr)
        if word is None:
            break
        op = word >> 26
        rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
        fn = word & 63
        imm = word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm

        if op == 0:                                     # SPECIAL
            if fn in (0x21, 0x25):                      # addu / or  (move 포함)
                left, right = reg[rs], reg[rt]
                if left == ("imm", 0):
                    reg[rd] = right
                elif right == ("imm", 0):
                    reg[rd] = left
                elif left and right and left[0] == right[0] == "imm":
                    reg[rd] = ("imm", (left[1] | right[1]) & 0xFFFFFFFF)
                else:
                    reg[rd] = None
            elif fn not in (0x08, 0x09):                # jr / jalr 은 값을 안 바꾼다
                reg[rd] = None
        elif op == 0x0F:                                # lui
            reg[rt] = ("imm", (imm << 16) & 0xFFFFFFFF)
        elif op == 0x0D:                                # ori
            left = reg[rs]
            reg[rt] = (("imm", (left[1] | imm) & 0xFFFFFFFF)
                       if left and left[0] == "imm" else None)
        elif op == 0x09:                                # addiu
            left = reg[rs]
            reg[rt] = (("imm", (left[1] + simm) & 0xFFFFFFFF)
                       if left and left[0] == "imm" else None)
        elif op == 0x23:                                # lw
            left = reg[rs]
            if left and left[0] == "imm":
                ea = (left[1] + simm) & 0xFFFFFFFF
                slot = _toc_slot(ea)
                if slot is not None:
                    reg[rt] = slot
                else:
                    value = img.word(ea)
                    reg[rt] = ("imm", value) if value else ("mem", ea)
            else:
                reg[rt] = None
        elif op in (0x08, 0x0A, 0x0B, 0x0C, 0x0E,       # addi/slti/andi/xori
                    0x20, 0x21, 0x24, 0x25, 0x27):      # lb/lh/lbu/lhu/lwr
            reg[rt] = None
        addr += 4
    return reg


def function_start(img: Image, call_site: int) -> int:
    """호출 지점을 감싸는 함수의 프롤로그를 뒤로 찾는다."""
    probe = call_site
    for _ in range(_PROLOGUE_SCAN):
        probe -= 4
        word = img.word(probe)
        if word is None:
            break
        # addiu $sp, $sp, -N
        if (word >> 26) == 9 and (word >> 21) & 31 == _SP \
                and (word >> 16) & 31 == _SP and word & 0x8000:
            return probe
    return max(img.base, call_site - 4 * _FALLBACK_WINDOW)


def describe(value) -> str:
    if value is None:
        return "?"
    kind, payload = value
    if kind == "imm":
        return f"0x{payload:08X}"
    if kind == "toc":
        return f"TOC#{payload}"
    if kind == "tocsize":
        return f"TOC#{payload}.size"
    return f"[0x{payload:08X}]"


def collect(img: Image) -> list[dict]:
    calls = []
    for offset in range(0, len(img.data) - 4, 4):
        addr = img.base + offset
        word = img.word(addr)
        if word is None or (word >> 26) != 3:           # jal
            continue
        target = ((addr + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        if target not in READERS:
            continue
        # delay slot 이 인자를 설정하는 경우가 흔하므로 addr+4 까지 실행한다.
        reg = emulate(img, function_start(img, addr), addr + 4)
        source, size, dest = reg[4], reg[5], reg[6]
        calls.append({
            "site": f"0x{addr:08X}",
            "reader": READERS[target],
            "toc_index": source[1] if source and source[0] == "toc" else None,
            "lba": source[1] if source and source[0] == "imm" else None,
            "size": size[1] if size and size[0] == "imm" else None,
            "dest": f"0x{dest[1]:08X}" if dest and dest[0] == "imm" else None,
            "raw": [describe(reg[r]) for r in (4, 5, 6, 7)],
        })
    return calls


def archive_table(img: Image) -> list[dict]:
    """오버레이 묶음의 목적지 표. 두 번째 u32 는 이름 문자열 포인터다."""
    rows = []
    for n in range(ARCHIVE_DEST_COUNT):
        pair = ARCHIVE_DEST_TABLE + 8 * n
        dest, name_ptr = img.word(pair), img.word(pair + 4)
        if dest is None:
            break
        rows.append({
            "archive": n,
            "toc_index": n + ARCHIVE_TOC_ORIGIN,
            "dest": f"0x{dest:08X}" if 0x80000000 <= dest < 0x80800000 else None,
            "name": img.cstring(name_ptr) if name_ptr else None,
        })
    return rows


def sub_directory(img: Image) -> list[dict]:
    """서브엔트리 디렉터리. 오프셋 bit0 은 읽기 경로 플래그이며 나머지는
    아카이브 선두로부터의 바이트 오프셋으로 섹터 단위로 내림된다."""
    rows = []
    for n in range(SUB_DIRECTORY_COUNT):
        pair = SUB_DIRECTORY + 8 * n
        offset, size = img.word(pair), img.word(pair + 4)
        if offset is None:
            break
        if offset == 0 and size == 0:
            break
        if offset == 0xFFFFFFFF:                        # 빈 슬롯
            rows.append({"sub": n, "empty": True})
            continue
        rows.append({
            "sub": n,
            "empty": False,
            "flag": offset & 1,
            "sector_offset": (offset & ~1) >> 11,
            "size": size,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--base", type=lambda v: int(v, 0),
                        help="raw 이미지의 load address. PS-X EXE 는 헤더에서 읽는다")
    parser.add_argument("--archives", action="store_true",
                        help="오버레이 묶음 목적지 표와 서브엔트리 디렉터리를 덤프한다")
    parser.add_argument("--json", type=Path, help="결과를 JSON 으로 저장한다")
    args = parser.parse_args()

    blob = args.input.read_bytes()
    if blob[:8] == b"PS-X EXE":
        base, size = struct.unpack_from("<II", blob, 0x18)
        img = Image(blob[0x800:0x800 + size], base)
    elif args.base is not None:
        img = Image(blob, args.base)
    else:
        parser.error("PS-X EXE 가 아니면 --base 가 필요하다")

    calls = collect(img)
    print(f"{args.input}  base 0x{img.base:08X}  {len(img.data):,}B  "
          f"읽기 호출 {len(calls)}개")
    print(f"  {'호출지점':<12} {'종류':<9} {'출처':<14} {'크기':<15} {'목적지'}")
    for call in calls:
        source, size, dest, _ = call["raw"]
        print(f"  {call['site']:<12} {call['reader']:<9} {source:<14} "
              f"{size:<15} {dest}")

    archives: list[dict] = []
    subdir: list[dict] = []
    if args.archives:
        archives = archive_table(img)
        subdir = sub_directory(img)
        print(f"\n오버레이 묶음 목적지 표 0x{ARCHIVE_DEST_TABLE:08X}")
        print(f"  {'arch':>4} {'TOC':>4} {'목적지':<12} 이름")
        for row in archives:
            print(f"  {row['archive']:>4} {row['toc_index']:>4} "
                  f"{row['dest'] or '-':<12} {row['name'] or '-'}")
        print(f"\n서브엔트리 디렉터리 0x{SUB_DIRECTORY:08X}")
        print(f"  {'sub':>4} {'flag':>4} {'섹터오프셋':>10} {'크기':>10}")
        for row in subdir:
            if row["empty"]:
                print(f"  {row['sub']:>4}    -          -          -")
            else:
                print(f"  {row['sub']:>4} {row['flag']:>4} "
                      f"{row['sector_offset']:>10} {row['size']:>10}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"image": str(args.input), "base": f"0x{img.base:08X}",
             "calls": calls, "archives": archives, "sub_directory": subdir},
            indent=2, ensure_ascii=False))
        print(f"\n{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
