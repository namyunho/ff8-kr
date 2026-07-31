#!/usr/bin/env python3
"""PCSX-Redux 의 GDB 서버에 직접 말을 건다. 런타임 조사의 통로다.

GDB 원격 프로토콜은 `$페이로드#체크섬` 꼴의 텍스트 규약이라 클라이언트를
따로 깔 필요가 없다. 여기서 쓰는 것은 다섯 가지뿐이다.

    m  주소,길이     메모리 읽기
    M  주소,길이:값  메모리 쓰기
    Z2 주소,길이     쓰기 워치포인트   (Z3 읽기, Z4 접근)
    c / s           계속 / 한 단계
    g               레지스터 전부

**정적 분석으로 못 여는 것을 여는 데 쓴다.** 이 저장소에서 막혀 있는 R2(뱅크1
폭 테이블을 누가 채우는가)와 R7(MSD 를 밀면 뒤 섹션이 깨진다)이 둘 다
"실행 중에 누가 이 주소를 건드리나" 라는 한 가지 질문이다.

    python3 scripts/psx_gdb.py --read 0x800823BC 32
    python3 scripts/psx_gdb.py --watch 0x800823BC 4
"""

from __future__ import annotations

import argparse
import socket
import sys

HOST, PORT = "127.0.0.1", 3333


class Gdb:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buffer = b""
        self.log: list[str] = []

    def close(self) -> None:
        self.sock.close()

    def send(self, payload: str) -> None:
        total = sum(payload.encode()) & 0xFF
        self.sock.sendall(f"${payload}#{total:02x}".encode())

    def read(self, timeout: float | None = None) -> str | None:
        """다음 패킷 하나. 시간이 지나면 None.

        `O…` 는 대상의 콘솔 출력이지 응답이 아니다. `PCSX Logs to GDB` 를 켜면
        이게 섞여 들어와 레지스터 응답 자리에 앉는다. 걸러 낸다.
        """
        if timeout is not None:
            self.sock.settimeout(timeout)
        while True:
            start = self.buffer.find(b"$")
            end = self.buffer.find(b"#", start + 1) if start >= 0 else -1
            if start >= 0 and end >= 0 and len(self.buffer) >= end + 3:
                body = self.buffer[start + 1:end].decode("latin-1")
                self.buffer = self.buffer[end + 3:]
                self.sock.sendall(b"+")
                if body.startswith("O") and len(body) > 1:
                    self.log.append(body[1:])
                    continue
                return body
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buffer += chunk.replace(b"+", b"").replace(b"-", b"")

    def ask(self, payload: str, timeout: float | None = None) -> str | None:
        self.send(payload)
        return self.read(timeout)


def read_memory(gdb: Gdb, addr: int, length: int) -> bytes:
    out = bytearray()
    while len(out) < length:
        want = min(256, length - len(out))
        reply = gdb.ask(f"m{addr + len(out):x},{want:x}")
        if reply is None or reply.startswith("E") or not reply:
            break
        out += bytes.fromhex(reply)
    return bytes(out)


def registers(gdb: Gdb) -> list[int]:
    reply = gdb.ask("g")
    if not reply or reply.startswith("E"):
        return []
    try:
        raw = bytes.fromhex(reply)
    except ValueError:
        return []
    return [int.from_bytes(raw[i:i + 4], "little")
            for i in range(0, len(raw), 4)]


