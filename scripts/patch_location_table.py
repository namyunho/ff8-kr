#!/usr/bin/env python3
"""IMG TOC#132 지명 표를 한글로 다시 써넣는다.

## 왜 필요한가

제어 코드 `{0E:XX}`(`sub_8002F6C4`)가 문자열을 끼워 넣는 표는 필드 대사와
별도의 자원이다 — `patch_disc.py --apply` 는 필드 MSD 만 건드리므로 이 표는
**원본 일본어 인덱스 그대로** 남는다. 한글 폰트 패치가 같은 인덱스 자리를
한글로 바꿔 버렸으므로, 이 표를 그대로 두면 `{0E:XX}` 가 나오는 자리마다
엉뚱한 한글이 뜬다(예: `{0E:23}` = 인덱스 3 = `ドール` → 지명이 깨져 보임).

## 구조 (TOC#132, LBA 883, 176바이트, 1섹터)

    [u16 count][u16 offset] x count   -- 오프셋은 표 시작 기준
    문자열 풀 (NUL 종료)

`sub_8002F6C4` 가 읽는 인덱스는 제어 코드 파라미터에서 32 를 뺀 값이다
(`sub_8002F73C` 의 `{0E:XX}`/`{0F:XX}` 분기, `docs/external-sources.md` 참고).

## 지명 19개 — 실측 원문과 대응

    0 ガルバディア   1 エスタ       2 バラム       3 ドール
    4 ティンバー     5 トラビア     6 セントラ
    7 フィッシャーマンズ・ホライズン
    8 学園東         9 砂漠収容所   10 トラビアガーデン
    11 ルナサイドベース  12 シュミ族の村  13 デリングシティ
    14 バラムガーデン   15 学園東駅   16 ドール駅
    17 収容所駅        18 ルナゲート

    python3 scripts/patch_location_table.py --dry-run
    python3 scripts/patch_location_table.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_disc as PD                      # noqa: E402
import glyph_text as GT                      # noqa: E402
import psx_disc as PSX                       # noqa: E402

TOC_INDEX = 132
LAYOUT = Path("work/hangul-layout-all.json")

# 실측 원문(디스크에서 직접 읽은 것) -> 초벌 한글. 0~5 는 확정 용어집
# (work/text/glossary-additions.csv)을 따랐고, 나머지는 표준 FF8 표기를
# 참고한 초벌번역이다 — 다른 번역과 마찬가지로 교정 대상이다.
NAMES = [
    "갈바디아", "에스타", "발람", "돌", "팀버", "트라비아", "센트라",
    "피셔맨즈 호라이즌", "학원동", "사막의 수용소", "트라비아 가든",
    "루나 베이스", "슈미족 마을", "델링시티", "발람 가든",
    "학원동역", "돌역", "수용소역", "루나게이트",
]


def build_table(glyphs: GT.GlyphMap, bank1) -> bytes:
    encoded = [bytes(GT.encode(name, glyphs, bank1)) + b"\x00" for name in NAMES]
    count = len(encoded)
    header_size = 2 + count * 2
    offsets = []
    pool = bytearray()
    for chunk in encoded:
        offsets.append(header_size + len(pool))
        pool += chunk
    out = bytearray()
    out += struct.pack("<H", count)
    for off in offsets:
        out += struct.pack("<H", off)
    out += pool
    return bytes(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", type=Path, default=LAYOUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import json
    if "base" in json.loads(args.layout.read_text(encoding="utf-8")):
        print("이 표는 base 방식 배치를 지원하지 않는다", file=sys.stderr)
        return 1
    glyphs, bank1, _ = PD.korean_map(args.layout)

    table = build_table(glyphs, bank1)

    disc = PSX.Disc(PD.PATCH_BIN)
    toc = {e["index"]: e for e in PSX.read_toc(disc)}
    entry = toc[TOC_INDEX]
    lba, size = entry["lba"], entry["size"]
    print(f"TOC#{TOC_INDEX}  LBA {lba}  원본 {size}B  새 표 {len(table)}B")
    for i, name in enumerate(NAMES):
        print(f"  [{i:2d}] {name}")

    if len(table) > size:
        allocated = ((size + 2047) // 2048) * 2048
        if len(table) > allocated:
            print(f"할당 섹터를 넘는다: {len(table)}B > {allocated}B", file=sys.stderr)
            return 1
        print(f"원본 크기({size}B)는 넘지만 할당 섹터({allocated}B) 안에는 들어간다"
              " — 자리는 안 옮기고 크기만 는다.")

    if args.dry_run:
        print("dry-run — 쓰지 않는다")
        return 0

    PD.write_user(PD.PATCH_BIN, lba, table)
    print("썼다.")

    readback = PD.read_user(PD.PATCH_BIN, lba, len(table))
    if readback == table:
        print("되읽기 일치")
    else:
        print("되읽기 불일치!", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
