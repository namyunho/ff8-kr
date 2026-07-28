#!/usr/bin/env python3
"""필드 전체의 MSD 메시지를 한 파일로 모은다.

`field.bin` 목록 1,003엔트리를 전수로 훑어 DAT 를 가려내고, 각 DAT 의 MSD 를
풀어 `ja` 를 채운다. 형식은 `work/text/opening-scenes.json` 과 같으므로
`scripts/fill_japanese.py` 로 나중에 다시 채울 수 있다.

DAT 판정은 목록만으로는 안 된다. 해제한 뒤 포인터 12개가 단조 증가하고
`ptr[11]` 이 해제 크기와 맞물리는지로 가린다.

**쓰기 전에 왕복을 검사한다.** `ja` 를 다시 바이트로 되돌려 원본과 다르면
그 메시지를 표시하고, 실패가 있으면 파일을 쓰지 않는다.

    python3 scripts/build_text_db.py work/text/field-messages.json
    python3 scripts/build_text_db.py --check
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402

NAME_BYTES = 24                             # 섹션 0 선두의 필드 이름 상한


def field_name(dat: bytes) -> str:
    """섹션 [0] 선두에 필드 이름 문자열이 있다."""
    raw = dat[FT.DAT_FIRST_SECTION:FT.DAT_FIRST_SECTION + NAME_BYTES]
    name = raw.split(b"\x00")[0]
    return name.decode("ascii", "replace") if name.isascii() else ""


def dat_pointers(dat: bytes) -> tuple[int, ...] | None:
    """DAT 이면 포인터 12개를, 아니면 None 을 준다."""
    if len(dat) < FT.DAT_POINTERS * 4:
        return None
    pointers = struct.unpack_from(f"<{FT.DAT_POINTERS}I", dat, 0)
    base = pointers[0]
    if not (0x80000000 <= base < 0x80800000):
        return None
    if list(pointers) != sorted(pointers):
        return None
    if pointers[11] - base + FT.DAT_FIRST_SECTION != len(dat):
        return None
    return pointers


def message_records(msd: bytes, glyphs: GT.GlyphMap) -> tuple[list[dict], int]:
    records, broken = [], 0
    for index, offset in enumerate(FT.message_offsets(msd)):
        end = msd.find(b"\x00", offset)
        if end < 0:
            end = len(msd)
        data = bytes(msd[offset:end])
        tokens = FT.tokenize(msd, offset)
        text = GT.decode(data, glyphs)
        if GT.encode(text, glyphs) != data:
            broken += 1
            text = ""
        records.append({
            "id": index,
            "byte_budget": end - offset,
            "lines": 1 + sum(1 for t in tokens if t[0] == "ctrl" and t[1] == 2),
            "control_codes": [FT.describe_token(t) for t in tokens
                              if t[0] == "ctrl"],
            "raw_hex": data.hex(" "),
            "translate": any(t[0] == "g" for t in tokens),
            "ja": text,
            "ko": "",
            "ko_short": "",
        })
    return records, broken


def build(glyphs: GT.GlyphMap) -> tuple[list[dict], dict]:
    entries = FT.field_list()
    fields: list[dict] = []
    stats = {"entries": len(entries), "dat": 0, "messages": 0,
             "broken": 0, "bank1": 0, "unnamed": 0}
    for index, (lba, size) in enumerate(entries):
        try:
            dat = FT.load_entry(index)
        except Exception:
            continue
        if dat_pointers(dat) is None:
            continue
        try:
            msd = FT.msd_section(dat)
            records, broken = message_records(msd, glyphs)
        except Exception:
            continue
        stats["dat"] += 1
        stats["messages"] += len(records)
        stats["broken"] += broken
        stats["bank1"] += sum(r["ja"].count("{b1:") for r in records)
        name = field_name(dat)
        if not name:
            stats["unnamed"] += 1
        fields.append({"field": index, "name": name, "lba": lba,
                       "count": len(records), "entries": records})
    return fields, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, nargs="?",
                        help="낼 JSON 경로")
    parser.add_argument("--check", action="store_true",
                        help="세어만 보고 쓰지 않는다")
    args = parser.parse_args()
    if not args.path and not args.check:
        parser.print_help()
        return 0

    glyphs = GT.GlyphMap.load()
    fields, stats = build(glyphs)

    print(f"목록 {stats['entries']}엔트리 중 DAT {stats['dat']}개")
    print(f"  메시지 {stats['messages']:,}건, 왕복 실패 {stats['broken']}건")
    print(f"  이름을 못 읽은 필드 {stats['unnamed']}개")
    print(f"  필드 전용 폰트(뱅크1) 글리프 {stats['bank1']:,}개"
          " — 뱅크0 표의 대상이 아니다")

    if stats["broken"]:
        print("왕복에 실패한 메시지가 있어 쓰지 않는다.")
        return 1
    if not args.path:
        return 0

    document = {
        "note": "필드 DAT 전수. order 가 아니라 MSD 저장 순서다. "
                "제어 코드는 ja 안에 그대로 남아 있다.",
        "fields": fields,
    }
    args.path.parent.mkdir(parents=True, exist_ok=True)
    args.path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    print(f"{args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
