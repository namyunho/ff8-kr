#!/usr/bin/env python3
"""번역문이 들어갈 자리가 있는지 잰다. M5 판정용 수치를 낸다.

두 가지를 따로 본다.

1. **글리프 칸** — 뱅크0 은 882칸이다. 일본어 원문이 그중 몇 칸을 쓰는지 세면
   한글에 내줄 수 있는 칸이 몇인지 나온다.
2. **바이트** — 인덱스 0..223 은 1바이트, 224 이상은 2바이트로 실린다. 일본어는
   가나가 대부분이라 1바이트가 압도적이다. 한글 음절이 224 뒤에 놓이면 같은
   글자 수라도 바이트가 두 배가 된다. 이게 in-place 교체의 실제 제약이다.

최악을 재려고 원문을 **모든 글리프 2바이트**로 다시 인코딩한다. 폰트 인코딩에
0..223 을 두 바이트로 싣는 선두 `0x18` 형식이 있어 글자를 바꾸지 않고 폭만
넓힐 수 있다. 같은 본문·같은 엔트로피라 압축 결과가 현실적이다.

섹터 판정은 `docs/roadmap.md` M6 의 크기 제약을 따른다. IMG 목차가 절대 LBA 를
담으므로 파일이 원래 섹터 수를 넘으면 뒤 파일이 전부 밀린다.

    python3 scripts/analyze_text_budget.py
    python3 scripts/analyze_text_budget.py --limit 40
"""

from __future__ import annotations

import argparse
import collections
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_text_db as DB                  # noqa: E402
import extract_field_text as FT             # noqa: E402
import lzss                                 # noqa: E402

SECTOR = 2048
BANK1 = 0x400
SINGLE_BYTE = 224                           # 이 미만은 1바이트로 실린다


def wide_bytes(index: int) -> bytes:
    """글리프 하나를 무조건 두 바이트로 싣는다."""
    bank1 = bool(index & BANK1)
    index &= ~BANK1
    base = 0x1C if bank1 else 0x18
    for step, low in enumerate((0, 224, 448, 672)):
        if low <= index < low + 224:
            return bytes((base + step, index - low + 32))
    raise ValueError(f"범위 밖 글리프 인덱스: {index}")


def widen(msd: bytes, keep: set[int] | None = None) -> bytes:
    """글리프를 2바이트로 바꾼 MSD 를 만든다. 제어 코드는 그대로 둔다.

    `keep` 에 든 인덱스는 1바이트 자리에 남는다. 한글 폰트를 만들 때 자주 쓰는
    음절을 0..223 에 놓는 배치를 흉내 내는 것이다.
    """
    offsets = FT.message_offsets(msd)
    bodies = []
    for offset in offsets:
        end = msd.find(b"\x00", offset)
        if end < 0:
            end = len(msd)
        out = bytearray()
        for token in FT.tokenize(msd, offset):
            if token[0] == "ctrl":
                out.append(token[1])
                if token[2] is not None:
                    out.append(token[2])
            elif keep is not None and token[1] in keep:
                out.append((token[1] & ~BANK1) % SINGLE_BYTE + 32)
            else:
                out += wide_bytes(token[1])
        bodies.append(bytes(out))

    header_size = len(offsets) * 4
    new_offsets, cursor = [], header_size
    for body in bodies:
        new_offsets.append(cursor)
        cursor += len(body) + 1                 # 종료 바이트
    header = b"".join(struct.pack("<I", value) for value in new_offsets)
    return header + b"".join(body + b"\x00" for body in bodies)


def splice_msd(dat: bytes, msd: bytes) -> bytes:
    """DAT 안의 MSD 섹션을 갈아 끼우고 뒤 섹션 포인터를 민다.

    **미는 양은 4의 배수여야 한다.** 원본 DAT 304개의 섹션 오프셋 3,648개가
    예외 없이 4정렬인데, 어긋난 오프셋을 넘겨받은 고속 memcpy(`0x800394FC`)는
    정렬을 확인하지 않고 `lw` 를 써서 그 자리에서 예외를 낸다. 한 번
    207바이트를 밀어 그렇게 죽은 적이 있다.
    """
    pointers = list(struct.unpack_from(f"<{FT.DAT_POINTERS}I", dat, 0))
    base = pointers[0]
    start = pointers[FT.MSD_POINTER] - base + FT.DAT_FIRST_SECTION
    end = pointers[FT.MSD_POINTER + 1] - base + FT.DAT_FIRST_SECTION
    delta = len(msd) - (end - start)
    if delta % 4:
        raise ValueError(
            f"섹션을 {delta:+,}바이트 밀려 한다. 4의 배수가 아니라 뒤 섹션의 "
            "정렬이 깨진다 — MSD 를 4의 배수 길이로 맞춰야 한다.")
    for i in range(FT.MSD_POINTER + 1, FT.DAT_POINTERS):
        pointers[i] += delta
    head = b"".join(struct.pack("<I", value) for value in pointers)
    return head + dat[len(head):start] + msd + dat[end:]


def sectors(nbytes: int) -> int:
    return (nbytes + SECTOR - 1) // SECTOR


