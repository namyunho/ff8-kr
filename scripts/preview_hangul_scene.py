#!/usr/bin/env python3
"""한 씬에 필요한 음절만 원본 폰트에 얹어 한글 화면을 미리 본다.

디스크를 고치기 전에 세 가지를 먼저 판정하려는 도구다.

1. 이 씬이 쓰는 **음절 수**가 몇 개인가 (뱅크 0 의 882 안에 드는가)
2. 12x12 셀에서 한글이 **읽히는가**
3. 번역문이 원본 **바이트 예산**에 들어가는가

원본을 수정하지 않는다. 출력은 미리보기 PNG 와 배정표 JSON 뿐이다.

배정은 **단일바이트 영역(인덱스 0..223)을 먼저 쓴다.** 그 영역은 한 글자가
1바이트라, 2바이트인 한자를 대체하면 오히려 예산이 남는다. 224 를 넘으면
2바이트 lead(`0x18`~`0x1B`)로 넘어간다.

    python3 scripts/extract_field_text.py 293 --worksheet work/text/f293.csv
    # CSV 의 ko 칸을 채운 뒤
    python3 scripts/preview_hangul_scene.py work/text/f293.csv \\
        --render work/text/f293-ko.png --json work/text/f293-map.json
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as HF          # noqa: E402
import extract_field_text as FT         # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SINGLE_BYTE_LIMIT = 224     # 인덱스 0..223 은 바이트 하나로 쓴다
BANK0_CAPACITY = 882        # 441칸 x 2 인터리브
LEAD_BASE = 0x18            # 2바이트 문자, 뱅크 0


def load_worksheet(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle)]


def collect_syllables(rows: list[dict]) -> list[str]:
    """등장 순서를 유지해 중복 없이 모은다. 순서가 재현 가능해야 한다.

    **띄어쓰기도 글리프 하나로 배정한다.** 한국어는 공백이 의미를 가르므로
    버리면 안 된다. 비트맵이 빈 글리프라 1바이트만 쓰고 폭 테이블에서
    진행폭을 따로 줄 수 있다. 줄바꿈은 제어 코드 `0x02` 가 담당하므로
    여기서 제외한다.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for char in (row.get("ko", "") or "").replace("\\n", "\n"):
            if char == "\n":
                continue
            seen.setdefault(char, None)
    return list(seen)


def assign_indices(syllables: list[str]) -> dict[str, int]:
    return {char: index for index, char in enumerate(syllables)}


def split_lines(text: str) -> list[str]:
    """워크시트는 실제 줄바꿈과 리터럴 `\\n` 을 모두 허용한다."""
    return [piece for piece in text.replace("\\n", "\n").split("\n")]


