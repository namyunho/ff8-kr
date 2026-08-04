#!/usr/bin/env python3
"""MIPS R3000 을 조립하고 해체한다. 주입할 루틴을 짜고 되읽어 검산하는 데 쓴다.

외부 조립기를 쓰지 않는 이유는 하나다 — **왕복 검산**. 조립한 바이트를 도로
해체해 원래 적은 명령과 글자까지 맞춰 보면, 인코딩을 잘못해 조용히 다른 명령이
박히는 사고를 막을 수 있다. 패치는 한 워드만 어긋나도 무슨 일이 벌어졌는지
알아내기가 매우 어렵다.

지연 슬롯은 채워 주지 않는다. 분기·점프 바로 뒤 한 줄이 먼저 실행된다는 것은
그대로 드러나 있어야 하고, 자동으로 `nop` 을 끼우면 그 사실이 숨는다.

    from mips_asm import assemble, disassemble
    code = assemble(SOURCE, base=0x801B83E4, symbols={"PIXELS": 0x801B8500})
"""

from __future__ import annotations

import re
import sys

REGISTERS = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
             "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()
NUMBER = {f"${index}": index for index in range(32)}
NUMBER.update({f"${name}": index for index, name in enumerate(REGISTERS)})
NUMBER.update({name: index for index, name in enumerate(REGISTERS)})

# funct 코드. rd, rs, rt 순서로 적는다.
THREE = {"add": 0x20, "addu": 0x21, "sub": 0x22, "subu": 0x23,
         "and": 0x24, "or": 0x25, "xor": 0x26, "nor": 0x27,
         "slt": 0x2A, "sltu": 0x2B}
SHIFT = {"sll": 0x00, "srl": 0x02, "sra": 0x03}
# op 코드. rt, rs, imm 순서로 적는다.
IMMEDIATE = {"addi": 0x08, "addiu": 0x09, "slti": 0x0A, "sltiu": 0x0B,
             "andi": 0x0C, "ori": 0x0D, "xori": 0x0E}
MEMORY = {"lb": 0x20, "lh": 0x21, "lw": 0x23, "lbu": 0x24, "lhu": 0x25,
          "sb": 0x28, "sh": 0x29, "sw": 0x2B}
BRANCH = {"beq": 0x04, "bne": 0x05}

MASK = 0xFFFFFFFF
SPLIT = re.compile(r"[\s,]+")
OFFSET = re.compile(r"^(-?(?:0x)?[0-9a-fA-F]+)\((\w+)\)$")


class AsmError(ValueError):
    """무엇이 몇 번째 줄에서 틀렸는지 말한다."""


def _register(token: str, line: int) -> int:
    key = token.lower()
    if key not in NUMBER:
        raise AsmError(f"{line}행: 모르는 레지스터 '{token}'")
    return NUMBER[key]


def _value(token: str, symbols: dict[str, int], line: int) -> int:
    if token in symbols:
        return symbols[token]
    try:
        return int(token, 0)
    except ValueError as error:
        raise AsmError(f"{line}행: 숫자도 심볼도 아니다 '{token}'") from error


def _imm16(value: int, line: int, signed: bool = True) -> int:
    low, high = (-0x8000, 0x7FFF) if signed else (0, 0xFFFF)
    if not low <= value <= high:
        kind = "부호 있는" if signed else "부호 없는"
        raise AsmError(f"{line}행: {kind} 16비트에 안 들어간다 {value:#x} "
                       f"({low:#x}~{high:#x})")
    return value & 0xFFFF


def _clean(raw: str) -> str:
    return raw.split("#")[0].split(";")[0].strip()


def assemble(source: str, base: int,
             symbols: dict[str, int] | None = None) -> bytes:
    """두 번 훑는다. 처음엔 라벨 주소만, 다음엔 명령을 낸다."""
    symbols = dict(symbols or {})
    body: list[tuple[int, str, list[str]]] = []
    address = base

    for line, raw in enumerate(source.splitlines(), start=1):
        text = _clean(raw)
        if not text:
            continue
        if text.endswith(":"):
            symbols[text[:-1]] = address
            continue
        parts = SPLIT.split(text)
        body.append((line, parts[0].lower(), parts[1:]))
        address += 4

    out = bytearray()
    for index, (line, name, args) in enumerate(body):
        here = base + index * 4
        out += _encode(name, args, here, symbols, line).to_bytes(4, "little")
    return bytes(out)


