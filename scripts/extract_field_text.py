#!/usr/bin/env python3
"""필드 대사(MSD)를 뽑아 번역 작업용 자료로 만든다.

경로는 다음과 같다. 각 단계의 근거는 `docs/font-analysis.md` 에 있다.

    field.bin (IMG TOC #2)  → LZSS 해제 → +0x28668 의 필드 목록 1,003엔트리
    목록은 [MIM, DAT, LZK] 3종 세트가 반복된다 (가운데가 DAT)
    DAT → LZSS 해제 → 선두 u32 포인터 12개, 첫 섹션은 오프셋 48
    포인터 [8] 이 MSD 섹션
    MSD = [u32 오프셋 배열][텍스트],  N = 첫 오프셋 / 4

글리프 인덱스와 한자의 대응표는 아직 없다. 그래서 **원본 폰트로 렌더한
이미지**를 함께 낸다. 사람이 그림을 보고 번역문을 채우는 것이 현재로선
가장 확실하다. 뱅크 1(2바이트 lead `0x1C`~`0x1F`)은 그 필드의 MIM 안에
들어 있는 전용 폰트(TDW)를 쓴다.

    python3 scripts/extract_field_text.py 104
    python3 scripts/extract_field_text.py 104 --render work/text/f104.png
    python3 scripts/extract_field_text.py 104 --json work/text/f104.json
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIN_PATH = PROJECT_ROOT / "roms" / "Final Fantasy VIII (Japan, Asia) (Disc 1).bin"
FIELD_BIN = PROJECT_ROOT / "work" / "extracted" / "img_002_lba33249.dec.bin"
SYSFNT = PROJECT_ROOT / "work" / "extracted" / "img_130_lba849.bin"

RAW_SECTOR = 2352
USER_DATA = 2048
USER_OFFSET = 24                # Mode 2 Form 1. 16 이 아니다.

FIELD_LIST_OFFSET = 0x28668
FIELD_LIST_COUNT = 1003
DAT_POINTERS = 12
DAT_FIRST_SECTION = 48
MSD_POINTER = 8                 # 섹션 9

# MIM 안의 필드 전용 폰트(TDW) 위치. docs/font-analysis.md 참조.
MIM_TDW_OFFSET = 438284

# 폰트 텍스처 격자. 셀 하나에 글리프 둘이 인터리브된다.
CELL = 12
COLS = 21

# 파라미터 1바이트를 소비하는 제어 코드. sub_8002E4A0 이 정본이다.
CTRL_WITH_PARAM = {3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15}


def read_sectors(lba: int, nbytes: int) -> bytes:
    """절대 LBA 에서 사용자 데이터만 이어 붙인다."""
    out = bytearray()
    with BIN_PATH.open("rb") as handle:
        for index in range((nbytes + USER_DATA - 1) // USER_DATA):
            handle.seek((lba + index) * RAW_SECTOR)
            sector = handle.read(RAW_SECTOR)
            out += sector[USER_OFFSET:USER_OFFSET + USER_DATA]
    return bytes(out[:nbytes])


def lzss_decode(src: bytes) -> bytes:
    """Okumura 계열. 링버퍼 4096, 초기 위치 0xFEE, 제어 바이트 LSB 우선."""
    ring = bytearray(4096)
    pos = 0xFEE
    out = bytearray()
    i = 0
    while i < len(src):
        control = src[i]
        i += 1
        for bit in range(8):
            if i >= len(src):
                break
            if control & (1 << bit):
                byte = src[i]
                i += 1
                out.append(byte)
                ring[pos] = byte
                pos = (pos + 1) & 0xFFF
            else:
                if i + 1 >= len(src):
                    break
                lo, hi = src[i], src[i + 1]
                i += 2
                offset = lo | ((hi & 0xF0) << 4)
                length = (hi & 0x0F) + 3
                for k in range(length):
                    byte = ring[(offset + k) & 0xFFF]
                    out.append(byte)
                    ring[pos] = byte
                    pos = (pos + 1) & 0xFFF
    return bytes(out)


def field_list() -> list[tuple[int, int]]:
    blob = FIELD_BIN.read_bytes()
    return [struct.unpack_from("<II", blob, FIELD_LIST_OFFSET + 8 * i)
            for i in range(FIELD_LIST_COUNT)]


def load_entry(index: int) -> bytes:
    lba, size = field_list()[index]
    return lzss_decode(read_sectors(lba, size)[4:])


def msd_section(dat: bytes) -> bytes:
    pointers = struct.unpack_from(f"<{DAT_POINTERS}I", dat, 0)
    base = pointers[0]
    start = pointers[MSD_POINTER] - base + DAT_FIRST_SECTION
    end = pointers[MSD_POINTER + 1] - base + DAT_FIRST_SECTION
    return dat[start:end]


def message_offsets(msd: bytes) -> list[int]:
    """첫 오프셋이 배열의 끝을 가리킨다."""
    count = struct.unpack_from("<I", msd, 0)[0] // 4
    return [struct.unpack_from("<I", msd, 4 * i)[0] for i in range(count)]


def tokenize(msd: bytes, start: int) -> list[tuple]:
    """원시 메시지를 토큰 열로 편다. ('g', index) 또는 ('ctrl', code, param)."""
    tokens = []
    pos = start
    while pos < len(msd):
        byte = msd[pos]
        if byte == 0:
            break
        if byte in CTRL_WITH_PARAM:
            tokens.append(("ctrl", byte, msd[pos + 1]))
            pos += 2
        elif 0x18 <= byte <= 0x1B:
            tokens.append(("g", 224 * byte + msd[pos + 1] - 5408))
            pos += 2
        elif 0x1C <= byte <= 0x1F:
            tokens.append(("g", (224 * byte + msd[pos + 1] - 6304) | 0x400))
            pos += 2
        elif byte < 0x20:
            tokens.append(("ctrl", byte, None))
            pos += 1
        else:
            tokens.append(("g", byte - 32))
            pos += 1
    return tokens


class Font:
    """4bpp 텍스처에서 12x12 글리프를 꺼낸다.

    셀 = 인덱스 >> 1 이고 짝/홀은 니블의 하위/상위 2비트로 갈린다.
    """

    def __init__(self, blob: bytes, pixel_offset: int, width: int):
        self.blob = blob
        self.pixel_offset = pixel_offset
        self.stride = width // 2

    def glyph(self, index: int) -> list[list[int]]:
        cell = index >> 1
        cx, cy = (cell % COLS) * CELL, (cell // COLS) * CELL
        high = index & 1
        rows = []
        for y in range(CELL):
            row = []
            for x in range(CELL):
                px, py = cx + x, cy + y
                pointer = self.pixel_offset + py * self.stride + (px >> 1)
                if pointer >= len(self.blob):
                    row.append(0)
                    continue
                packed = self.blob[pointer]
                value = (packed >> 4) if (px & 1) else (packed & 0xF)
                row.append((value >> 2) & 3 if high else value & 3)
            rows.append(row)
        return rows


def system_font() -> Font:
    blob = SYSFNT.read_bytes()
    table_offset, tim_offset = struct.unpack_from("<II", blob, 0)
    clut_block = tim_offset + 8
    clut_size = struct.unpack_from("<I", blob, clut_block)[0]
    image_block = clut_block + clut_size
    return Font(blob, image_block + 12, 256)


def field_font(mim: bytes) -> Font | None:
    """MIM 섹션 3 의 TDW. 없으면 None."""
    if len(mim) <= MIM_TDW_OFFSET + 8:
        return None
    table_offset, tim_offset = struct.unpack_from("<II", mim, MIM_TDW_OFFSET)
    if table_offset != 8:
        return None
    tim = MIM_TDW_OFFSET + tim_offset
    magic = struct.unpack_from("<I", mim, tim)[0]
    if magic != 0x10:
        return None
    clut_block = tim + 8
    clut_size = struct.unpack_from("<I", mim, clut_block)[0]
    image_block = clut_block + clut_size
    width = struct.unpack_from("<H", mim, image_block + 8)[0] * 4
    return Font(mim, image_block + 12, width)


SHADE = {0: None, 1: (82, 90, 82), 2: (99, 99, 99), 3: (214, 214, 214)}
BACKGROUND = (18, 18, 26)
LINE_HEIGHT = 15
SCALE = 2
MARGIN = 6


def render(messages: list[dict], bank0: Font, bank1: Font | None,
           path: Path) -> None:
    from PIL import Image, ImageDraw

    lines = []
    for message in messages:
        current = []
        for token in message["tokens_raw"]:
            if token[0] == "g":
                current.append(token[1])
            elif token[1] == 2:                     # 줄바꿈
                lines.append((message["id"], current))
                current = []
        lines.append((message["id"], current))

    width = MARGIN * 2 + 46 + 40 * CELL
    height = MARGIN * 2 + LINE_HEIGHT * len(lines)
    image = Image.new("RGB", (width * SCALE, height * SCALE), BACKGROUND)
    pixels = image.load()
    draw = ImageDraw.Draw(image)

    for row, (message_id, indices) in enumerate(lines):
        top = MARGIN + row * LINE_HEIGHT
        draw.text((MARGIN * SCALE, (top + 2) * SCALE), f"{message_id:>3}",
                  fill=(120, 130, 150))
        pen = MARGIN + 46
        for index in indices:
            font = bank1 if index & 0x400 else bank0
            if font is None:
                pen += CELL
                continue
            cells = font.glyph(index & 0x3FF if index & 0x400 else index)
            for y in range(CELL):
                for x in range(CELL):
                    colour = SHADE[cells[y][x]]
                    if colour is None:
                        continue
                    for sy in range(SCALE):
                        for sx in range(SCALE):
                            px, py = (pen + x) * SCALE + sx, (top + y) * SCALE + sy
                            if 0 <= px < width * SCALE and 0 <= py < height * SCALE:
                                pixels[px, py] = colour
            pen += CELL
            if pen > width - CELL:
                break

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def describe_token(token: tuple) -> str:
    if token[0] == "g":
        return f"g:{token[1]}"
    return f"ctrl:{token[1]:02X}" + (f",{token[2]:02X}" if token[2] is not None else "")


def write_worksheet(messages: list[dict], path: Path) -> None:
    """번역 입력용 CSV. 원본 텍스트는 렌더 이미지로 보고 `ko` 칸을 채운다.

    `byte_budget` 은 종료 바이트를 뺀 원본 길이다. IMG TOC 가 절대 LBA 를
    쓰므로 크기를 늘리면 뒤 파일이 전부 밀린다. 번역문은 이 예산 안에
    들어가야 in-place 교체가 가능하다.

    `control_codes` 는 순서를 지켜 그대로 남겨야 하는 제어 코드다. 줄바꿈
    `02` 를 지우면 창 밖으로 흘러넘치고, 파라미터를 받는 코드를 빠뜨리면
    이름·수치 삽입이 깨진다.
    """
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "byte_budget", "glyph_count", "lines",
                         "control_codes", "ko"])
        for message in messages:
            tokens = message["tokens_raw"]
            controls = [describe_token(t) for t in tokens if t[0] == "ctrl"]
            glyphs = sum(1 for t in tokens if t[0] == "g")
            lines = 1 + sum(1 for t in tokens if t[0] == "ctrl" and t[1] == 2)
            writer.writerow([message["id"], message["bytes"], glyphs, lines,
                             " ".join(controls), ""])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("field", type=int, help="필드 목록의 DAT 인덱스")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--render", type=Path)
    parser.add_argument("--worksheet", type=Path, help="번역 입력용 CSV")
    args = parser.parse_args()

    entries = field_list()
    lba, size = entries[args.field]
    dat = load_entry(args.field)
    msd = msd_section(dat)
    offsets = message_offsets(msd)

    messages = []
    for index, offset in enumerate(offsets):
        end = msd.find(b"\x00", offset)
        tokens = tokenize(msd, offset)
        messages.append({
            "id": index,
            "offset": f"0x{offset:04X}",
            "bytes": end - offset,
            "hex": msd[offset:end].hex(" "),
            "tokens": [describe_token(t) for t in tokens],
            "tokens_raw": tokens,
        })

    glyphs = sum(1 for m in messages for t in m["tokens_raw"] if t[0] == "g")
    bank1_used = sorted({t[1] & 0x3FF for m in messages
                         for t in m["tokens_raw"] if t[0] == "g" and t[1] & 0x400})
    print(f"필드 #{args.field}  LBA {lba}  압축 {size:,}B  해제 {len(dat):,}B")
    print(f"MSD {len(msd):,}B   메시지 {len(messages)}개   글리프 {glyphs:,}개")
    if bank1_used:
        print(f"뱅크1 글리프 {len(bank1_used)}종 (인덱스 {min(bank1_used)}..{max(bank1_used)})"
              " — 이 필드 MIM 의 TDW 가 공급한다")

    if args.render:
        mim = load_entry(args.field - 1)          # 3종 세트에서 MIM 은 DAT 바로 앞
        bank1 = field_font(mim)
        if bank1 is None:
            print("경고: MIM 에서 TDW 를 찾지 못했다. 뱅크1 글리프는 비워 둔다.")
        render(messages, system_font(), bank1, args.render)
        print(f"{args.render}")

    if args.worksheet:
        write_worksheet(messages, args.worksheet)
        print(f"{args.worksheet}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        for message in messages:
            message.pop("tokens_raw")
        args.json.write_text(json.dumps(
            {"field": args.field, "lba": lba, "count": len(messages),
             "messages": messages}, indent=2, ensure_ascii=False))
        print(f"{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
