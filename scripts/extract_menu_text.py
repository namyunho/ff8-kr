#!/usr/bin/env python3
"""메뉴 텍스트를 `mngrp.bin` 에서 뽑는다. 빈 슬롯을 걸러낸다.

## 구조

`mngrp.bin`(IMG TOC #22, LBA 98,035)의 서브엔트리 30개 중 텍스트는 둘뿐이다.
서브엔트리 디렉터리는 아카이브가 아니라 **EXE 안**(`0x800543F8`)에 있고,
`(u32 오프셋+플래그, u32 크기)` 꼴에 섹터는 `>> 11` 이다.

    [0] LBA 98,035   2,048B   2단 u16 표
    [1] LBA 98,036  10,240B   2단 u16 표
    [2]             57,344B   Shift-JIS (초코보 월드)
    [7]              4,096B   16바이트 고정 레코드 77개
    나머지 25개                TIM 이미지

2단 표는 이렇다.

    u16 그룹 수
    u16 그룹 오프셋 x 그룹 수
      그룹마다: u16 항목 수
                u16 상대 오프셋 x 항목 수
                문자열 (0x00 종료)

## 헤더는 바이트 수가 아니라 항목 수다

이 값을 오프셋 배열의 **바이트 수**로 읽으면 절반만 나온다. **항목 수**가 맞고,
근거는 자기 일관성이다 — 첫 오프셋이 정확히 `2 + 항목수 x 2` 를 가리킨다.
배열 바로 뒤에서 문자열이 시작하기 때문이다.

    그룹 7   항목 70   첫 오프셋 142 = 2 + 70 x 2
    그룹 1   항목 163  첫 오프셋 328 = 2 + 163 x 2

**디코드 성공률로는 못 가른다.** FF8 인코딩은 `0x20`~`0xFF` 가 전부 글리프라
아무 바이트나 글자로 읽힌다. 일본어 비율로도 안 된다 — 한 글자짜리 더미도
100% 일본어다. 구조가 스스로와 맞는지를 봐야 한다.

## 빈 슬롯

여러 항목이 **같은 자리를 가리킨다.** 안 쓰는 슬롯을 하나의 더미 문자열로
몰아 둔 것이다(`め` 85회, `ニ` 63회 …). 번역할 대상이 아니므로 걸러낸다.

    python3 scripts/extract_menu_text.py
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402

BLOCKS = ((0, 98_035, 2_048), (1, 98_036, 10_240))
DUPLICATE_IS_EMPTY = 5      # 같은 자리를 이만큼 가리키면 빈 슬롯으로 본다
TOKEN = re.compile(r"\{[^}]*\}")


def groups(blob: bytes) -> list[tuple[int, int]]:
    count = int.from_bytes(blob[:2], "little")
    out = []
    for index in range(count):
        off = int.from_bytes(blob[2 + index * 2:4 + index * 2], "little")
        if off and off < len(blob):
            out.append((index, off))
    return out


def entries(blob: bytes, off: int) -> tuple[list[int], str | None]:
    """항목 오프셋 목록. 자기 일관성이 깨지면 이유를 함께 돌려준다."""
    total = int.from_bytes(blob[off:off + 2], "little")
    if not 0 < total <= 400:
        return [], f"항목 수 {total} 가 범위 밖"
    first = int.from_bytes(blob[off + 2:off + 4], "little")
    if first != 2 + total * 2:
        return [], f"첫 오프셋 {first} != {2 + total * 2}"
    return [int.from_bytes(blob[off + 2 + i * 2:off + 4 + i * 2], "little")
            for i in range(total)], None


def extract(glyphs: GT.GlyphMap) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    stats = collections.Counter()
    skipped: list[str] = []
    for sub, lba, size in BLOCKS:
        blob = PD.read_user(FT.BIN_PATH, lba, size)
        for group, off in groups(blob):
            rels, why = entries(blob, off)
            if why:
                stats["형식이 다른 그룹"] += 1
                skipped.append(f"서브{sub} 그룹{group}: {why}")
                continue
            seen = collections.Counter(rels)
            empty = {r for r, n in seen.items() if n >= DUPLICATE_IS_EMPTY}
            for index, rel in enumerate(rels):
                stats["항목"] += 1
                if rel in empty:
                    stats["빈 슬롯"] += 1
                    continue
                start = off + rel
                end = blob.find(b"\x00", start)
                if end < 0 or not 0 < end - start <= 400:
                    stats["못 읽음"] += 1
                    continue
                raw = blob[start:end]
                try:
                    text = GT.decode(raw, glyphs)
                except (ValueError, IndexError):
                    # 매개변수를 못 받고 잘린 제어 코드가 끝에 오면 IndexError 다.
                    # 진짜 메시지가 아니라 표의 빈 자리를 읽은 것이다.
                    stats["디코드 실패"] += 1
                    continue
                rows.append({"sub": sub, "group": group, "id": index,
                             "ja": text, "raw_hex": raw.hex(), "ko": ""})
                stats["실제"] += 1
    return rows, {"stats": stats, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path,
                        default=Path("work/text/menu-messages.json"))
    parser.add_argument("--keep-ko", action="store_true",
                        help="기존 파일의 번역을 같은 원문에 옮겨 붙인다")
    args = parser.parse_args()

    glyphs = GT.GlyphMap.load()
    rows, extra = extract(glyphs)

    if args.keep_ko and args.output.exists():
        old = json.loads(args.output.read_text(encoding="utf-8"))
        known = {r["ja"]: r["ko"] for r in old if r.get("ko", "").strip()}
        moved = 0
        for row in rows:
            if row["ja"] in known:
                row["ko"] = known[row["ja"]]
                moved += 1
        print(f"기존 번역 {moved}건을 옮겨 붙였다")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    plain = [TOKEN.sub("", r["ja"]) for r in rows]
    chars = collections.Counter(c for t in plain for c in t)
    for key, value in extra["stats"].most_common():
        print(f"  {key:<16} {value:>6,}")
    for line in extra["skipped"]:
        print(f"    건너뜀 — {line}")
    print(f"\n실제 메시지 {len(rows):,}건 (고유 원문 {len(set(plain)):,}종)")
    print(f"  그려지는 글자 {sum(chars.values()):,}자, 서로 다른 {len(chars)}종")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
