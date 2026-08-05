#!/usr/bin/env python3
"""멈춘 에뮬레이터를 붙잡아 **PC 와 주변 코드**를 뜬다.

## 왜 만드는가

메뉴가 멈추는 원인을 문자열을 하나씩 갈아 끼우며 찾고 있었다. 판마다 실기에
옮겨 확인해야 해서 한 번에 1비트씩밖에 줄지 않는다. **멈춘 순간의 PC 를 한 번
읽으면** 어느 루프에 갇혔는지 바로 보인다.

PCSX-Redux 의 웹 API 는 RAM·VRAM 덤프만 준다. 레지스터는 GDB 스텁으로만
나온다. mips 용 gdb 를 따로 깔 필요는 없다 — 원격 프로토콜이 단순해서 직접
말을 걸면 된다.

    $<본문>#<검사합 두 자리>      보내기
    +                              받았다는 응답

`g` 로 레지스터 전부, `m주소,길이` 로 메모리를 읽는다. 돌고 있으면 `0x03`
한 바이트로 멈춰 세운다.

## 켜는 법

PCSX-Redux 의 Configuration -> Emulation 에서 **Enable GDB server** 를 켠다
(포트 기본 3333). 그 다음 게임을 멈춘 지점까지 몰고 가서 부른다.

    python3 scripts/gdb_peek.py
    python3 scripts/gdb_peek.py --around 24 --port 3333
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mips_dis import decode                  # noqa: E402

# PSX 코드가 놓이는 자리. PC 후보를 고를 때 쓴다.
CODE = ((0x80000000, 0x80200000), (0x00000000, 0x00200000),
        (0x1F000000, 0x1F800100), (0xBFC00000, 0xBFC80000))


# FF8 코드가 실제로 놓이는 자리. 커널·BIOS 와 갈라 보려고 따로 둔다 —
# 인터럽트 지점에서 붙잡으면 늘 예외 벡터가 잡혀서, 그것만 보면 아무것도 모른다.
GAME = ((0x80010000, 0x80090000, "부트 EXE"),
        (0x80090000, 0x800E0000, "메뉴 모듈"),
        (0x801F0000, 0x80210000, "menumain.ovl"))


def where(addr: int) -> str | None:
    for lo, hi, name in GAME:
        if lo <= addr < hi:
            return name
    return None


def frame(body: str) -> bytes:
    total = sum(body.encode()) & 0xFF
    return f"${body}#{total:02x}".encode()


class Link:
    """GDB 원격 프로토콜 한 줄짜리 구현."""

    def __init__(self, host: str, port: int, timeout: float):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def close(self) -> None:
        self.sock.close()

    def _read(self) -> str:
        """`$...#xx` 하나를 꺼낸다. 앞의 `+`/`-` 는 흘린다."""
        while True:
            start = self.buf.find(b"$")
            end = self.buf.find(b"#", start + 1) if start >= 0 else -1
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                body = self.buf[start + 1:end].decode(errors="replace")
                self.buf = self.buf[end + 3:]
                self.sock.sendall(b"+")
                return body
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("스텁이 끊었다")
            self.buf += chunk

    def ask(self, body: str) -> str:
        self.sock.sendall(frame(body))
        return self._read()

    def interrupt(self) -> None:
        """돌고 있으면 멈춰 세운다. 이미 멈췄으면 아무 일도 안 일어난다."""
        self.sock.sendall(b"\x03")
        try:
            self._read()
        except (socket.timeout, TimeoutError):
            pass


def words(hexed: str) -> list[int]:
    """`g` 응답을 u32 목록으로. 레지스터는 대상 바이트 순서(리틀)다."""
    out = []
    for i in range(0, len(hexed) - 7, 8):
        raw = bytes.fromhex(hexed[i:i + 8])
        out.append(int.from_bytes(raw, "little"))
    return out


def plausible(value: int) -> bool:
    return any(lo <= value < hi for lo, hi in CODE) and not (value & 3)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--around", type=int, default=16,
                        help="PC 앞뒤로 볼 명령 수 (기본 16)")
    parser.add_argument("--stay", action="store_true",
                        help="다 보고 나서도 멈춘 채로 둔다")
    parser.add_argument("--trace", type=int, default=0,
                        help="한 명령씩 N번 밟으며 PC 를 모은다. 멈춤 진단용")
    parser.add_argument("--watch", default="",
                        help="이 주소에 쓰기 감시점을 걸고 기다린다 (16진). "
                             "누가 그 자리를 망가뜨리는지 잡는다")
    parser.add_argument("--wait", type=float, default=600.0,
                        help="감시점이 걸릴 때까지 기다릴 초 (기본 600)")
    args = parser.parse_args()

    try:
        link = Link(args.host, args.port, args.timeout)
    except OSError as error:
        print(f"GDB 스텁에 못 붙었다: {error}\n"
              f"  {args.host}:{args.port}\n"
              "  PCSX-Redux 의 Configuration -> Emulation 에서\n"
              "  **Enable GDB server** 를 켠다.", file=sys.stderr)
        return 2

    link.interrupt()
    why = link.ask("?")
    print(f"멈춤 사유  {why}")

    if args.watch:
        addr = int(args.watch, 16) & 0xFFFFFFFF
        for kind, name in ((2, "쓰기"), (4, "접근")):
            reply = link.ask(f"Z{kind},{addr:x},4")
            if reply == "OK":
                print(f"{addr:#010x} 에 {name} 감시점을 걸었다. "
                      f"최대 {args.wait:.0f}초 기다린다 …")
                break
            print(f"  Z{kind} 는 안 먹는다 ({reply!r})")
        else:
            print("감시점을 못 걸었다", file=sys.stderr)
            return 1
        link.sock.sendall(frame("c"))
        link.sock.settimeout(args.wait)
        try:
            # 'O...' 는 타깃 콘솔 출력이다 — 진짜 정지 응답(S/T)이 올 때까지 건너뛴다.
            stop = link._read()
            while stop.startswith("O"):
                stop = link._read()
        except (TimeoutError, OSError):
            print("시간 안에 안 걸렸다. 재현이 안 됐거나 다른 경로다.")
            link.sock.settimeout(args.timeout)
            link.interrupt()
            link.ask(f"z2,{addr:x},4")
            link.sock.sendall(frame("c"))
            return 1
        link.sock.settimeout(args.timeout)
        print(f"**걸렸다** — 정지 응답 {stop!r}")
        regs = words(link.ask("g"))
        pc = regs[37]
        print(f"**쓴 자리 PC = {pc:#010x}**  [{where(pc) or '커널/BIOS'}]\n")
        base = pc - 12 * 4
        data = bytes.fromhex(link.ask(f"m{base:x},{24 * 4:x}"))
        for i in range(0, len(data) - 3, 4):
            at = base + i
            word = int.from_bytes(data[i:i + 4], "little")
            print(f"  {at:#010x}  {word:08x}  {decode(word, at)}"
                  f"{'  <== PC' if at == pc else ''}")
        names = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
                 "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()
        print()
        for i, name in enumerate(names):
            print(f"{name:>4} {regs[i]:#010x}", end="\n" if i % 4 == 3 else "   ")
        link.ask(f"z2,{addr:x},4")
        link.close()
        return 0

    # **인터럽트 지점에서 붙잡으면 늘 BIOS 예외 처리기가 잡힌다.** 그것만 보면
    # 게임이 어디에 있었는지 알 수 없다. 다행히 처리기가 EPC 를 TCB 에 넣어 둔다.
    #
    #   0x00000c90  addiu k0, zero, 0x100
    #   0x00000c94  lw    k0, 0x8(k0)      k0 = *(0x108)
    #   0x00000c9c  lw    k0, 0x0(k0)      k0 = *k0
    #   0x00000ca4  addi  k0, k0, 0x8
    #   0x00000cec  sw    v1, 0x80(k0)     v1 = mfc0 $14 (EPC)
    #
    # 그래서 `*(*(0x108)) + 8 + 0x80` 이 **게임의 진짜 PC** 다. 한 명령씩 밟는
    # 것보다 훨씬 빠르고, 인터럽트가 도는 한 언제 붙잡아도 옳은 값이 나온다.
    def peek(addr: int) -> int:
        return int.from_bytes(bytes.fromhex(link.ask(f"m{addr:x},4")), "little")

    saved = None
    try:
        tcb = peek(peek(0x108) & 0x1FFFFFFF) & 0x1FFFFFFF
        saved = peek(tcb + 8 + 0x80)
        print(f"TCB {tcb:#010x}   **끊긴 자리(EPC) = {saved:#010x}**  "
              f"[{where(saved) or '커널/BIOS'}]")
    except (ValueError, OSError):
        print("TCB 를 못 읽었다")

    if args.trace:
        print(f"한 명령씩 {args.trace:,}번 밟는다 …")
        seen: dict[int, int] = {}
        order: list[int] = []
        # `g` 는 72개를 다 실어 오니 `p25`(= 37번, pc) 하나만 받는다.
        # 스텁이 프레임마다 한 패킷씩만 처리해서 왕복이 비싸다 — 초당 20스텝쯤이다.
        for _ in range(args.trace):
            link.ask("s")
            pc = words(link.ask("p25"))[0]
            if pc not in seen:
                order.append(pc)
            seen[pc] = seen.get(pc, 0) + 1
        game = {a: n for a, n in seen.items() if where(a)}
        print(f"서로 다른 PC {len(seen)}개 — 그중 게임 코드 {len(game)}개\n")
        if not game:
            print("게임 코드에 한 번도 안 들어왔다. 커널 안에서만 돌고 있다 —\n"
                  "  인터럽트가 막힌 채 멈췄거나, BIOS 오류 루프다.")
        for addr, count in sorted(game.items(), key=lambda x: -x[1])[:20]:
            print(f"  {addr:#010x}  {count:>6}번  {count * 100 / args.trace:5.1f}%"
                  f"   [{where(addr)}]")
        lo = min(game) if game else 0
        hi = max(game) if game else 0
        if game:
            print(f"\n게임 코드 구간  {lo:#010x} .. {hi:#010x}  ({hi - lo + 4}바이트)")
        if not args.stay:
            link.sock.sendall(frame("c"))
            print("\n다시 실행시켰다.")
        link.close()
        return 0

    regs = words(link.ask("g"))
    print(f"레지스터 {len(regs)}개")

    # MIPS 의 `g` 순서는 GPR 32개 다음에 sr, lo, hi, bad, cause, pc 다.
    # 그 뒤로는 FPU 라서 볼 것이 없다. **0 도 유효한 주소라** 「코드처럼 보이는
    # 값을 뒤에서 고르기」로는 안 된다 — 그렇게 했다가 FPU 의 0 을 PC 로 읽었다.
    CP0 = ("sr", "lo", "hi", "bad", "cause", "pc")
    print("  " + "   ".join(f"{n} {regs[32 + i]:#010x}"
                            for i, n in enumerate(CP0) if 32 + i < len(regs)))
    if len(regs) <= 37:
        print("레지스터가 모자란다. 전부 찍는다:", file=sys.stderr)
        for i, value in enumerate(regs):
            print(f"  [{i:>2}] {value:#010x}")
        return 1

    pc = regs[37]
    cause = (regs[36] >> 2) & 0x1F
    WHY = {0: "인터럽트", 4: "주소 오류(읽기)", 5: "주소 오류(쓰기)",
           6: "버스 오류(명령)", 7: "버스 오류(자료)", 8: "syscall",
           9: "break", 10: "**없는 명령**", 12: "산술 넘침"}
    print(f"\n**PC = {pc:#010x}**   예외 사유 {cause} — "
          f"{WHY.get(cause, '기타')}   badvaddr {regs[35]:#010x}\n")
    if not plausible(pc):
        print("  PC 가 코드 자리가 아니다 — 엉뚱한 데로 뛴 것이다.\n")

    span = args.around
    base = pc - span * 4
    data = bytes.fromhex(link.ask(f"m{base:x},{span * 8:x}"))
    for i in range(0, len(data) - 3, 4):
        addr = base + i
        word = int.from_bytes(data[i:i + 4], "little")
        mark = "  <== PC" if addr == pc else ""
        print(f"  {addr:#010x}  {word:08x}  {decode(word, addr)}{mark}")

    print("\nGPR")
    names = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
             "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()
    for i, name in enumerate(names):
        end = "\n" if i % 4 == 3 else "   "
        print(f"{name:>4} {regs[i]:#010x}", end=end)
    print()

    # **다시 풀어준다.** 붙잡아 놓고 끝내면 게임이 멈춘 것처럼 보인다.
    if not args.stay:
        link.sock.sendall(frame("c"))
        print("\n다시 실행시켰다.")
    else:
        print("\n멈춘 채로 둔다 (--stay)")
    link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