def encode(text: str, assignment: dict[str, int]) -> tuple[bytes, list[str]]:
    """한글 문자열을 FF8 바이트 열로 만든다.

    줄바꿈은 제어 코드 `0x02` 가 담당하므로 여기서 인코딩하지 않는다.
    배정표에 없는 문자는 그대로 돌려줘 호출자가 보고하게 한다.
    """
    out = bytearray()
    unmapped: list[str] = []
    for char in text.replace("\\n", "\n"):
        if char == "\n":                    # 줄바꿈은 제어 코드가 담당한다
            continue
        index = assignment.get(char)
        if index is None:
            unmapped.append(char)
            continue
        if index < SINGLE_BYTE_LIMIT:
            out.append(index + 32)
        else:
            offset = index - SINGLE_BYTE_LIMIT
            out.append(LEAD_BASE + 1 + offset // 224)
            out.append(32 + offset % 224)
    return bytes(out), unmapped


def control_byte_cost(field: str) -> int:
    """워크시트의 control_codes 문자열이 차지하는 바이트 수를 센다."""
    total = 0
    for token in field.split():
        if not token.startswith("ctrl:"):
            continue
        total += 2 if "," in token else 1
    return total


def overlay_font(cells: dict[str, list[list[int]]], assignment: dict[str, int],
                 level: int) -> FT.Font:
    """원본 sysfnt 픽셀을 복사한 뒤 배정된 인덱스에만 한글을 덮어쓴다."""
    blob = bytearray(FT.SYSFNT.read_bytes())
    base = FT.system_font()
    pixel = base.pixel_offset
    stride = base.stride

    for char, index in assignment.items():
        cell = cells.get(char)
        if cell is None:
            continue
        target = index >> 1
        cx, cy = (target % FT.COLS) * FT.CELL, (target // FT.COLS) * FT.CELL
        high = index & 1
        for y in range(FT.CELL):
            for x in range(FT.CELL):
                px, py = cx + x, cy + y
                pointer = pixel + py * stride + (px >> 1)
                if pointer >= len(blob):
                    continue
                value = min(cell[y][x], level) if cell[y][x] else 0
                packed = blob[pointer]
                low_nibble = not (px & 1)
                nibble = packed & 0xF if low_nibble else (packed >> 4) & 0xF
                # 짝수 글리프는 하위 2비트, 홀수 글리프는 상위 2비트를 쓴다.
                if high:
                    nibble = (nibble & 0x3) | ((value & 3) << 2)
                else:
                    nibble = (nibble & 0xC) | (value & 3)
                blob[pointer] = ((packed & 0xF0) | nibble) if low_nibble \
                    else ((packed & 0x0F) | (nibble << 4))

    return FT.Font(bytes(blob), pixel, stride * 2)


def render(rows: list[dict], assignment: dict[str, int], font: FT.Font,
           path: Path) -> None:
    from PIL import Image, ImageDraw

    lines: list[tuple[str, list[int]]] = []
    for row in rows:
        text = row.get("ko", "") or ""
        if not text.strip():
            continue
        for piece in split_lines(text):
            lines.append((row["id"], [assignment[c] for c in piece
                                      if c in assignment]))

    if not lines:
        raise SystemExit("워크시트의 ko 칸이 비어 있다. 번역문을 채운 뒤 다시 실행한다.")

    width = 60 + 34 * FT.CELL
    height = 12 + FT.LINE_HEIGHT * len(lines)
    scale = FT.SCALE
    image = Image.new("RGB", (width * scale, height * scale), FT.BACKGROUND)
    pixels = image.load()
    draw = ImageDraw.Draw(image)

    for row_index, (message_id, indices) in enumerate(lines):
        top = 6 + row_index * FT.LINE_HEIGHT
        draw.text((6 * scale, (top + 2) * scale), f"{message_id:>3}",
                  fill=(120, 130, 150))
        pen = 46
        for index in indices:
            cell = font.glyph(index)
            for y in range(FT.CELL):
                for x in range(FT.CELL):
                    colour = FT.SHADE[cell[y][x]]
                    if colour is None:
                        continue
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = (pen + x) * scale + sx, (top + y) * scale + sy
                            if 0 <= px < width * scale and 0 <= py < height * scale:
                                pixels[px, py] = colour
            pen += FT.CELL
            if pen > width - FT.CELL:
                break

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worksheet", type=Path)
    parser.add_argument("--ttf", type=Path, default=HF.DEFAULT_TTF)
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--level", type=int, default=3, choices=range(1, 4),
                        help="글리프 계조 상한 (원본은 3단계)")
    parser.add_argument("--render", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    rows = load_worksheet(args.worksheet)
    syllables = collect_syllables(rows)
    assignment = assign_indices(syllables)

    print(f"워크시트 {args.worksheet}  메시지 {len(rows)}개")
    print(f"필요한 음절 {len(syllables)}종 "
          f"(단일바이트 영역 224 중 {min(len(syllables), SINGLE_BYTE_LIMIT)} 사용)")
    if len(syllables) > BANK0_CAPACITY:
        print(f"경고: 뱅크 0 수용량 {BANK0_CAPACITY} 를 넘는다")

    report = []
    over = 0
    for row in rows:
        text = row.get("ko", "") or ""
        if not text.strip():
            continue
        encoded, unmapped = encode(text, assignment)
        budget = int(row["byte_budget"])
        used = len(encoded) + control_byte_cost(row.get("control_codes", ""))
        fits = used <= budget
        if not fits:
            over += 1
        if unmapped:
            print(f"  #{row['id']} 배정되지 않은 문자: {''.join(sorted(set(unmapped)))}")
        report.append({"id": int(row["id"]), "budget": budget,
                       "encoded": used, "fits": fits})

    translated = len(report)
    print(f"번역된 메시지 {translated}개 중 예산 초과 {over}개")
    if report:
        worst = max(report, key=lambda r: r["encoded"] - r["budget"])
        print(f"가장 빠듯한 것: #{worst['id']} 예산 {worst['budget']} "
              f"사용 {worst['encoded']}")

    if args.render:
        cells = HF.rasterize(args.ttf, args.size, syllables)
        render(rows, assignment, overlay_font(cells, assignment, args.level),
               args.render)
        print(f"{args.render}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"syllables": len(syllables), "assignment": assignment,
             "messages": report}, indent=2, ensure_ascii=False))
        print(f"{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
