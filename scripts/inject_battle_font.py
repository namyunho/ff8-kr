#!/usr/bin/env python3
"""손으로 그린 전투 이름 글꼴 그림을 읽어 게임이 쓰는 4bpp 인덱스로 되돌린다.

## 왜 필요한가

전투 캐릭터 이름은 뱅크0 와 **같은 인덱스 공간**을 쓰는 별도의 일본어 글꼴에서
조립된다(`docs/roadmap.md` 항목 6). 그 글꼴을 한글로 갈아 끼우려면 사람이 그린
그림을 다시 인덱스로 환원해야 한다.

## 그림과 버퍼는 배치가 다르다

버퍼는 **12픽셀씩 이어 붙인 연속 스트림**이고 한 줄이 256픽셀이다. `256` 이
`12` 로 안 나눠떨어져서(`256 = 21*12 + 4`) 줄 끝 글자가 다음 줄로 걸친다.
논리 좌표에서 버퍼 좌표로 가는 식은 하나뿐이다.

    k = (12*b + j) * 256 + 24 + 12*c + i      # 버퍼 선형 픽셀
    인덱스 = b * 21 + c

## 색은 CLUT 로 되돌린다

그림은 16단계 회색으로 그려져 있다. CLUT(VRAM `(256,226)`) 의 16색 중 가장
가까운 것을 골라 인덱스로 되돌린다. 편집기가 색을 조금 흔들어도(안티에일리어싱,
색공간 변환) 가장 가까운 칸으로 붙는다.

    python3 scripts/inject_battle_font.py 그림.png --report
    python3 scripts/inject_battle_font.py 그림.png --out work/analysis/battle-font/edited.bin
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

COLS, BANDS, CELL = 21, 11, 12
SHEET_W, SHEET_H = COLS * CELL, BANDS * CELL      # 252 x 132
BUF_W = 256                                        # 버퍼 한 줄의 4bpp 픽셀 수

# VRAM (256,226) 에서 실측한 16색. 회색값(0~31) 로 적는다. 0 은 투명.
CLUT_GRAY = [None, 14, 31, 28, 26, 24, 21, 19, 17, 15, 13, 11, 9, 7, 5, 3]


def clut_rgb() -> list[tuple[int, int, int] | None]:
    return [None if g is None else (g * 255 // 31,) * 3 for g in CLUT_GRAY]


# --------------------------------------------------------------------------
# PNG 를 직접 푼다. 편집기마다 색 형식이 달라서 네 가지를 다 받는다.

def read_png(path: Path) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"PNG 가 아니다: {path}")
    pos, idat, palette, trns = 8, bytearray(), None, None
    width = height = depth = colour = 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    # 팔레트 PNG 는 편집기가 1·2·4비트로 내보내는 일이 잦다. 그것도 받는다.
    if depth not in (1, 2, 4, 8):
        raise ValueError(f"1·2·4·8비트만 받는다 (이 파일은 {depth}비트)")
    if depth != 8 and colour not in (0, 3):
        raise ValueError(f"{depth}비트는 회색조·팔레트에서만 쓴다 (색 형식 {colour})")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(bytes(idat))
    stride = (width * channels * depth + 7) // 8
    out, prev = [], bytearray(stride)
    # 필터가 참고하는 '왼쪽 픽셀'의 거리. 8비트 미만이면 1바이트다.
    back = max(1, channels * depth // 8)
    at = 0
    for _ in range(height):
        filt = raw[at]; at += 1
        line = bytearray(raw[at:at + stride]); at += stride
        for i in range(stride):
            a = line[i - back] if i >= back else 0
            b = prev[i]
            c = prev[i - back] if i >= back else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else
                                      b if pb <= pc else c)) & 0xFF
        row = []
        if depth != 8:
            # 한 바이트에 픽셀 여러 개. 왼쪽이 상위 비트다.
            values, per = [], 8 // depth
            mask = (1 << depth) - 1
            for byte in line:
                for k in range(per):
                    values.append((byte >> (8 - depth * (k + 1))) & mask)
            top = (1 << depth) - 1
            for x in range(width):
                v = values[x]
                if colour == 3:
                    r, g, b = palette[v]
                    alpha = trns[v] if trns and v < len(trns) else 255
                    row.append((r, g, b, alpha))
                else:
                    level = v * 255 // top
                    row.append((level, level, level, 255))
            out.append(row)
            prev = line
            continue
        for x in range(width):
            px = line[x * channels:(x + 1) * channels]
            if colour == 0:
                row.append((px[0], px[0], px[0], 255))
            elif colour == 4:
                row.append((px[0], px[0], px[0], px[1]))
            elif colour == 2:
                row.append((px[0], px[1], px[2], 255))
            elif colour == 6:
                row.append((px[0], px[1], px[2], px[3]))
            else:
                r, g, b = palette[px[0]]
                alpha = trns[px[0]] if trns and px[0] < len(trns) else 255
                row.append((r, g, b, alpha))
        out.append(row)
        prev = line
    return width, height, out


# --------------------------------------------------------------------------

def to_index(pixel: tuple[int, int, int, int]) -> int:
    """색 하나를 CLUT 인덱스로. 투명하거나 완전한 검정은 0."""
    r, g, b, a = pixel
    if a < 128:
        return 0
    table = clut_rgb()
    best, dist = 0, None
    for i, want in enumerate(table):
        if want is None:                       # 인덱스 0 = 투명/검정
            want = (0, 0, 0)
        d = (r - want[0]) ** 2 + (g - want[1]) ** 2 + (b - want[2]) ** 2
        if dist is None or d < dist:
            best, dist = i, d
    return best


def buffer_offset(b: int, c: int, j: int, i: int) -> int:
    """논리 (줄,열,글자행,글자내x) -> 버퍼 선형 픽셀."""
    return (12 * b + j) * BUF_W + 24 + 12 * c + i


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", help=f"{SHEET_W}x{SHEET_H} 그림")
    parser.add_argument("--out", help="논리 시트 인덱스를 이 파일로 쓴다(126B 스트라이드)")
    parser.add_argument("--base", default="work/analysis/battle-font/battle-name-font-logical.bin",
                        help="비교할 원본 논리 시트")
    parser.add_argument("--report", action="store_true", help="바뀐 칸을 센다")
    args = parser.parse_args()

    width, height, pixels = read_png(Path(args.image))
    if (width, height) != (SHEET_W, SHEET_H):
        print(f"크기가 {SHEET_W}x{SHEET_H} 여야 한다 (받은 것 {width}x{height})",
              file=sys.stderr)
        return 2

    sheet = [[to_index(pixels[y][x]) for x in range(SHEET_W)] for y in range(SHEET_H)]

    if args.out:
        blob = bytearray(SHEET_H * (SHEET_W // 2))
        for y in range(SHEET_H):
            for x in range(0, SHEET_W, 2):
                blob[y * (SHEET_W // 2) + x // 2] = sheet[y][x] | (sheet[y][x + 1] << 4)
        Path(args.out).write_bytes(bytes(blob))
        print(f"{args.out} 에 {len(blob)}바이트")

    if args.report:
        base_path = Path(args.base)
        if not base_path.exists():
            print(f"원본이 없다: {base_path}", file=sys.stderr)
            return 2
        base = base_path.read_bytes()

        def base_px(x: int, y: int) -> int:
            return (base[y * (SHEET_W // 2) + x // 2] >> ((x % 2) * 4)) & 0xF

        changed = []
        for b in range(BANDS):
            for c in range(COLS):
                same = all(sheet[b * CELL + j][c * CELL + i] == base_px(c * CELL + i, b * CELL + j)
                           for j in range(CELL) for i in range(CELL))
                if not same:
                    changed.append(b * COLS + c)
        used = sum(1 for b in range(BANDS) for c in range(COLS)
                   if any(sheet[b * CELL + j][c * CELL + i]
                          for j in range(CELL) for i in range(CELL)))
        print(f"글자가 든 칸 {used} / {BANDS * COLS}")
        print(f"원본과 다른 칸 {len(changed)}개")
        if changed:
            print("  인덱스: " + " ".join(str(i) for i in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
