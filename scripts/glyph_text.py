#!/usr/bin/env python3
"""메시지 바이트 ↔ 사람이 읽는 문자열.

대응표(`data/glyph-map-bank0.json`)로 MSD 메시지를 일본어로 펴고 다시 바이트로
되돌린다.

**제어 코드는 하나도 버리지 않는다.** 대사 사이의 코드를 지우면 창 밖으로
흘러넘치거나 이름·수치 삽입이 깨진다. 번역할 때도 코드를 보면서 자리를 잡아야
하므로 눈에 보이는 표기로 남긴다. 줄바꿈(`02`)도 개행 문자로 펴지 않는다 —
표 계열 편집기를 거치면 조용히 사라지는 쪽이 개행이다.

표기

    {01}        파라미터 없는 제어 코드
    {03:30}     파라미터 있는 제어 코드. 코드와 값 모두 16진 두 자리
    {b1:123}    필드 전용 폰트(뱅크1) 글리프. 뱅크0 대응표로는 문자를 모른다
    {g:801}     같은 문자가 두 인덱스에 배정됐을 때 표준이 아닌 쪽

중괄호는 이 폰트에 없으므로 본문과 겹치지 않는다.

`decode` 한 문자열을 `encode` 하면 **원본 바이트가 그대로 나온다.** 이 성질이
번역문 삽입의 전제다. 문자열만 바꾸고 나머지는 건드리지 않는다.

    python3 scripts/glyph_text.py --check work/text/opening-scenes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = PROJECT_ROOT / "data" / "glyph-map-bank0.json"

BANK1 = 0x400
# 제어 코드는 언제나 0x20 미만이다. 그 자리를 `[01]x` 로 좁혀야 `{b1:10}` 과
# 겹치지 않는다. "b1" 도 유효한 16진이라 열어 두면 제어 코드로 읽힌다.
TOKEN = re.compile(r"\{(?:b1:(\d+)|g:(\d+)|"
                   r"([01][0-9A-Fa-f])(?::([0-9A-Fa-f]{2}))?)\}")

# 두 바이트 인코딩 구간. (선두 바이트, 인덱스 하한, 뺄 값)
LEADS = ((0x19, 224, 192), (0x1A, 448, 416), (0x1B, 672, 640))


class GlyphMap:
    """인덱스 ↔ 문자. 같은 문자가 둘 이상이면 작은 인덱스를 표준으로 삼는다."""

    def __init__(self, entries: dict[int, str]):
        self.char = entries
        self.index: dict[str, int] = {}
        for index in sorted(entries):
            self.index.setdefault(entries[index], index)

    @classmethod
    def load(cls, path: Path = MAP_PATH) -> "GlyphMap":
        document = json.loads(path.read_text(encoding="utf-8"))
        return cls({int(k): v["char"] for k, v in document["entries"].items()})

    def is_canonical(self, index: int) -> bool:
        char = self.char.get(index)
        return char is not None and self.index[char] == index


def decode(data: bytes, glyphs: GlyphMap) -> str:
    out: list[str] = []
    for token in FT.tokenize(data, 0):
        if token[0] == "ctrl":
            code, param = token[1], token[2]
            out.append(f"{{{code:02X}}}" if param is None
                       else f"{{{code:02X}:{param:02X}}}")
            continue
        index = token[1]
        if index & BANK1:
            out.append(f"{{b1:{index & ~BANK1}}}")
        elif glyphs.is_canonical(index):
            out.append(glyphs.char[index])
        elif index in glyphs.char:
            # 같은 문자를 가진 인덱스가 앞에 또 있다. 되돌릴 수 있게 번호로 남긴다.
            out.append(f"{{g:{index}}}")
        else:
            out.append(f"{{g:{index}}}")
    return "".join(out)


def glyph_bytes(index: int, bank1: bool = False) -> bytes:
    """인덱스 하나를 원본이 쓰는 형태로 되돌린다.

    0..223 은 뱅크0 에서 1바이트다. 원본이 선두 `0x18` 을 쓰지 않는다.
    """
    if not bank1 and index < 224:
        return bytes((index + 32,))
    base = 0x1C if bank1 else 0x18
    if index < 224:
        return bytes((base, index + 32))
    for offset, (lead, low, drop) in enumerate(LEADS):
        if low <= index < low + 224:
            return bytes((lead + (4 if bank1 else 0), index - drop))
    raise ValueError(f"범위 밖 글리프 인덱스: {index}")


def encode(text: str, glyphs: GlyphMap) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if match:
            bank1, raw_index, code, param = match.groups()
            if code is not None:
                out.append(int(code, 16))
                if param is not None:
                    out.append(int(param, 16))
            elif bank1 is not None:
                out += glyph_bytes(int(bank1), bank1=True)
            else:
                out += glyph_bytes(int(raw_index))
            pos = match.end()
            continue
        char = text[pos]
        if char not in glyphs.index:
            raise ValueError(f"대응표에 없는 문자: {char!r} (위치 {pos})")
        out += glyph_bytes(glyphs.index[char])
        pos += 1
    return bytes(out)


def check(path: Path, glyphs: GlyphMap) -> int:
    """JSON 안 모든 메시지가 바이트까지 되돌아오는지 본다."""
    document = json.loads(path.read_text(encoding="utf-8"))
    total = broken = bank1 = 0
    for field in document["fields"]:
        for entry in field["entries"]:
            raw = entry.get("raw_hex", "")
            if not raw:
                continue
            data = bytes.fromhex(raw)
            total += 1
            text = decode(data, glyphs)
            bank1 += text.count("{b1:")
            if encode(text, glyphs) != data:
                broken += 1
                print(f"  왕복 실패: 필드 {field['field']} 메시지 {entry['id']}")
    print(f"메시지 {total}건 중 왕복 실패 {broken}건")
    print(f"  필드 전용 폰트(뱅크1) 글리프 {bank1}개 — 뱅크0 표의 대상이 아니다")
    return 1 if broken else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", type=Path, metavar="JSON",
                        help="메시지 JSON 의 왕복을 검사한다")
    parser.add_argument("--hex", help="바이트 열 하나를 풀어 본다")
    args = parser.parse_args()

    glyphs = GlyphMap.load()
    if args.hex:
        data = bytes.fromhex(args.hex)
        text = decode(data, glyphs)
        print(text)
        print("왕복:", "일치" if encode(text, glyphs) == data else "불일치")
        return 0
    if args.check:
        return check(args.check, glyphs)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