def collect(limit: int | None):
    """DAT 를 한 번만 읽어 두고 빈도를 센다."""
    fields = []
    glyphs = collections.Counter()
    width = collections.Counter()
    bank1_kinds = []
    for index, (lba, size) in enumerate(FT.field_list()):
        try:
            dat = FT.load_entry(index)
        except Exception:
            continue
        if DB.dat_pointers(dat) is None:
            continue
        try:
            msd = FT.msd_section(dat)
            offsets = FT.message_offsets(msd)
        except Exception:
            continue
        seen_bank1 = set()
        for offset in offsets:
            for token in FT.tokenize(msd, offset):
                if token[0] != "g":
                    continue
                if token[1] & BANK1:
                    seen_bank1.add(token[1] & ~BANK1)
                    width["뱅크1 (2바이트)"] += 1
                else:
                    glyphs[token[1]] += 1
                    width["뱅크0 1바이트" if token[1] < SINGLE_BYTE
                          else "뱅크0 2바이트"] += 1
        bank1_kinds.append(len(seen_bank1))
        fields.append((index, size, dat, msd))
        if limit and len(fields) >= limit:
            break
    return fields, glyphs, width, bank1_kinds


def analyse(limit: int | None) -> int:
    fields, glyphs, width, bank1_kinds = collect(limit)
    total = sum(width.values())

    print(f"필드 DAT {len(fields)}개\n")
    print("1. 글리프 칸 — 한글에 내줄 수 있는 자리")
    print(f"   일본어 원문이 쓰는 뱅크0 칸 : {len(glyphs)} / 882")
    print(f"   비어 있는 칸               : {882 - len(glyphs)}")
    print(f"   필드 전용 뱅크1 종수        : 최대 {max(bank1_kinds)}, "
          f"평균 {sum(bank1_kinds) / len(bank1_kinds):.0f}")

    print("\n2. 글리프 인코딩 폭 — 지금")
    for key, value in width.most_common():
        print(f"   {key:14} {value:8,}개  ({value / total:5.1%})")
    now_bytes = (width["뱅크0 1바이트"]
                 + 2 * (width["뱅크0 2바이트"] + width["뱅크1 (2바이트)"]))
    print(f"   글자당 평균 {now_bytes / total:.2f}바이트")

    print("\n3. 빈도 쏠림 — 1바이트 자리에 무엇을 넣을지 정한다")
    ranked = [index for index, _ in glyphs.most_common()]
    used = sum(glyphs.values())
    for count in (128, SINGLE_BYTE, 384):
        covered = sum(glyphs[i] for i in ranked[:count])
        print(f"   가장 잦은 {count:3}자가 본문의 {covered / used:6.2%} 를 덮는다")
    keep = set(ranked[:SINGLE_BYTE])
    covered = sum(glyphs[i] for i in keep) / used
    print(f"   → 상위 {SINGLE_BYTE}자를 1바이트 자리에 놓으면 "
          f"글자당 평균 {2 - covered:.2f}바이트")

    print("\n4. 섹터 적합 — IMG 목차가 절대 LBA 라 원래 섹터 수를 넘으면 안 된다")
    rows = []
    for index, size, dat, msd in fields:
        packed_now = len(lzss.compress(dat)) + 4
        packed_wide = len(lzss.compress(splice_msd(dat, widen(msd)))) + 4
        packed_real = len(lzss.compress(splice_msd(dat, widen(msd, keep)))) + 4
        budget = sectors(size) * SECTOR
        rows.append({
            "field": index,
            "sectors": sectors(size),
            "now": sectors(packed_now),
            "wide": sectors(packed_wide),
            "real": sectors(packed_real),
            "slack_wide": budget - packed_wide,
            "slack_real": budget - packed_real,
        })
    over_now = [r for r in rows if r["now"] > r["sectors"]]
    over_wide = [r for r in rows if r["wide"] > r["sectors"]]
    over_real = [r for r in rows if r["real"] > r["sectors"]]
    print(f"   손대지 않고 재압축만 해도 넘침   : {len(over_now):3}개 / {len(rows)}")
    print(f"   모두 2바이트 (최악)              : {len(over_wide):3}개 / {len(rows)}")
    print(f"   상위 {SINGLE_BYTE}자만 1바이트 (현실적) : "
          f"{len(over_real):3}개 / {len(rows)}")
    for label, group in (("손대지 않아도 넘치는 필드", over_now),
                         ("현실 배치에서 넘치는 필드", over_real)):
        if group:
            print(f"   {label}: "
                  + ", ".join(str(r["field"]) for r in group[:10]))

    print("\n5. 여유 — 최악(모두 2바이트)일 때 배정 섹터에 남는 바이트")
    margins = sorted(r["slack_wide"] for r in rows)
    print(f"   가장 빠듯한 필드 {margins[0]:,}B")
    print(f"   중앙값           {margins[len(margins) // 2]:,}B")
    print(f"   가장 여유로운 필드 {margins[-1]:,}B")
    print("   번역문 글자 수가 원문과 같다는 가정이다. 더 길어지면 그만큼 줄어든다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="앞에서 이만큼만 본다")
    args = parser.parse_args()
    return analyse(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
