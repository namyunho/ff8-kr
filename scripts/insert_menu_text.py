#!/usr/bin/env python3
"""번역한 메뉴 텍스트를 `mngrp.bin` 에 써넣는다.

## 형식

2단 u16 표다. 필드 대사의 MSD 와 달리 **그룹이 여러 개**이고 그룹마다 오프셋
배열이 따로 있다.

    u16 그룹 수
    u16 그룹 오프셋 x 그룹 수
      그룹마다: u16 항목 수
                u16 상대 오프셋 x 항목 수     <- 그룹 시작 기준
                문자열 (0x00 종료)

## 블록을 통째로 다시 만든다

MSD 는 크기를 지키는 쪽이 안전했지만(뒤 섹션이 밀리므로) 메뉴는 사정이 다르다.
그룹 오프셋 표가 블록 머리에 있으므로 **그룹이 커지거나 작아져도 표만 고치면
된다.** 그래서 전체를 다시 짜고 총 크기만 원본 안에 들어가면 된다.

실측하면 한국어 쪽이 훨씬 작다 — 일본어 한 글자가 2바이트인데 한글은 자주
쓰는 것이 1바이트라서다. 12개 그룹 합계가 9,676 -> 7,846바이트로 줄어든다.

## 손대지 않는 것 둘

**형식이 다른 그룹.** 첫 오프셋이 `2 + 항목수 x 2` 와 안 맞는 그룹이 넷 있다.
읽는 법을 모르므로 **원래 바이트를 그대로 옮긴다.**

**빈 슬롯의 공유.** 여러 항목이 같은 자리를 가리켜 안 쓰는 슬롯을 하나의 더미로
몰아 둔다. 같은 원본을 가리키던 것은 새 블록에서도 같은 자리를 가리키게 한다 —
안 그러면 371개의 더미가 각자 자리를 차지해 블록이 부푼다.

    python3 scripts/insert_menu_text.py --dry-run
    python3 scripts/insert_menu_text.py
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_menu_text as EM              # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402

ALIGN = 4               # 그룹 오프셋은 4의 배수여야 한다


def rebuild(blob: bytes, sub: int, ko: dict, glyphs, bank1) -> tuple[bytes, dict]:
    """블록을 다시 짠다. 원본과 같은 그룹 수·항목 수를 유지한다."""
    count = int.from_bytes(blob[:2], "little")
    starts = [int.from_bytes(blob[2 + i * 2:4 + i * 2], "little")
              for i in range(count)]
    bounds = sorted({s for s in starts if s} | {len(blob)})
    stats = {"그룹": 0, "그대로 둔 그룹": 0, "바꾼 항목": 0, "인코딩 실패": 0}

    bodies: list[bytes] = []
    for index, off in enumerate(starts):
        if not off or off >= len(blob):
            bodies.append(b"")
            continue
        stats["그룹"] += 1
        end = min([b for b in bounds if b > off] + [len(blob)])
        rels, why = EM.entries(blob, off)
        if why:
            stats["그대로 둔 그룹"] += 1
            bodies.append(blob[off:end])          # 읽는 법을 모른다 — 그대로
            continue

        header = 2 + len(rels) * 2
        pool = bytearray()
        placed: dict[bytes, int] = {}
        new_rels: list[int] = []
        for slot, rel in enumerate(rels):
            start = off + rel
            stop = blob.find(b"\x00", start)
            raw = blob[start:stop] if stop >= 0 else b""
            text = ko.get((sub, index, slot))
            if text:
                try:
                    raw = GT.encode(text, glyphs, bank1)
                    stats["바꾼 항목"] += 1
                except ValueError:
                    stats["인코딩 실패"] += 1
            key = bytes(raw)
            if key not in placed:                 # 같은 문자열은 한 번만 둔다
                placed[key] = header + len(pool)
                pool += key + b"\x00"
            new_rels.append(placed[key])

        body = bytearray(struct.pack("<H", len(rels)))
        for rel in new_rels:
            body += struct.pack("<H", rel)
        body += pool
        bodies.append(bytes(body))

    # **그룹은 4바이트 경계에 놓는다.** 원본은 16개 그룹의 오프셋이 예외 없이
    # 4의 배수다(머리도 34가 아니라 36까지 채워 둔다). 그냥 이어 붙이면 2, 1, 3
    # 처럼 제멋대로가 되는데, 그룹 데이터를 워드로 읽는 코드가 있으면 실기에서
    # **정렬 안 된 `lw` -> 주소 오류 예외**가 난다. 에뮬레이터는 봐주므로
    # 실기에서만 죽는다.
    #
    # MSD 섹션이 전부 4의 배수였던 것(R7)과 같은 규칙인데 여기서는 놓쳤다.
    head = bytearray(struct.pack("<H", count))
    cursor = 2 + count * 2
    cursor += -cursor % ALIGN
    offsets = []
    for body in bodies:
        if not body:
            offsets.append(0)
            continue
        offsets.append(cursor)
        cursor += len(body)
        cursor += -cursor % ALIGN
    for value in offsets:
        head += struct.pack("<H", value)
    out = bytearray(head)
    out += b"\x00" * (-len(out) % ALIGN)
    for body in bodies:
        if not body:
            continue
        out += body
        out += b"\x00" * (-len(out) % ALIGN)
    stats["크기"] = len(out)
    return bytes(out), stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", type=Path,
                        default=Path("work/hangul-layout-all.json"))
    parser.add_argument("--messages", type=Path,
                        default=Path("work/text/menu-messages.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for path in (args.layout, args.messages):
        if not path.exists():
            print(f"없는 파일: {path}", file=sys.stderr)
            return 2
    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}", file=sys.stderr)
        return 2

    glyphs, bank1, _ = PD.korean_map(args.layout)
    rows = json.loads(args.messages.read_text(encoding="utf-8"))
    ko = {(r["sub"], r["group"], r["id"]): r["ko"]
          for r in rows if r.get("ko", "").strip()}
    print(f"번역 {len(ko)}건을 넣는다")

    failed = 0
    for sub, lba, size in EM.BLOCKS:
        blob = PD.read_user(FT.BIN_PATH, lba, size)     # **원본에서 읽는다**
        out, stats = rebuild(blob, sub, ko, glyphs, bank1)
        room = size
        fits = len(out) <= room
        print(f"\n서브{sub}  LBA {lba}  {size:,}B")
        for key, value in stats.items():
            print(f"    {key:<12} {value:>6,}")
        print(f"    {'들어간다' if fits else '**넘친다**'}  "
              f"{len(out):,} / {room:,}")
        if stats["인코딩 실패"]:
            failed += stats["인코딩 실패"]
        if not fits:
            print("    크기를 넘어 쓰지 않는다", file=sys.stderr)
            return 1
        if args.dry_run:
            continue
        padded = out + blob[len(out):] if len(out) < len(blob) else out
        PD.write_user(PD.PATCH_BIN, lba, padded[:size])
        back = PD.read_user(PD.PATCH_BIN, lba, size)
        if back[:len(out)] != out:
            print("    쓴 대로 안 읽힌다", file=sys.stderr)
            return 1
        print("    썼다. 되읽기 일치")

    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
    if failed:
        print(f"\n인코딩 실패 {failed}건 — 배치에 없는 글자다", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
