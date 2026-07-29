#!/usr/bin/env python3
"""갈무리 TTF 에서 FF8 형식의 12x12 한글 폰트 뱅크를 만든다.

**공급된 `.bin` 을 쓰지 않는다.** `fonts/galmuri11_16x16_12pt/*.bin` 은 약 10px
로 잘못 래스터라이즈돼 획이 누락돼 있다. 예를 들어 '선' 은 ㅓ 의 곁줄기가
빠져 '신' 처럼 보인다. TTF 에서 직접 다시 뽑아야 한다.

출력은 IMG TOC #130 과 같은 구조다.

    offset  크기      내용
    0x0000     4      u32 = 8    (폭 테이블 오프셋)
    0x0004     4      u32 = 452  (TIM 헤더 오프셋)
    0x0008   452      글리프 폭 테이블 (니블 팩, 뱅크당 904 엔트리)
    0x01C4     8      TIM 헤더  magic 0x10 / flag 0x08 (4bpp + CLUT)
    0x01CC    12      CLUT 블록 헤더  blockSize = 1036
    0x01D8  1024      CLUT 데이터
    0x05D8    12      IMG 블록 헤더  RECT (x, y) 64 x 252
    0x05E4 32256      픽셀 (256 x 252, 4bpp)

적재기 `sub_8002C358` 은 **블록을 건너뛸 때 `blockSize` 를 쓰고** VRAM 전송량은
RECT 로 정한다. 그래서 CLUT 블록의 `blockSize` 1036 이 픽셀 시작 위치를
결정한다. 이 값을 바꾸면 픽셀 시작이 밀려 글리프가 통째로 어긋난다.

게임은 TIM 의 RECT 좌표를 무시하고 하드코딩된 VRAM 좌표를 쓰므로, 여기 적는
좌표는 참고값이다. 자세한 내용은 docs/font-analysis.md 를 본다.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TTF = (PROJECT_ROOT / "fonts" / "galmuri11_16x16_12pt"
               / "font-58c1637749eb0742.ttf")
DEFAULT_MAP = (PROJECT_ROOT / "fonts" / "galmuri11_16x16_12pt"
               / "font-58c1637749eb0742_glyph_map.json")

CELL = 12               # FF8 글리프 셀
COLS, ROWS = 21, 21     # 256x252 텍스처의 격자
CELLS_PER_BANK = COLS * ROWS        # 441
PER_BANK = CELLS_PER_BANK * 2       # 882 — 셀 하나에 글리프 둘이 인터리브

# 2중 팔레트 인터리브
# ----------------------------------------------------------------------
# sub_8002EE90 / sub_8002E8F8 은 글리프 인덱스로 다음을 계산한다.
#
#   U = 12 * ((index >> 1) % 21)      V = 12 * ((index >> 1) / 21)
#   CLUT = 0x3812 (index 짝수)  또는  0x3852 (index 홀수)
#
# 즉 셀 위치는 index>>1 로 정하고 짝/홀은 CLUT 로 가른다. 4bpp 픽셀의
# 하위 2비트가 짝수 글리프, 상위 2비트가 홀수 글리프다. 원본 CLUT 도
# 이 구조를 그대로 보여준다.
#
#   pal0 = [c0 c1 c2 c3] * 4          -> 하위 2비트만 읽는다
#   pal1 = [c0*4 c1*4 c2*4 c3*4]      -> 상위 2비트만 읽는다
#
# 그래서 441칸짜리 텍스처가 882글리프를 담는다.
TEX_W, TEX_H = 256, 252
TIM_OFFSET = 452        # 원본 #130 과 동일
WIDTH_TABLE_BYTES = TIM_OFFSET - 8      # 파일상 444바이트
COPY_BYTES = 452        # sub_8002C358 이 실제로 복사하는 길이
GAP = 1                 # 글리프 오른쪽 여백 -> 진행폭 = 잉크폭 + GAP

# 적재기는 오프셋 8부터 452바이트를 복사하므로 마지막 8바이트는 TIM 헤더를
# 덮어 읽는다. 그 8바이트는 인덱스 888..903 에 해당하고 뱅크당 441칸만 쓰므로
# 무해하다. 원본이 이 구조이며, 크기를 33,764바이트로 맞춰야 TOC 재작성 없이
# in-place 교체가 된다.


def rasterize(ttf: Path, size: int, chars: list[str]) -> dict[str, list[list[int]]]:
    """TTF 를 지정 픽셀 크기로 래스터라이즈해 12x12 셀 비트맵을 만든다.

    **세로 위치는 글자마다 정하지 않고 전체 공통으로 잡는다.** 글자별
    잉크 높이로 중앙 정렬하면 받침 없는 '도'·'보' 가 받침 있는 '랄' 보다
    1픽셀 내려앉아 베이스라인이 흔들린다. 그래서 먼저 전체를 그려 잉크
    범위를 모은 뒤 하나의 세로 기준을 적용한다.

    가로는 글자별 중앙 정렬을 유지한다. 한글은 모아쓰기라 글자마다 폭이
    달라도 어색하지 않고, 폭 테이블이 진행폭을 따로 정한다.
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(ttf), size)
    box = CELL * 3

    drawn: dict[str, list[tuple[int, int]]] = {}
    for char in chars:
        image = Image.new("1", (box, box), 0)
        draw = ImageDraw.Draw(image)
        draw.fontmode = "1"          # 픽셀 폰트: 안티에일리어싱 금지
        draw.text((CELL, CELL - 2), char, font=font, fill=1)
        pixels = image.load()
        drawn[char] = [(x, y) for y in range(box) for x in range(box)
                       if pixels[x, y]]

    inked = [p for points in drawn.values() for p in points]
    if inked:
        top = min(y for _, y in inked)
        bottom = max(y for _, y in inked)
        span = bottom - top + 1
        if span > CELL:
            raise ValueError(
                f"글자들의 세로 범위가 {size}px 에서 {span}px 로 셀 {CELL} 을 "
                f"넘는다. --size 를 줄인다."
            )
        base_y = (CELL - span) // 2 - top
    else:
        base_y = 0

    cells: dict[str, list[list[int]]] = {}
    for char, points in drawn.items():
        cell = [[0] * CELL for _ in range(CELL)]
        if points:
            min_x = min(x for x, _ in points)
            width = max(x for x, _ in points) - min_x + 1
            if width > CELL:
                raise ValueError(
                    f"'{char}' 가 {size}px 에서 가로 {width}px 로 셀을 넘는다. "
                    f"--size 를 줄인다."
                )
            off_x = (CELL - width) // 2
            for x, y in points:
                cell[y + base_y][x - min_x + off_x] = 1
        cells[char] = cell
    return cells


