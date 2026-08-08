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

## 지명 19개 — 정본은 `data/location-names.json`

한때 이 파일 안에 리스트로 박혀 있었다. 코드와 자료 양쪽에 두면 어느 쪽이
새것인지 가릴 수 없어(불변식 24·26) 자료 하나로 옮겼다. **리스트 위치가 곧
표 인덱스**라 순서를 바꾸면 지명이 뒤바뀐다 — `load_names` 가 그것을 본다.

축약문 우선순위는 kernel 텍스트와 같다(`scripts/text_rows.py`) — `ko_short`
가 있으면 그것이, 없으면 `ko_draft` 가 나간다.

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
import text_rows as TR                       # noqa: E402

TOC_INDEX = 132
LAYOUT = Path("data/glyph-layout.json")
NAMES_JSON = Path("data/location-names.json")


def load_names(path: Path = NAMES_JSON) -> list[str]:
    """지명 19개의 **정본은 자료 파일 하나**다. 코드에 사본을 두지 않는다.

    한때 이 목록이 이 파일 안에 박혀 있었다. 코드와 자료 양쪽에 두면
    어느 쪽이 새것인지 가릴 수 없다(불변식 24·26). 파일이 없으면 표를
    만들지 않고 멈춘다 — 조용히 옛 목록으로 되돌아가는 쪽이 더 나쁘다.

    리스트 위치가 곧 표 인덱스라 `index` 가 0..n-1 로 이어지는지 본다.
    """
    import json

    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document["names"]
    for position, entry in enumerate(entries):
        if entry.get("index") != position:
            raise ValueError(f"지명 순서가 어긋났다: {position}번째의 index "
                             f"{entry.get('index')}")
    return [TR.effective(entry) for entry in entries]


def build_table(glyphs: GT.GlyphMap, bank1, names: list[str]) -> bytes:
    encoded = [bytes(GT.encode(name, glyphs, bank1)) + b"\x00" for name in names]
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
    parser.add_argument("--names", type=Path, default=NAMES_JSON)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import json
    if "base" in json.loads(args.layout.read_text(encoding="utf-8")):
        print("이 표는 base 방식 배치를 지원하지 않는다", file=sys.stderr)
        return 1
    glyphs, bank1, _ = PD.korean_map(args.layout)

    try:
        names = load_names(args.names)
    except (OSError, KeyError, ValueError) as error:
        print(f"지명 정본을 못 읽었다 ({args.names}): {error}", file=sys.stderr)
        return 1

    table = build_table(glyphs, bank1, names)

    disc = PSX.Disc(PD.PATCH_BIN)
    toc = {e["index"]: e for e in PSX.read_toc(disc)}
    entry = toc[TOC_INDEX]
    lba, size = entry["lba"], entry["size"]
    print(f"TOC#{TOC_INDEX}  LBA {lba}  원본 {size}B  새 표 {len(table)}B")
    print(f"  정본 {args.names}")
    for i, name in enumerate(names):
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
