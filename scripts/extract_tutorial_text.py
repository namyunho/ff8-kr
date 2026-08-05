#!/usr/bin/env python3
"""`mngrp.bin` 안의 튜토리얼 문자열 풀을 뽑는다.

## 구조

`mngrp.bin`(IMG TOC #22, LBA 98,035)의 서브 0/1(메뉴 문구)·2(Shift-JIS
초코보 월드) 뒤, mngrp.bin 자체 상대 오프셋 **1,732,608**부터 자체
오프셋 표(`[u16 오프셋]×N`)로 시작하는 문자열 풀이 있다. 필드/메뉴와 같은
FF8 글리프 인코딩이고, 약 **1,913,374**까지 이어진다(그 뒤는 TIM 이미지로
보이는 이진 데이터).

찾은 경위는 `docs/font-analysis.md`(2026-08-06) — 사용자가 화면에서 읽어준
깨진 한글 대사를 한글 글리프표로 역인코딩해 원문(`ガンブレード`)을 복원하고,
그 문자열로 디스크를 검색하다 발견했다.

## 담고 있는 것 (실측 확인)

전투 조작 해설 8쪽, 카드게임 해설 13쪽, 월간무기·격투왕·펫통신·오컬트팬
잡지, 아빌리티 세팅 설명, 초코보 월드 관련 텍스트.

## 쓰는 법

    python3 scripts/extract_tutorial_text.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                       # noqa: E402

MNGRP_LBA = 98035
MNGRP_SIZE = 2168832
POOL_START = 1732608
POOL_END = 1912000  # 실측 끝(1,913,374) 직전 — 그 뒤는 이진 노이즈
OUTPUT = Path("work/text/tutorial-text.json")


def read_mngrp() -> bytes:
    disc = Path("roms/Final Fantasy VIII (Japan, Asia) (Disc 1).bin")
    data = disc.read_bytes()
    out = bytearray()
    lba = MNGRP_LBA
    while len(out) < MNGRP_SIZE:
        sector = data[lba * 2352:(lba + 1) * 2352]
        out += sector[24:24 + 2048]
        lba += 1
    return bytes(out[:MNGRP_SIZE])


def looks_like_text(text: str) -> bool:
    kana_kanji = sum(1 for c in text
                      if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    return kana_kanji >= 2


def main() -> int:
    mngrp = read_mngrp()
    glyphs = GT.GlyphMap.load()

    entries = []
    cur = POOL_START
    buf = bytearray()
    for i in range(POOL_START, min(POOL_END, len(mngrp))):
        byte = mngrp[i]
        if byte == 0:
            if buf:
                raw = bytes(buf)
                try:
                    text = GT.decode(raw, glyphs)
                except Exception:
                    text = None
                if text and looks_like_text(text):
                    entries.append({"offset": cur, "raw_hex": raw.hex(), "ja": text})
            buf = bytearray()
            cur = i + 1
        else:
            buf.append(byte)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(entries):,}개 문자열 -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