NAMES = (["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3"]
         + [f"t{i}" for i in range(8)] + [f"s{i}" for i in range(8)]
         + ["t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]
         + ["sr", "lo", "hi", "bad", "cause", "pc"])


def show_registers(values: list[int]) -> None:
    for index, value in enumerate(values[:len(NAMES)]):
        name = NAMES[index]
        if name in ("zero", "at", "k0", "k1"):
            continue
        print(f"    {name:<5} {value:#010x}", end="\n" if index % 4 == 3 else "")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--read", nargs=2, metavar=("주소", "길이"),
                        help="메모리를 읽어 16진으로 낸다")
    parser.add_argument("--watch", nargs=2, metavar=("주소", "길이"),
                        help="쓰기 워치포인트를 걸고 멈출 때까지 기다린다")
    parser.add_argument("--wait", type=float, default=30.0,
                        help="워치포인트를 기다릴 초 (기본 30)")
    parser.add_argument("--probe", action="store_true",
                        help="붙어서 무엇을 지원하는지 본다")
    parser.add_argument("--break", dest="stop_at", metavar="주소",
                        help="그 주소에 멈춰 서서 인자와 호출자를 본다")
    parser.add_argument("--hits", type=int, default=1,
                        help="몇 번 잡을 것인가 (기본 1)")
    args = parser.parse_args()

    try:
        gdb = Gdb(args.host, args.port)
    except OSError as error:
        print(f"붙지 못했다: {error}", file=sys.stderr)
        return 2

    if args.probe:
        print("서버가 알리는 기능:")
        print("   ", gdb.ask("qSupported:swbreak+;hwbreak+") or "(응답 없음)")
        print("멈춤 이유 :", gdb.ask("?") or "(응답 없음)")
        values = registers(gdb)
        print(f"레지스터  : {len(values)}개")
        if values:
            show_registers(values)

    if args.read:
        addr, length = int(args.read[0], 0), int(args.read[1], 0)
        data = read_memory(gdb, addr, length)
        print(f"{addr:#010x}  {len(data)}바이트")
        for offset in range(0, len(data), 16):
            row = data[offset:offset + 16]
            print(f"  {addr + offset:#010x}  {row.hex(' ')}")
        if data:
            print(f"  0 아닌 바이트 {sum(1 for b in data if b)}개")

    if args.stop_at:
        addr = int(args.stop_at, 0)
        reply = gdb.ask(f"Z1,{addr:x},4") or ""
        if reply != "OK":
            reply = gdb.ask(f"Z0,{addr:x},4") or ""
        if reply != "OK":
            print(f"브레이크포인트를 못 걸었다: {reply!r}", file=sys.stderr)
            return 1
        print(f"{addr:#x} 에 멈춰 서기로 했다. 기다린다.")
        for hit in range(args.hits):
            gdb.send("c")
            stop = gdb.read(timeout=args.wait)
            if stop is None:
                print(f"  {args.wait:.0f}초 동안 안 걸렸다.")
                break
            values = registers(gdb)
            # MIPS o32: a0..a3 은 4..7, ra 는 31, pc 는 목록 끝
            caller = values[31] if len(values) > 31 else 0
            print(f"  {hit + 1}회 — 호출자 $ra = {caller:#010x}"
                  f"  (호출 지점 {caller - 8:#010x})")
            for index, name in ((4, "a0 파일"), (5, "a1 뱅크"),
                                (6, "a2"), (7, "a3")):
                if len(values) > index:
                    print(f"      {name:<8} {values[index]:#010x}")
        gdb.ask(f"z1,{addr:x},4")
        gdb.ask(f"z0,{addr:x},4")
        gdb.send("c")

    if args.watch:
        addr, length = int(args.watch[0], 0), int(args.watch[1], 0)
        reply = gdb.ask(f"Z2,{addr:x},{length:x}")
        if reply is None or reply.startswith("E") or reply == "":
            print(f"쓰기 워치포인트를 못 걸었다: {reply!r}"
                  " — 서버가 지원하지 않는다", file=sys.stderr)
            return 1
        print(f"{addr:#x} 에 쓰기 워치포인트를 걸었다. 실행을 잇는다.")
        gdb.send("c")
        stop = gdb.read(timeout=args.wait)
        if stop is None:
            print(f"{args.wait:.0f}초 동안 아무도 쓰지 않았다.")
        else:
            print(f"멈췄다: {stop}")
            values = registers(gdb)
            if values:
                show_registers(values)
        gdb.ask(f"z2,{addr:x},{length:x}")
    gdb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
