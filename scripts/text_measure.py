#!/usr/bin/env python3
"""번역문이 자리에 들어가는가를 **인코더로 직접 재는** 판정기.

세지 않는다. 음절 수·글자 수 같은 대리 지표는 무엇을 안 보고 있는지 못
알려 준다(불변식 14). 삽입기가 쓰는 `glyph_text.encode` 를 조각마다 그대로
불러 바이트를 얻고, 조각의 합이 전체 인코딩 길이와 같은지 `--check` 가
검산한다. 판정기가 인코더에서 갈라지면 그 자리에서 드러난다.

## 글자마다 값이 다르다

배치(`data/glyph-layout.json`)의 `single_byte_limit` 이 224다.

    인덱스 <224        1바이트   싼 음절
    224~755           2바이트   비싼 음절 — 줄일 여지가 여기 있다
    슬롯 756~ (뱅크1)  2바이트   **화면에서 깨진다.** 지금은 피해 쓰는 수밖에 없다
    배치에 없음        인코딩 실패

`헤이스트` 가 5바이트인 것은 길어서가 아니라 `헤`(580)가 2바이트 구간에 있기
때문이다. kernel 초과 462건 중 337건은 2바이트 음절 개수가 초과분보다 많아
**문장을 줄이지 않고 동의어만 바꿔도** 이론상 들어간다.

## 뱅크 경계가 둘이라 헷갈린다

인코딩이 갈리는 자리는 **슬롯 756**(텍스처 378칸 x 글리프 2개)이고, 게임
글리프 인덱스가 갈리는 자리는 **882** 다. `patch_disc.korean_map` 은 756에서
잘라 뒤쪽을 `Bank1Map` 에 담는다. 그래서 뱅크1 음절은 `glyphs.index` 에 없고
`bank1.index` 에만 있다 — **한쪽만 보면 없는 것으로 나온다.**

    python3 scripts/text_measure.py --check
    python3 scripts/text_measure.py "전투 불능을 회복합니다" --slot 12
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                      # noqa: E402
import patch_disc as PD                      # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANON = PROJECT_ROOT / "data" / "glyph-layout.json"

# 조각의 종류. 편집기가 이 값으로 색을 고른다.
ONE = "one"          # 1바이트 음절
TWO = "two"          # 2바이트 음절
BANK1 = "bank1"      # 뱅크1 — 인코딩은 되지만 화면에서 깨진다
TOKEN = "token"      # 제어 코드·글리프 번호. 지워지면 삽입이 어긋난다
MISSING = "missing"  # 배치에 없다 — 인코딩 실패


@dataclass(frozen=True)
class Maps:
    """배치 하나에서 나온 대응표 묶음. 편집기는 이것 하나만 들고 다닌다."""

    glyphs: GT.GlyphMap
    bank1: GT.Bank1Map | None
    single_byte_limit: int
    version: int

    def zone(self, char: str) -> str:
        """글자 하나가 어느 구간에 있는가."""
        index = self.glyphs.index.get(char)
        if index is not None:
            return ONE if index < self.single_byte_limit else TWO
        if self.bank1 is not None and char in self.bank1.index:
            return BANK1
        return MISSING

    def game_index(self, char: str) -> int | None:
        """화면 진단용 게임 글리프 인덱스. 뱅크1 은 882 부터다."""
        index = self.glyphs.index.get(char)
        if index is not None:
            return index
        if self.bank1 is not None and char in self.bank1.index:
            return 882 + self.bank1.index[char]
        return None


def load_maps(layout: Path = CANON) -> Maps:
    document = json.loads(layout.read_text(encoding="utf-8"))
    glyphs, bank1, _ = PD.korean_map(layout)
    return Maps(glyphs, bank1,
                int(document.get("single_byte_limit", 224)),
                int(document.get("version", 0)))


@dataclass(frozen=True)
class Span:
    """편집기가 색칠할 한 조각. 글자 하나이거나 토큰 하나다."""

    start: int
    end: int
    text: str
    kind: str
    nbytes: int


def _width(piece: str, maps: Maps) -> int:
    """조각 하나의 바이트. **세지 않고 인코더를 부른다.**"""
    try:
        return len(GT.encode(piece, maps.glyphs, maps.bank1))
    except ValueError:
        return 0


def spans(text: str, maps: Maps) -> list[Span]:
    """`glyph_text.encode` 와 **같은 순서로** 문자열을 조각낸다.

    토큰을 먼저 물어보는 것까지 인코더와 같다. 순서가 다르면 `{02}` 를 글자
    넷으로 세는 식으로 조용히 어긋난다.
    """
    out: list[Span] = []
    pos = 0
    while pos < len(text):
        match = GT.TOKEN.match(text, pos)
        end = match.end() if match else pos + 1
        piece = text[pos:end]
        kind = TOKEN if match else maps.zone(piece)
        out.append(Span(pos, end, piece, kind, _width(piece, maps)))
        pos = end
    return out


def encoded_length(text: str, maps: Maps) -> int | None:
    """전체를 한 번에 인코딩한 길이. 실패하면 `None`."""
    try:
        return len(GT.encode(text, maps.glyphs, maps.bank1))
    except ValueError:
        return None


def tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in GT.TOKEN.finditer(text))


@dataclass(frozen=True)
class Measurement:
    """한 건의 판정 결과. 편집기와 `--check` 가 같은 것을 본다."""

    slot_bytes: int
    used_bytes: int | None            # None = 인코딩 실패
    spans: tuple[Span, ...]
    lost_tokens: tuple[str, ...]      # 원문에 있었는데 번역에서 사라진 것
    added_tokens: tuple[str, ...]     # 원문에 없던 것

    @property
    def overflow(self) -> int:
        if self.used_bytes is None:
            return 0
        return max(0, self.used_bytes - self.slot_bytes)

    @property
    def headroom(self) -> int:
        if self.used_bytes is None:
            return 0
        return self.slot_bytes - self.used_bytes

    def chars(self, kind: str) -> tuple[str, ...]:
        return tuple(s.text for s in self.spans if s.kind == kind)

    @property
    def savable(self) -> int:
        """2바이트 음절을 전부 1바이트로 바꾸면 줄어들 바이트 — 이론상 상한."""
        return len(self.chars(TWO))

    @property
    def fits(self) -> bool:
        return (self.used_bytes is not None and not self.overflow
                and not self.lost_tokens and not self.added_tokens
                and not self.chars(MISSING))

    @property
    def clean(self) -> bool:
        """화면까지 온전한가 — 들어가기만 하는 것과 다르다."""
        return self.fits and not self.chars(BANK1)


def measure(text: str, slot_bytes: int, origin: str, maps: Maps) -> Measurement:
    """`text` 가 `slot_bytes` 자리에 들어가는가. `origin` 은 토큰 대조용 원문."""
    pieces = spans(text, maps)
    want = collections.Counter(tokens(origin))
    have = collections.Counter(tokens(text))
    lost = tuple(sorted((want - have).elements()))
    added = tuple(sorted((have - want).elements()))
    return Measurement(slot_bytes, encoded_length(text, maps), tuple(pieces),
                       lost, added)


def highlight(text: str, maps: Maps) -> str:
    """터미널용 표시. 2바이트는 `[]`, 뱅크1 은 `<>`, 없는 글자는 `??`."""
    marks = {TWO: "[{}]", BANK1: "<{}>", MISSING: "?{}?"}
    return "".join(marks.get(s.kind, "{}").format(s.text) for s in spans(text, maps))


# ---------------------------------------------------------------------------
# 자기검증 — 판정기가 인코더에서 갈라지지 않았는가
# ---------------------------------------------------------------------------

def check(maps: Maps, sources: list[Path]) -> int:
    """조각의 합 == 전체 인코딩 길이. 어긋나면 판정기를 못 믿는다."""
    print(f"배치 v{maps.version}  1바이트 상한 {maps.single_byte_limit}  "
          f"뱅크0 {len(maps.glyphs.index):,}자  "
          f"뱅크1 {len(maps.bank1.index) if maps.bank1 else 0:,}자\n")

    total = drift = broken = 0
    for path in sources:
        if not path.exists():
            print(f"  건너뜀 (없음) {path}")
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        seen = bad = 0
        for row in rows:
            text = row.get("ko") or ""
            if not text:
                continue
            seen += 1
            whole = encoded_length(text, maps)
            piecewise = sum(s.nbytes for s in spans(text, maps))
            if whole is None:
                broken += 1
                continue
            if whole != piecewise:
                bad += 1
                if bad <= 3:
                    print(f"    어긋남 {path.name}: 전체 {whole}B "
                          f"!= 조각합 {piecewise}B  {text[:30]!r}")
        total += seen
        drift += bad
        print(f"  {path.name}  {seen:,}건 검사, 조각합 불일치 {bad}건")

    print(f"\n검사 {total:,}건 · 불일치 {drift}건 · 인코딩 불가 {broken}건")
    if drift:
        print("**조각내기가 인코더와 갈라졌다.** 색칠과 바이트 판정을 못 믿는다.")
        return 1
    print("조각의 합이 전체와 같다 — 판정기가 인코더를 그대로 따른다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", nargs="?", help="한 줄을 재 본다")
    parser.add_argument("--slot", type=int, default=0, help="안전 슬롯 바이트")
    parser.add_argument("--layout", type=Path, default=CANON)
    parser.add_argument("--check", action="store_true",
                        help="조각내기가 인코더와 일치하는지 전수 검사한다")
    args = parser.parse_args()

    maps = load_maps(args.layout)

    if args.check:
        return check(maps, [
            PROJECT_ROOT / "work" / "text" / "kernel-text-ko.json",
            PROJECT_ROOT / "work" / "text" / "menu-messages.json",
        ])

    if not args.text:
        parser.print_help()
        return 0

    result = measure(args.text, args.slot, args.text, maps)
    print(highlight(args.text, maps))
    print(f"  바이트 {result.used_bytes}/{args.slot}"
          + (f"  **초과 {result.overflow}**" if result.overflow else ""))
    print(f"  2바이트 음절 {result.savable}개"
          f"  뱅크1 {len(result.chars(BANK1))}개"
          f"  배치에 없음 {len(result.chars(MISSING))}개")
    for char in dict.fromkeys(result.chars(TWO)):
        print(f"    {char}  인덱스 {maps.game_index(char)}  2바이트")
    for char in dict.fromkeys(result.chars(BANK1)):
        print(f"    {char}  인덱스 {maps.game_index(char)}  **뱅크1 — 화면에서 깨진다**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
