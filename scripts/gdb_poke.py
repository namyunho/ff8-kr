#!/usr/bin/env python3
"""실행 중인 RAM 에 바이트를 써넣는다. `gdb_peek.py` 의 짝이다.

## 왜 필요한가

디스크에 넣기 전에 **화면으로 먼저 확인**하려면 실행 중 RAM 을 고쳐 봐야 한다.
전투 이름 글꼴처럼 어느 아카이브에서 오는지 아직 못 찾은 자원은, 디스크를
고칠 방법이 없어도 RAM 을 고치면 눈으로 판정할 수 있다.

PCSX-Redux 의 웹 API 는 읽기만 준다. 쓰기는 GDB 스텁의 `M` 패킷으로 한다.

    $M주소,길이:데이터#검사합

멈춘 상태에서 써야 안전하다. 이 도구는 `0x03` 으로 멈추고 쓴 뒤 `c` 로
다시 굴린다. **되읽어 확인**까지 하고 끝낸다 — 안 그러면 썼다고 믿고 넘어간다.

    python3 scripts/gdb_poke.py 0x801b8e00 work/analysis/battle-font/battle-name-font-patched.bin
    python3 scripts/gdb_poke.py 0x801b8e00 파일.bin --keep-halted
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

HOST, PORT = "127.0.0.1", 3333
CHUNK = 256                       # 한 패킷에 쓸 바이트. 크게 잡으면 스텁이 흘린다


def frame(body: str) -> bytes:
    return f"${body}#{sum(body.encode()) & 0xFF:02x}".encode()


class Link:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def ask(self, body: str) -> str:
        self.sock.sendall(frame(body))
        return self.reply()

    def reply(self) -> str:
        while True:
            start = self.buf.find(b"$")
            end = self.buf.find(b"#", start + 1) if start >= 0 else -1
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                payload = self.buf[start + 1:end].decode(errors="replace")
                self.buf = self.buf[end + 3:]
                self.sock.sendall(b"+")
                return payload
            more = self.sock.recv(4096)
            if not more:
                return ""
            self.buf += more

    def halt(self) -> str:
        """0x03 으로 멈춘다.

        PCSX-Redux 는 정지 패킷(`$T05…#`) 대신 **맨 `+` 한 바이트만** 보낼 때가
        있다. `$…#` 프레임을 기다리면 그대로 멈춰 선다 — 짧게 받아 보고 넘어간다.
        """
        self.sock.sendall(b"\x03")
        self.sock.settimeout(1.0)
        try:
            got = self.sock.recv(512)
        except socket.timeout:
            got = b""
        finally:
            self.sock.settimeout(5.0)
        self.buf += got.lstrip(b"+")
        return got.decode(errors="replace") or "(응답 없음)"

    def write(self, addr: int, data: bytes) -> None:
        for off in range(0, len(data), CHUNK):
            piece = data[off:off + CHUNK]
            answer = self.ask(f"M{addr + off:x},{len(piece):x}:{piece.hex()}")
            if answer != "OK":
                raise RuntimeError(f"{addr + off:#x} 쓰기 거부: {answer!r}")

    def read(self, addr: int, length: int) -> bytes:
        out = bytearray()
        for off in range(0, length, CHUNK):
            n = min(CHUNK, length - off)
            answer = self.ask(f"m{addr + off:x},{n:x}")
            if not answer or answer.startswith("E"):
                raise RuntimeError(f"{addr + off:#x} 읽기 실패: {answer!r}")
            out += bytes.fromhex(answer)
        return bytes(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("addr", type=lambda v: int(v, 0))
    parser.add_argument("blob", type=Path)
    parser.add_argument("--keep-halted", action="store_true",
                        help="쓴 뒤 다시 굴리지 않는다")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    data = args.blob.read_bytes()
    try:
        link = Link(HOST, args.port)
    except OSError as error:
        print(f"GDB 스텁에 못 붙는다 ({HOST}:{args.port}) — {error}", file=sys.stderr)
        return 2

    print(f"멈춤: {link.halt()}")
    try:
        link.write(args.addr, data)
        back = link.read(args.addr, len(data))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    if back == data:
        print(f"{args.addr:#010x} 에 {len(data):,}바이트 — 되읽기 일치")
    else:
        bad = sum(1 for a, b in zip(back, data) if a != b)
        print(f"**되읽기 불일치 {bad:,}/{len(data):,}바이트**", file=sys.stderr)
        return 1

    if not args.keep_halted:
        link.sock.sendall(frame("c"))
        print("다시 굴렸다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
