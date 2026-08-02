#!/usr/bin/env python3
"""실행 중 VRAM 을 4중 인터리브 폰트 파일과 **바이트로** 대조한다.

## 왜 따로 만드는가

`verify_font_vram.py` 는 뱅크를 파일로 하나씩 올리던 시절 것이다. 4중
인터리브는 텍스처 하나 + 팔레트 32벌이라 볼 곳이 다르다.

그리고 이 검사가 없어서 놓친 것이 있다. 팔레트를 원본 CLUT 아래 16줄
`(288,240)` 에 뒀는데 **남의 CLUT 가 덮는 자리**였다. 실기에서 뱅크1 글자만
노이즈로 나왔고, 원인을 찾는 데 화면을 보고 되짚어야 했다. 팔레트 32줄을
파일과 대조했으면 즉시 나왔을 실패다.

    행별 일치 (덮이기 전)   OOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO
    행별 일치 (덮인 뒤)     OOOOOOOOOOOOOOOOXXXXXXXXXXXXXXXX

## 쓰는 법

PCSX-Redux 를 웹 서버를 켠 채로 띄우고 게임을 대사 화면까지 진행한 뒤 부른다.
동영상 전후로 두 번 부르면 살아남는지도 본다.

    python3 scripts/verify_font_4plane.py
    python3 scripts/verify_font_4plane.py --host 127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402

VRAM_W = 1024
DEFAULT_HOST = "127.0.0.1:8080"


def fetch(host: str) -> bytes:
    url = f"http://{host}/api/v1/gpu/vram/raw"
    with urllib.request.urlopen(url, timeout=30) as reply:
        return reply.read()


def chunks(font: bytes) -> tuple[bytes, tuple, bytes, tuple]:
    """폰트 파일에서 CLUT 청크와 텍스처 청크를 꺼낸다."""
    tim = int.from_bytes(font[4:8], "little")
    start = tim + 8                                     # 0x10, 0x08 매직 다음
    length = int.from_bytes(font[start:start + 4], "little")
    clut_rect = struct.unpack_from("<4H", font, start + 4)
    clut = font[start + 12:start + length]

    start += length
    length = int.from_bytes(font[start:start + 4], "little")
    tex_rect = struct.unpack_from("<4H", font, start + 4)
    tex = font[start + 12:start + length]
    return clut, clut_rect, tex, tex_rect


def read_rect(vram: bytes, x: int, y: int, w: int, h: int) -> bytes:
    """VRAM 에서 halfword 단위 사각형을 뜬다. w 는 halfword 수다."""
    out = bytearray()
    for row in range(h):
        off = ((y + row) * VRAM_W + x) * 2
        out += vram[off:off + w * 2]
    return bytes(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font", type=Path, default=Path("work/font-all/font.bin"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args()

    if not args.font.exists():
        print(f"폰트 파일이 없다: {args.font}", file=sys.stderr)
        return 2
    try:
        vram = fetch(args.host)
    except (urllib.error.URLError, OSError) as error:
        print(f"에뮬레이터에 붙지 못했다: {error}\n"
              f"  http://{args.host}\n"
              "  PCSX-Redux 의 웹 서버를 켠다.", file=sys.stderr)
        return 2
    if len(vram) != VRAM_W * 512 * 2:
        print(f"VRAM 크기가 이상하다: {len(vram):,}B", file=sys.stderr)
        return 1

    clut, (cx, cy, cw, ch), tex, (tx, ty, tw, th) = chunks(args.font.read_bytes())
    fail = 0

    print(f"CLUT  VRAM({cx},{cy})  {cw} x {ch}")
    rows = []
    for row in range(ch):
        want = clut[row * cw * 2:(row + 1) * cw * 2]
        rows.append(read_rect(vram, cx, cy + row, cw, 1) == want)
    print("  " + "".join("O" if good else "X" for good in rows))
    print(f"  일치 {sum(rows)}/{ch}")
    if not all(rows):
        fail += 1
        print("  **팔레트가 덮였다.** 평면 2,3 은 아래 16줄을 쓴다 — 거기가"
              " 깨지면 뱅크1 글자만 노이즈로 나온다.")

    print(f"\n텍스처  VRAM({tx},{ty})  {tw} halfword x {th}")
    got = read_rect(vram, tx, ty, tw, th)
    same = sum(1 for a, b in zip(got, tex) if a == b)
    print(f"  일치 {same:,}/{len(tex):,}바이트 ({same * 100 / len(tex):.1f}%)")
    if same != len(tex):
        fail += 1
        print("  **텍스처가 덮였다.** 동영상 뒤라면 재적재를 기다린다.")

    print(f"\n칸 {BF.PER_TEXTURE:,}  셀 {BF.CELLS_USED}  뱅크당 슬롯 "
          f"{BF.SLOTS_PER_BANK}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
