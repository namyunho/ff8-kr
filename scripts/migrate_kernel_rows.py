#!/usr/bin/env python3
"""`work/text/kernel-text-ko.json` 을 편집기 스키마로 옮긴다. **한 번만, 멱등하게.**

기존 1,320행에 칸을 더한다. 있던 `ko` 는 초벌(`ko_draft`)로 내려가고,
`ko` 는 「화면에 나가는 글자」 칸으로 남는다 — 그래야 이 칸을 읽는 다섯
소비자가 한 줄도 안 바뀌고 자동으로 축약문을 읽는다(`text_rows` 참고).

## 두 가지를 함께 정리한다

**섹션 번호.** 원본 `kernel.bin` 을 읽어 각 오프셋이 어느 섹션에 속하는지
적는다. 초과가 어느 범주에 몰려 있는지 편집기가 바로 보여 줄 수 있다.

**`이름 :: 설명` 105건.** 번역 단계에서 설명 앞에 아이템 이름을 붙여 놓은
것이 있다. 삽입기가 `strip_name()` 으로 걷어내고 넣으므로, 편집기가 안
걷어내면 **화면에 뜨는 초과 바이트가 실제와 다르다.** 여기서 한 번 걷어내고
떼어낸 접두는 `note` 에 남긴다. `strip_name` 은 멱등이라 삽입 결과는 한
바이트도 안 바뀐다 — 그것을 이 스크립트가 전수로 대조한다.

## 검산 하나

**이관 전후로 디스크에 나갈 바이트가 완전히 같아야 한다.** 1,320건을
`glyph_text.encode` 로 각각 인코딩해 맞춰 본다. 하나라도 다르면 쓰지 않는다.

    python3 scripts/migrate_kernel_rows.py --dry-run
    python3 scripts/migrate_kernel_rows.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT              # noqa: E402
import insert_kernel_text as IK              # noqa: E402
import patch_disc as PD                      # noqa: E402
import patch_overlay_clut as OC              # noqa: E402
import text_measure as TM                    # noqa: E402
import text_rows as TR                       # noqa: E402

TOC_INDEX = 129


def section_map() -> dict[int, int]:
    """원본 `kernel.bin` 에서 `{문자열 오프셋: 섹션 번호}` 를 만든다.

    삽입기와 **같은 방식으로 훑는다** — 섹션 안을 NUL 로 끊어 가며 자리를
    센다. 다르게 훑으면 자리 계산이 갈라진다.
    """
    lba, size = next((l, s) for i, l, s in OC.entries() if i == TOC_INDEX)
    raw = PD.read_user(FT.BIN_PATH, lba, size)
    out: dict[int, int] = {}
    for index, (start, end) in enumerate(IK.sections(raw)):
        at = start
        while at < end:
            stop = raw.find(b"\x00", at)
            if stop < 0 or stop >= end:
                break
            out[at] = index
            at = stop + 1
    return out


def convert(row: dict, sections: dict[int, int], maps: TM.Maps) -> tuple[dict, str]:
    """행 하나를 새 스키마로. `(새 행, 떼어낸 접두)`.

    이미 이관된 행은 파생값만 다시 잰다.
    """
    if "ko_draft" in row:
        return TR.refresh(row, maps), ""

    draft = row.get("ko") or ""
    stripped = IK.strip_name(draft)
    prefix = draft.split("::", 1)[0].strip() if "::" in draft else ""

    fresh = {
        **row,
        "section": sections.get(row["offset"]),
        "ko_draft": stripped,
        "ko_short": None,
        "slot_bytes": len(row["raw_hex"]) // 2,
        "note": (f"이름 접두 제거: {prefix}" if prefix else ""),
    }
    return TR.refresh(fresh, maps), prefix


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, nargs="?", default=TR.KERNEL_JSON)
    parser.add_argument("--layout", type=Path, default=TM.CANON)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    maps = TM.load_maps(args.layout)
    rows = TR.load(args.path)

    try:
        sections = section_map()
    except Exception as error:                      # 원본이 없으면 섹션만 비운다
        print(f"원본 kernel.bin 을 못 읽었다 ({error}) — 섹션 번호는 비워 둔다",
              file=sys.stderr)
        sections = {}

    # **이관 전에 나갈 바이트를 먼저 재 둔다.** 뒤에서 이것과 맞춘다.
    before = {}
    for row in rows:
        text = IK.strip_name(row.get("ko") or "")
        before[row["offset"]] = TM.encoded_length(text, maps)

    fresh_rows = []
    stripped = 0
    for row in rows:
        new_row, prefix = convert(row, sections, maps)
        fresh_rows.append(new_row)
        stripped += bool(prefix)

    # --- 검산: 디스크에 나갈 바이트가 한 건도 안 바뀌었는가 -------------------
    drift = [r for r in fresh_rows
             if TM.encoded_length(TR.effective(r), maps) != before[r["offset"]]]
    no_section = sum(1 for r in fresh_rows if r.get("section") is None)
    bad_slot = [r for r in fresh_rows
                if r["slot_bytes"] != len(r["raw_hex"]) // 2]

    print(f"{args.path}  {len(rows):,}행")
    print(f"  이름 접두를 떼어낸 행 {stripped}건 (note 에 남겼다)")
    print(f"  섹션 번호를 못 채운 행 {no_section}건")
    print(f"  안전 슬롯 불일치 {len(bad_slot)}건")
    print(f"  **삽입 바이트가 달라진 행 {len(drift)}건**")

    if drift or bad_slot:
        for row in drift[:5]:
            print(f"    offset {row['offset']}: "
                  f"{before[row['offset']]}B -> "
                  f"{TM.encoded_length(TR.effective(row), maps)}B", file=sys.stderr)
        print("\n검산이 깨져 쓰지 않는다.", file=sys.stderr)
        return 1

    print("\n집계:")
    for key, value in TR.stats(fresh_rows, maps).items():
        print(f"  {key:>14}  {value:,}")

    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    TR.save(args.path, fresh_rows)
    print(f"\n썼다 (.bak 과 날짜 사본을 남겼다) → {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