def ink_width(cell: list[list[int]]) -> int:
    columns = [x for y in range(CELL) for x in range(CELL) if cell[y][x]]
    return (max(columns) + 1) if columns else 0


def pack_bank(cells: list[list[list[int]]], level: int) -> bytes:
    """882개 글리프를 441칸 256x252 4bpp 텍스처에 인터리브해 넣는다.

    글리프 index 는 `index >> 1` 번째 셀에 들어가고, 짝수는 픽셀의 하위
    2비트, 홀수는 상위 2비트를 쓴다. level 은 0..3 중 잉크 농도다.
    """
    texture = bytearray(TEX_W * TEX_H // 2)
    for index, cell in enumerate(cells):
        slot = index >> 1
        odd = index & 1
        base_x = (slot % COLS) * CELL
        base_y = (slot // COLS) * CELL
        for y in range(CELL):
            row = base_y + y
            for x in range(CELL):
                if not cell[y][x]:
                    continue
                column = base_x + x
                offset = row * (TEX_W // 2) + column // 2
                # 픽셀 안에서의 2비트 위치
                value = (level & 3) << (2 if odd else 0)
                mask = 0b1100 if odd else 0b0011
                if column & 1:                       # 바이트의 상위 니블
                    texture[offset] = (texture[offset] & ~(mask << 4) & 0xFF) \
                                      | (value << 4)
                else:                                # 바이트의 하위 니블
                    texture[offset] = (texture[offset] & ~mask & 0xFF) | value
    return bytes(texture)


def pack_widths(widths: list[int]) -> bytes:
    """니블 팩. sub_8002E3EC 규칙: table[i >> 1] 의 하위/상위 니블."""
    table = bytearray(WIDTH_TABLE_BYTES)
    for index, value in enumerate(widths[:WIDTH_TABLE_BYTES * 2]):
        value &= 0xF
        position = index >> 1
        if index & 1:
            table[position] = (table[position] & 0x0F) | (value << 4)
        else:
            table[position] = (table[position] & 0xF0) | value
    return bytes(table)


def build_clut() -> bytes:
    """원본과 같은 4색 x 4반복 구성. 하위 2비트만 색을 정한다."""
    def bgr555(r: int, g: int, b: int) -> int:
        return (b >> 3) << 10 | (g >> 3) << 5 | (r >> 3)

    base = [bgr555(0, 0, 0), bgr555(0x52, 0x5A, 0x52),
            bgr555(0x63, 0x63, 0x63), bgr555(0xA5, 0xA5, 0xA5)]
    entries = (base * 4)                      # 16색 팔레트 1개
    data = bytearray()
    for _ in range(32):                       # 1024바이트 = 32팔레트분
        for value in entries:
            data += struct.pack("<H", value)
    return bytes(data[:1024])


def build_bank(cells: list[list[list[int]]], widths: list[int],
               vram_x: int, vram_y: int, level: int) -> bytes:
    padded = cells + [[[0] * CELL for _ in range(CELL)]] * (PER_BANK - len(cells))
    pixels = pack_bank(padded[:PER_BANK], level)
    clut = build_clut()

    out = bytearray()
    out += struct.pack("<II", 8, TIM_OFFSET)
    out += pack_widths(widths)
    out += struct.pack("<II", 0x10, 0x08)                       # TIM magic / flag
    out += struct.pack("<IHHHH", 12 + len(clut), 896, 256, 16, 16)
    out += clut
    out += struct.pack("<IHHHH", 12 + len(pixels), vram_x, vram_y, 64, TEX_H)
    out += pixels
    return bytes(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ttf", type=Path, default=DEFAULT_TTF)
    parser.add_argument("--glyph-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--size", type=int, default=12,
                        help="래스터 픽셀 크기. 11 은 폰트 설계 크기, "
                             "12 는 12x12 셀을 더 채운다 (기본 12)")
    parser.add_argument("--banks", type=int, default=3,
                        help="만들 뱅크 수 (기본 3 = 2,646칸으로 완성형 전체 수용)")
    parser.add_argument("--level", type=int, default=3, choices=range(4),
                        help="잉크 픽셀 값 0..3 (기본 3 = 가장 밝음)")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "work" / "font")
    args = parser.parse_args()

    glyph_map = json.loads(args.glyph_map.read_text(encoding="utf-8"))
    # 두 가지 입력을 받는다. 완성형 전체 대응표는 문자를 **키**로 담고,
    # `count_korean_syllables.py --layout` 이 내는 배치 후보는 빈도순으로
    # 정렬한 `chars` 배열을 담는다. 배치를 그대로 폰트로 만들 수 있어야
    # 음절 수 판정이 실행 가능한 결론이 된다.
    chars = (glyph_map["chars"] if isinstance(glyph_map, dict)
             and "chars" in glyph_map else list(glyph_map))
    print(f"음절 {len(chars)}자, 래스터 {args.size}px, 뱅크 {args.banks}개 "
          f"(수용 {args.banks * PER_BANK}칸)")

    cells = rasterize(args.ttf, args.size, chars)
    args.output.mkdir(parents=True, exist_ok=True)

    assignment: dict[str, tuple[int, int]] = {}
    total = 0
    for bank in range(args.banks):
        chunk = chars[bank * PER_BANK:(bank + 1) * PER_BANK]
        if not chunk:
            break
        for slot, char in enumerate(chunk):
            assignment[char] = (bank, slot)
        bank_cells = [cells[c] for c in chunk]
        widths = [ink_width(cell) + GAP for cell in bank_cells]
        # 원본 뱅크 0/1 은 (832,256) / (960,256). 뱅크 2 이상은 빈 VRAM 을 쓴다.
        vram_x = (832, 960, 320, 384, 448)[bank] if bank < 5 else 320
        data = build_bank(bank_cells, widths, vram_x, 256, args.level)
        path = args.output / f"bank{bank:02d}.bin"
        path.write_bytes(data)
        total += len(chunk)
        print(f"  bank{bank:02d}  {len(chunk):>3}자  VRAM({vram_x},256)  "
              f"{len(data):,}B  -> {path.name}")

    (args.output / "assignment.json").write_text(
        json.dumps({c: list(v) for c, v in assignment.items()},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"배정 {total}/{len(chars)}자, 미배정 {len(chars) - total}자")
    print(f"배정표 -> {args.output / 'assignment.json'}")
    if total < len(chars):
        print("남는 음절은 서브셋팅으로 버리거나 뱅크를 더 늘린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