def _encode(name: str, args: list[str], here: int,
            symbols: dict[str, int], line: int) -> int:
    if name == "nop":
        return 0
    if name in THREE:
        rd, rs, rt = (_register(a, line) for a in args)
        return (rs << 21) | (rt << 16) | (rd << 11) | THREE[name]
    if name in SHIFT:
        rd, rt = _register(args[0], line), _register(args[1], line)
        amount = _value(args[2], symbols, line)
        if not 0 <= amount < 32:
            raise AsmError(f"{line}행: 시프트 양이 범위 밖 {amount}")
        return (rt << 16) | (rd << 11) | (amount << 6) | SHIFT[name]
    if name in IMMEDIATE:
        rt, rs = _register(args[0], line), _register(args[1], line)
        raw = _value(args[2], symbols, line)
        signed = name not in ("andi", "ori", "xori")
        return (IMMEDIATE[name] << 26) | (rs << 21) | (rt << 16) | \
            _imm16(raw, line, signed)
    if name == "lui":
        rt = _register(args[0], line)
        return (0x0F << 26) | (rt << 16) | _imm16(
            _value(args[1], symbols, line), line, signed=False)
    if name in MEMORY:
        rt = _register(args[0], line)
        match = OFFSET.match(args[1])
        if not match:
            raise AsmError(f"{line}행: 'off(reg)' 꼴이 아니다 '{args[1]}'")
        return (MEMORY[name] << 26) | (_register(match.group(2), line) << 21) \
            | (rt << 16) | _imm16(int(match.group(1), 0), line)
    if name in BRANCH:
        rs, rt = _register(args[0], line), _register(args[1], line)
        step = (_value(args[2], symbols, line) - (here + 4)) // 4
        if not -0x8000 <= step <= 0x7FFF:
            raise AsmError(f"{line}행: 분기가 너무 멀다 {step}")
        return (BRANCH[name] << 26) | (rs << 21) | (rt << 16) | (step & 0xFFFF)
    if name in ("j", "jal"):
        target = _value(args[0], symbols, line)
        if target & 3:
            raise AsmError(f"{line}행: 목적지가 4의 배수가 아니다 {target:#x}")
        if (target >> 28) != ((here + 4) >> 28):
            raise AsmError(
                f"{line}행: {target:#x} 는 {here:#x} 와 256MB 영역이 다르다")
        op = 0x02 if name == "j" else 0x03
        return (op << 26) | ((target >> 2) & 0x3FFFFFF)
    if name == "jr":
        return (_register(args[0], line) << 21) | 0x08
    if name == "jalr":
        # jalr 은 원래 `jalr rd, rs` 이고 rd 를 생략하면 ra 다. 0 을 넣으면
        # 돌아올 주소가 버려져 함수가 반환하지 못한다.
        target, link = args[-1], args[0] if len(args) > 1 else "ra"
        return (_register(target, line) << 21) | \
            (_register(link, line) << 11) | 0x09
    raise AsmError(f"{line}행: 모르는 명령 '{name}'")


def disassemble(code: bytes, base: int) -> list[str]:
    from mips_dis import decode  # 해체는 한 벌만 둔다
    return [decode(int.from_bytes(code[i:i + 4], "little"), base + i)
            for i in range(0, len(code), 4)]


def verify(source: str, base: int,
           symbols: dict[str, int] | None = None) -> bytes:
    """조립한 뒤 도로 해체해 보여 준다. 눈으로 대조하라고 만든 것이다."""
    code = assemble(source, base, symbols)
    for index, text in enumerate(disassemble(code, base)):
        word = int.from_bytes(code[index * 4:index * 4 + 4], "little")
        print(f"  {base + index * 4:#010x}  {word:08x}  {text}")
    return code


if __name__ == "__main__":
    print(__doc__, file=sys.stderr)
