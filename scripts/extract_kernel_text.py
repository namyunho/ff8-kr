#!/usr/bin/env python3
"""kernel.bin(IMG TOC #129)의 문자열 풀을 뽑는다.

## 구조

kernel.bin(LBA 831, 35,976B)은 [count=56][u32 offset]×56 헤더로 시작해
56개 구조체 섹션이 이어지고(마법·아이템·GF 등의 수치 데이터, 필드별
스트라이드는 섹션마다 다르다 — 아직 안 풀었다), 그 뒤로 **필드/메뉴 텍스트와
같은 FF8 글리프 인코딩을 쓰는 공용 문자열 풀**이 파일 끝까지 이어진다.

    오프셋 0..19,975     구조체 섹션 56개 (수치 데이터, 미해독)
    오프셋 19,976..EOF   문자열 풀 — NUL 종료, 이름+설명이 번갈아 나온다

문자열 풀 시작은 `たたかう`(전투 커맨드 "싸운다")의 첫 바이트로 확인했다 —
바로 앞은 제어 코드로 어지럽게 풀리는 구조체 꼬리라 경계가 뚜렷하다.

## 왜 이렇게 찾았는가

제어 코드 `0x0C`/`0x0D`(주문/기술 ID → 이름 삽입, `docs/external-sources.md`
E절)를 추적하다 RAM 위치가 kernel.bin 파일 오프셋과 일치하는 것을 발견했다.
정확한 필드 오프셋은 못 찾았지만, 사용자가 제공한 실제 아이템/마법 이름
(`ポーション` `ファイア` `ケアルラ` `たたかう`)을 FF8 인코딩으로 바꿔
kernel.bin 안에서 직접 찾으니 4개 중 4개가 그대로 걸렸다 — **실제로 찾아본
것이 구조체 필드를 추측하는 것보다 빨랐다.**

## 담고 있는 것

이 풀 하나에 전투 커맨드, 마법 전체(이름+설명), G.F. 소환 이름, 적 기술
이름, 주인공 이름(ゼル 등 8명), 아이템 전체(이름+설명), 스테이터스·속성
이름, 전투 결과 메시지(`AP入手`, `を手にいれた!` 등)가 전부 들어 있다.
`docs/roadmap.md` M4b 의 4·7·8 번 항목이 이 파일 하나로 상당 부분 열린다.

## 쓰는 법

    python3 scripts/extract_kernel_text.py
    python3 scripts/extract_kernel_text.py --output work/text/kernel-text.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                      # noqa: E402

KERNEL_TOC_INDEX = 129
POOL_START = 19976
DEFAULT_SOURCE = Path("work/analysis/toc129/img_129_lba831.bin")
DEFAULT_OUTPUT = Path("work/text/kernel-text.json")


def extract_pool(data: bytes, glyphs: GT.GlyphMap, start: int = POOL_START) -> list[dict]:
    """`start` 부터 파일 끝까지 NUL 종료 문자열을 순서대로 뽑는다."""
    entries = []
    cur = start
    buf = bytearray()
    for i in range(start, len(data)):
        byte = data[i]
        if byte == 0:
            if buf:
                raw = bytes(buf)
                entries.append({
                    "offset": cur,
                    "raw_hex": raw.hex(),
                    "ja": GT.decode(raw, glyphs),
                })
            buf = bytearray()
            cur = i + 1
        else:
            buf.append(byte)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="추출된 kernel.bin 경로 (없으면 psx_disc.py extract 먼저 실행)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start", type=int, default=POOL_START,
                        help="문자열 풀 시작 오프셋 (기본 19976)")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"{args.source} 가 없다. 먼저 실행:\n"
              f"  python3 scripts/psx_disc.py extract --index {KERNEL_TOC_INDEX} "
              f"--destination {args.source.parent}", file=sys.stderr)
        return 1

    data = args.source.read_bytes()
    glyphs = GT.GlyphMap.load()
    entries = extract_pool(data, glyphs, args.start)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(entries, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    print(f"{len(entries):,}개 문자열 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
