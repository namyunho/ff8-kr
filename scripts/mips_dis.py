#!/usr/bin/env python3
"""PSX-EXE 의 기계어를 눈으로 본다. 패치 자리를 고를 때 쓴다.

의사코드를 믿지 않으려고 만들었다. Hex-Rays 는 `bne`(0 이 아니면 분기)를
`if (x != 0) { … }` 로 펴서 보여 주는데, 그 블록을 분기가 **간** 쪽으로 읽으면
갈래가 통째로 뒤집힌다. 이번 저장소에서 폰트 뱅크 좌표를 그렇게 한 번 거꾸로
적었다. 기계어에는 뒤집힐 여지가 없다.

    python3 scripts/mips_dis.py 0x8002c358 40
    python3 scripts/mips_dis.py --find-word 0x8002c358
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

EXE = "work/disc1/SLPS_018.80"

R = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
     "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()

SPECIAL = {0x20: "add", 0x21: "addu", 0x22: "sub", 0x23: "subu", 0x24: "and",
           0x25: "or", 0x26: "xor", 0x27: "nor", 0x2A: "slt", 0x2B: "sltu"}
SHIFT = {0x00: "sll", 0x02: "srl", 0x03: "sra"}
VARIABLE = {0x04: "sllv", 0x06: "srlv", 0x07: "srav"}
MULDIV = {0x18: "mult", 0x19: "multu", 0x1A: "div", 0x1B: "divu"}
MOVE = {0x10: "mfhi", 0x11: "mthi", 0x12: "mflo", 0x13: "mtlo"}
IMMEDIATE = {0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu",
             0x0C: "andi", 0x0D: "ori", 0x0E: "xori"}
MEMORY = {0x20: "lb", 0x21: "lh", 0x22: "lwl", 0x23: "lw", 0x24: "lbu",
          0x25: "lhu", 0x26: "lwr", 0x28: "sb", 0x29: "sh", 0x2A: "swl",
          0x2B: "sw", 0x2E: "swr"}


def decode(word: int, addr: int) -> str:
    if word == 0:
        return "nop"
    op, rs, rt, rd = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    imm, amount = word & 0xFFFF, (word >> 6) & 31
    signed = imm - 0x10000 if imm & 0x8000 else imm
    target = ((addr + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
    branch = addr + 4 + signed * 4

    if op == 0:
        # 쓰이지 않는 자리가 0 이 아니면 명령이 아니라 데이터다. .text 안에는
        # 문자열도 표도 섞여 있어서, 그걸 명령으로 읽어 주면 패치할 자리를
        # 고를 때 없는 코드를 보게 된다.
        funct = word & 63
        if funct in SPECIAL:
            return (f"{SPECIAL[funct]:<6} {R[rd]}, {R[rs]}, {R[rt]}"
                    if amount == 0 else f".word  {word:08x}")
        if funct in SHIFT:
            return (f"{SHIFT[funct]:<6} {R[rd]}, {R[rt]}, {amount}"
                    if rs == 0 else f".word  {word:08x}")
        if funct in VARIABLE:
            return (f"{VARIABLE[funct]:<6} {R[rd]}, {R[rt]}, {R[rs]}"
                    if amount == 0 else f".word  {word:08x}")
        if funct in MULDIV:
            return (f"{MULDIV[funct]:<6} {R[rs]}, {R[rt]}"
                    if rd == 0 and amount == 0 else f".word  {word:08x}")
        if funct in MOVE:
            return (f"{MOVE[funct]:<6} {R[rd]}"
                    if rs == 0 and rt == 0 and amount == 0
                    else f".word  {word:08x}")
        if funct == 0x08:
            return f"jr     {R[rs]}" if (word >> 6) & 0x7FFF == 0 \
                else f".word  {word:08x}"
        if funct == 0x09:
            if rt or amount:
                return f".word  {word:08x}"
            return f"jalr   {R[rs]}" if rd == 31 else f"jalr   {R[rd]}, {R[rs]}"
        if funct == 0x0C:
            return "syscall"
        return f".word  {word:08x}"

    if op in IMMEDIATE:
        # andi/ori/xori 는 즉시값을 0 확장한다. 부호 있는 것처럼 적으면
        # 다시 조립할 때 다른 명령이 된다.
        raw = imm if op in (0x0C, 0x0D, 0x0E) else signed
        return f"{IMMEDIATE[op]:<6} {R[rt]}, {R[rs]}, {raw:#x}"
    if op == 0x0F:
        return f"lui    {R[rt]}, {imm:#x}" if rs == 0 else f".word  {word:08x}"
    if op in MEMORY:
        return f"{MEMORY[op]:<6} {R[rt]}, {signed:#x}({R[rs]})"
    if op == 0x02:
        return f"j      {target:#010x}"
    if op == 0x03:
        return f"jal    {target:#010x}"
    if op == 0x04:
        if rs == 0 and rt == 0:
            return f"b      {branch:#010x}"
        return f"beq    {R[rs]}, {R[rt]}, {branch:#010x}"
    if op == 0x05:
        return f"bne    {R[rs]}, {R[rt]}, {branch:#010x}"
    if op == 0x06:
        return f"blez   {R[rs]}, {branch:#010x}"
    if op == 0x07:
        return f"bgtz   {R[rs]}, {branch:#010x}"
    if op == 0x01:
        return f"{'bltz' if rt == 0 else 'bgez':<6} {R[rs]}, {branch:#010x}"
    return f".word  {word:08x}"


class Exe:
    """PSX-EXE 한 벌. 런타임 주소로 읽고 쓴다."""

    HEADER = 0x800

    def __init__(self, path: str = EXE):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"EXE 가 없다: {path}")
        self.data = bytearray(self.path.read_bytes())
        self.entry, _, self.load, self.size = struct.unpack_from(
            "<4I", self.data, 0x10)

    def offset(self, addr: int) -> int:
        if not self.load <= addr < self.load + self.size:
            raise ValueError(f"{addr:#x} 는 EXE 영역 밖이다 "
                             f"({self.load:#x}~{self.load + self.size:#x})")
        return self.HEADER + addr - self.load

    def word(self, addr: int) -> int:
        start = self.offset(addr)
        return int.from_bytes(self.data[start:start + 4], "little")

    def put_word(self, addr: int, value: int) -> None:
        start = self.offset(addr)
        self.data[start:start + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")

    def show(self, addr: int, count: int, mark: frozenset[int] = frozenset()) -> None:
        for index in range(count):
            here = addr + index * 4
            word = self.word(here)
            note = "  <<<" if here in mark else ""
            print(f"  {here:#010x}  {word:08x}  {decode(word, here)}{note}")

    def find_word(self, needle: int, limit: int = 40) -> list[int]:
        raw = (needle & 0xFFFFFFFF).to_bytes(4, "little")
        out: list[int] = []
        start = self.HEADER
        while len(out) < limit:
            start = self.data.find(raw, start)
            if start < 0 or start >= self.HEADER + self.size:
                break
            if (start - self.HEADER) % 4 == 0:
                out.append(self.load + start - self.HEADER)
            start += 4
        return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("addr", nargs="?", help="런타임 주소")
    parser.add_argument("count", nargs="?", type=int, default=16)
    parser.add_argument("--exe", default=EXE)
    parser.add_argument("--find-word", metavar="값",
                        help="그 32비트 값이 놓인 자리를 찾는다")
    args = parser.parse_args()

    try:
        exe = Exe(args.exe)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2

    print(f"entry {exe.entry:#010x}  load {exe.load:#010x}  text {exe.size:#x}")
    if args.find_word:
        found = exe.find_word(int(args.find_word, 0))
        print(f"{int(args.find_word, 0):#010x} 가 놓인 자리 {len(found)}곳")
        for addr in found:
            print(f"  {addr:#010x}")
    if args.addr:
        exe.show(int(args.addr, 0), args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
