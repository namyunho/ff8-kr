#!/usr/bin/env python3
"""화면에 그려진 글자를 질의로 삼아 **그 원본이 VRAM·RAM 어디에 있는지** 찾는다.

## 왜 만드는가

「이 글자는 어느 폰트에서 왔나」를 코드부터 거슬러 올라가며 찾다가 한 세션을
통째로 날렸다(`docs/lessons.md` 15번). 답은 화면에 이미 떠 있었다. 화면 픽셀을
그대로 질의로 쓰면 한 번에 풀린다.

핵심은 **배경과 글자를 가르는 물리적 성질**이다. FF8 의 HP·이름 글자는 회색
계조(`r=g=b`)로 그려지고 전투 배경(풀·하늘)은 유채색이라, 무채색 판정만으로
글자 마스크가 깨끗하게 떨어진다. 밝기 문턱은 배경이 밝으면 무너지지만 이건
안 무너진다.

그 마스크와 같은 「0 이 아닌 인덱스」 모양을 VRAM 전체에서 찾는다. 폰트 텍스처는
거의 항상 4bpp 지만 8bpp 도 같이 본다.

RAM 검색은 다르다. RAM 은 2차원이 아니라 **한 줄이 몇 바이트인지(스트라이드)를
모른다.** 그래서 고정하지 않고, 첫 줄을 찾은 뒤 둘째 줄까지의 거리로 스트라이드를
역산하고 나머지 줄이 같은 간격으로 이어지는지 확인한다.

## 쓰는 법

에뮬레이터를 **멈춘 상태**로 두고 떠야 한다. 안 그러면 화면과 텍스처가 서로
다른 프레임이 된다.

    python3 scripts/find_vram_source.py --rect 177 173 13 12
    python3 scripts/find_vram_source.py --rect 177 173 13 12 --ram
    python3 scripts/find_vram_source.py --rect 177 173 13 12 --vram-file dump.bin
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
VRAM_W, VRAM_H = 1024, 512
RAM_BASE = 0x80000000


def fetch(path: str, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=timeout) as reply:
        return reply.read()


def load(kind: str, path: str | None) -> bytes:
    if path:
        with open(path, "rb") as handle:
            return handle.read()
    return fetch(f"{kind}/raw")


def achromatic_mask(vram: bytes, x0: int, y0: int, width: int, height: int
                    ) -> list[list[int]]:
    """화면 사각형에서 글자 마스크를 뽑는다. 무채색이면 글자로 본다."""
    rows = []
    for y in range(y0, y0 + height):
        row = []
        for x in range(x0, x0 + width):
            offset = (y * VRAM_W + x) * 2
            colour = vram[offset] | (vram[offset + 1] << 8)
            red, green, blue = colour & 31, (colour >> 5) & 31, (colour >> 10) & 31
            row.append(1 if (red == green == blue and colour) else 0)
        rows.append(row)
    return rows


def indexed_rows(vram: bytes, bpp: int) -> tuple[list[bytes], int]:
    """VRAM 을 bpp 로 읽어 '0 이 아니면 1' 인 바이트 행들로 편다."""
    per = 16 // bpp
    width = VRAM_W * per
    mask = (1 << bpp) - 1
    rows = []
    for y in range(VRAM_H):
        base = y * VRAM_W * 2
        out = bytearray(width)
        for i in range(VRAM_W):
            half = vram[base + i * 2] | (vram[base + i * 2 + 1] << 8)
            for k in range(per):
                if (half >> (k * bpp)) & mask:
                    out[i * per + k] = 1
        rows.append(bytes(out))
    return rows, width


def search_vram(vram: bytes, mask: list[list[int]], bpp: int,
                need_off: float = 0.75) -> list[tuple[float, int, int]]:
    rows, width = indexed_rows(vram, bpp)
    height, span = len(mask), len(mask[0])
    # 켜진 칸이 가장 많은 행을 닻으로 삼는다. bytes.find 가 C 속도로 좁혀 준다.
    anchor = max(range(height), key=lambda r: sum(mask[r]))
    pattern = bytes(mask[anchor])
    total_off = sum(span - sum(r) for r in mask)
    hits = []
    for top in range(VRAM_H - height + 1):
        line = rows[top + anchor]
        start = 0
        while True:
            left = line.find(pattern, start)
            if left < 0 or left + span > width:
                break
            start = left + 1
            good = True
            off_ok = 0
            for r in range(height):
                probe = rows[top + r]
                for c in range(span):
                    if mask[r][c] and not probe[left + c]:
                        good = False
                        break
                    if not mask[r][c] and not probe[left + c]:
                        off_ok += 1
                if not good:
                    break
            if good and (not total_off or off_ok / total_off >= need_off):
                hits.append((off_ok / max(total_off, 1), left, top))
    return sorted(hits, reverse=True)


def nibbles(vram: bytes, px: int, py: int, width: int, height: int
            ) -> list[list[int]]:
    out = []
    for y in range(py, py + height):
        row = []
        for p in range(px, px + width):
            offset = (y * VRAM_W + p // 4) * 2
            row.append(((vram[offset] | (vram[offset + 1] << 8)) >> ((p % 4) * 4)) & 0xF)
        out.append(row)
    return out


def search_ram(ram: bytes, rows: list[list[int]], min_rows: int = 8,
               max_stride: int = 1024) -> list[tuple[int, int, int]]:
    """스트라이드를 고정하지 않고 원본 비트맵을 찾는다.

    니블 정렬이 원본과 화면에서 다를 수 있으므로 시작 열을 0 과 1 로 두 번 본다.
    """
    found = []
    for shift in (0, 1):
        span = (len(rows[0]) - shift) // 2 * 2
        if span < 6:
            continue
        packed = [bytes(r[shift + i] | (r[shift + i + 1] << 4)
                        for i in range(0, span, 2)) for r in rows]
        solid = [i for i, r in enumerate(rows) if any(r[shift:shift + span])]
        if len(solid) < 3:
            continue
        base, nxt = solid[0], solid[1]
        gap = nxt - base
        start = 0
        while True:
            at = ram.find(packed[base], start)
            if at < 0:
                break
            start = at + 1
            for stride in range(1, max_stride + 1):
                probe = at + stride * gap
                if probe + len(packed[nxt]) > len(ram):
                    break
                if ram[probe:probe + len(packed[nxt])] != packed[nxt]:
                    continue
                ok = sum(1 for r in solid
                         if ram[at + (r - base) * stride:
                                at + (r - base) * stride + len(packed[r])] == packed[r])
                if ok >= min_rows:
                    found.append((ok, RAM_BASE + at - base * stride, stride))
    return sorted(found, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rect", nargs=4, type=int, required=True,
                        metavar=("x", "y", "폭", "높이"), help="화면 좌표")
    parser.add_argument("--ram", action="store_true",
                        help="VRAM 에서 찾은 뒤 RAM 원본까지 쫓는다")
    parser.add_argument("--vram-file")
    parser.add_argument("--ram-file")
    parser.add_argument("--bpp", type=int, nargs="*", default=[4, 8])
    args = parser.parse_args()

    x0, y0, width, height = args.rect
    vram = load("gpu/vram", args.vram_file)
    if len(vram) != VRAM_W * VRAM_H * 2:
        print(f"VRAM 크기가 이상하다: {len(vram)}", file=sys.stderr)
        return 2

    mask = achromatic_mask(vram, x0, y0, width, height)
    print(f"질의 모양  화면 ({x0},{y0}) {width}x{height}")
    for row in mask:
        print("   " + "".join("#" if c else "." for c in row))
    if not any(any(r) for r in mask):
        print("마스크가 비었다 — 사각형이 글자를 안 덮었거나 배경도 무채색이다",
              file=sys.stderr)
        return 1

    best = None
    for bpp in args.bpp:
        hits = search_vram(vram, mask, bpp)
        print(f"\n=== VRAM 을 {bpp}bpp 로 해석 — 일치 {len(hits)}곳 ===")
        for score, px, py in hits[:8]:
            per = 16 // bpp
            print(f"  {score * 100:5.1f}%  {bpp}bpp 픽셀 ({px},{py})"
                  f"  = halfword x={px // per} 니블 {px % per}, y={py}")
            if best is None and bpp == 4:
                best = (px, py)

    if args.ram and best:
        px, py = best
        print(f"\n=== RAM 에서 원본 찾기 (VRAM 4bpp ({px},{py}) 의 픽셀로) ===")
        ram = load("cpu/ram", args.ram_file)
        found = search_ram(ram, nibbles(vram, px, py, width, height))
        if not found:
            print("  없음 — 압축돼 있거나 이미 해제된 버퍼다")
        for ok, addr, stride in found[:8]:
            print(f"  {ok}행 일치   RAM {addr:#010x}   스트라이드 {stride}B"
                  f"  (4bpp 폭 {stride * 2}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
