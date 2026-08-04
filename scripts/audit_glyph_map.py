#!/usr/bin/env python3
"""롬이 실제로 쓰는 글리프를 전수로 세어 대응표와 맞대어 본다.

## 왜 필요한가

대응표 1,023자 중 `confirmed` 는 14자뿐이고 나머지는 12x12 픽셀을 눈으로 읽은
`read` 다. 그 상태로 초벌번역을 돌렸다. 원문에 못 읽은 글자가 섞이면 번역이
그만큼 어긋난다 — 번역기는 `{b1:37}` 을 보고도 그럴듯한 문장을 만들어 낸다.

그래서 **고쳐야 할 곳을 빈도로 줄 세우는 것**이 이 도구의 목적이다. 안 쓰는
글자를 아무리 정확히 읽어 봐야 소용없고, 많이 쓰는 글자가 틀리면 피해가 크다.

## 뱅크0 과 뱅크1 은 세는 법이 다르다

**뱅크0** 은 인덱스가 곧 글자다. 전역으로 세면 된다.

**뱅크1 은 필드마다 내용이 다르다.** 같은 인덱스가 필드마다 다른 글자다. 그래서
정본 표(`data/glyph-map-bank1.json`)의 키가 인덱스가 아니라 **12x12 셀 값의
해시**다. 세려면 필드마다 MIM 에서 폰트를 꺼내 글리프 모양을 해시해야 한다.
인덱스로 세면 아무 뜻이 없다.

## 무엇을 기준으로 재는가

**디스크의 원본**이다. `work/translate/*.json` 의 `ja` 가 아니다 — 그건 옛
대응표로 디코드된 결과라 이미 오염돼 있다. 오염된 것으로 오염을 재면 안 된다.

    필드 대사   work/text/field-messages.json 의 필드 목록을 따라 MSD 를 다시 읽는다
    메뉴        mngrp.bin 서브0·서브1

## 쓰는 법

    python3 scripts/audit_glyph_map.py
    python3 scripts/audit_glyph_map.py --holes-only
    python3 scripts/audit_glyph_map.py --no-bank1     # 빠르게 뱅크0 만
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_bank1_map as B1                # noqa: E402
import extract_field_text as FT             # noqa: E402
import extract_menu_text as EM              # noqa: E402
import patch_disc as PD                     # noqa: E402

BANK1 = 0x400
ROOT = Path(__file__).resolve().parent.parent
FIELDS = ROOT / "work" / "text" / "field-messages.json"

# `FT.tokenize` 의 역이다. 선두 바이트마다 더해지는 값이 다르다.
#
#   0x18 -> index = 둘째 - 32      0x1C -> (뱅크1) 같은 규칙에 선두만 +4
#   0x19 -> index = 둘째 + 192
#   0x1A -> index = 둘째 + 416
#   0x1B -> index = 둘째 + 640
#
# `GT.glyph_bytes` 를 쓰지 않는다 — 원본이 쓰는 인덱스 중에 그 함수가 감당 못
# 하는 값이 있어 터진다. 여기서는 보여 주는 것이 목적이라 못 되돌리면 물음표다.
OFFSETS = ((0x18, -32), (0x19, 192), (0x1A, 416), (0x1B, 640))


def encoding(slot: int, bank1: bool = False) -> str:
    shift = 4 if bank1 else 0
    if not bank1 and 0 <= slot < 224:
        return f"{slot + 32:02x}"
    for lead, delta in OFFSETS:
        second = slot - delta
        if 0x20 <= second <= 0xFF:
            return f"{lead + shift:02x} {second:02x}"
    return "?"


def field_indices() -> list[int]:
    """필드 DAT 인덱스 목록. 이미 뽑아 둔 전수 결과를 정본으로 삼는다."""
    if not FIELDS.exists():
        raise FileNotFoundError(
            f"{FIELDS} 가 없다. `extract_field_text.py` 로 먼저 뽑는다.")
    document = json.loads(FIELDS.read_text(encoding="utf-8"))
    return [row["field"] for row in document["fields"]]


class Tally:
    """센 것과 **못 센 것**을 함께 들고 다닌다.

    앞선 판에서 `except` 로 실패를 통째로 삼켰다가 필드 0건을 정상으로 보고할
    뻔했다. 실패는 반드시 세어서 보고한다.
    """

    def __init__(self):
        self.bank0: collections.Counter = collections.Counter()
        self.shapes: collections.Counter = collections.Counter()
        self.ctrl: collections.Counter = collections.Counter()
        self.where: dict = collections.defaultdict(collections.Counter)
        self.b1_no_font: collections.Counter = collections.Counter()
        self.failed: list[tuple[int, str]] = []
        self.fields = 0
        self.messages = 0


def scan_field(index: int, tally: Tally, want_bank1: bool) -> None:
    dat = FT.load_entry(index)
    msd = FT.msd_section(dat)
    if not msd:
        raise ValueError("MSD 섹션이 없다")
    starts = FT.message_offsets(msd)
    tally.fields += 1
    tally.messages += len(starts)

    font = None
    if want_bank1:
        try:
            font = FT.field_font(FT.load_entry(index - 1))   # MIM 은 DAT 바로 앞
        except (ValueError, IndexError, KeyError):
            font = None

    for start in starts:
        for token in FT.tokenize(msd, start):
            if token[0] == "ctrl":
                tally.ctrl[(token[1], token[2] is not None)] += 1
                continue
            value = token[1]
            if value & BANK1:
                if not want_bank1:
                    continue                # 세지 않기로 한 것이지 실패가 아니다
                slot = value & 0x3FF
                if font is None:
                    tally.b1_no_font[index] += 1
                    continue
                key = B1.shape_key(font.glyph(slot))
                tally.shapes[key] += 1
                tally.where[("b1", key)]["필드"] += 1
            else:
                tally.bank0[value] += 1
                tally.where[("b0", value)]["필드"] += 1


def scan_menu(tally: Tally) -> None:
    for sub, lba, size in EM.BLOCKS:
        blob = PD.read_user(FT.BIN_PATH, lba, size)
        for _group, off in EM.groups(blob):
            rels, why = EM.entries(blob, off)
            if why:
                continue                    # 형식을 모르는 그룹은 셀 수 없다
            for rel in rels:
                start = off + rel
                stop = blob.find(b"\x00", start)
                if stop < 0:
                    continue
                for token in FT.tokenize(bytes(blob[start:stop]) + b"\x00", 0):
                    if token[0] == "ctrl":
                        tally.ctrl[(token[1], token[2] is not None)] += 1
                    elif not (token[1] & BANK1):
                        tally.bank0[token[1]] += 1
                        tally.where[("b0", token[1])]["메뉴"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("work/glyph-audit"))
    parser.add_argument("--holes-only", action="store_true")
    parser.add_argument("--no-bank1", action="store_true",
                        help="뱅크1 은 건너뛴다 (필드마다 MIM 을 풀어야 해 느리다)")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    if not FT.BIN_PATH.exists():
        print(f"원본이 없다: {FT.BIN_PATH}", file=sys.stderr)
        return 2

    b0_map = json.loads((ROOT / "data" / "glyph-map-bank0.json")
                        .read_text(encoding="utf-8"))["entries"]
    b1_map = json.loads((ROOT / "data" / "glyph-map-bank1.json")
                        .read_text(encoding="utf-8"))["entries"]

    tally = Tally()
    indices = field_indices()
    print(f"필드 {len(indices)}개를 다시 읽는다"
          f"{'' if args.no_bank1 else ' (뱅크1 포함 — 오래 걸린다)'} …")
    for number, index in enumerate(indices, 1):
        try:
            scan_field(index, tally, not args.no_bank1)
        except Exception as error:          # noqa: BLE001 - 세어서 보고한다
            tally.failed.append((index, f"{type(error).__name__}: {error}"))
        if number % 60 == 0:
            print(f"   {number}/{len(indices)}")
    scan_menu(tally)

    print(f"\n필드 {tally.fields}/{len(indices)}개, 메시지 {tally.messages:,}건")
    if tally.failed:
        print(f"**읽지 못한 필드 {len(tally.failed)}개** — 이만큼은 안 센 것이다")
        for index, why in tally.failed[:6]:
            print(f"   필드 {index}: {why}")
    if tally.b1_no_font:
        total = sum(tally.b1_no_font.values())
        print(f"**MIM 폰트를 못 찾아 못 푼 뱅크1 글리프 {total:,}회** "
              f"(필드 {len(tally.b1_no_font)}개)")

    rows = []
    for index, count in tally.bank0.most_common():
        row = b0_map.get(str(index))
        rows.append({
            "뱅크": 0, "키": index, "바이트": encoding(index),
            "글자": (row or {}).get("char", ""),
            "출처": (row or {}).get("source", "없음"),
            "쓰임": count,
            "필드": tally.where[("b0", index)].get("필드", 0),
            "메뉴": tally.where[("b0", index)].get("메뉴", 0),
        })
    for key, count in tally.shapes.most_common():
        row = b1_map.get(key)
        rows.append({
            "뱅크": 1, "키": key, "바이트": "필드마다 다름",
            "글자": (row or {}).get("char", ""),
            "출처": (row or {}).get("source", "없음"),
            "쓰임": count,
            "필드": tally.where[("b1", key)].get("필드", 0), "메뉴": 0,
        })

    holes = [r for r in rows if r["출처"] == "없음"]
    read = [r for r in rows if r["출처"] == "read"]
    firm = [r for r in rows if r["출처"] in ("confirmed", "original")]
    total = sum(r["쓰임"] for r in rows)

    print(f"\n원본이 쓰는 글리프 {len(rows)}종, 총 {total:,}회")
    for name, group in (("대응표에 **없음**", holes),
                        ("눈으로 읽음 read", read),
                        ("확정", firm)):
        n = sum(r["쓰임"] for r in group)
        print(f"  {name:<20} {len(group):>4}종  {n:>8,}회  "
              f"({n * 100 / total:.1f}%)")

    show = holes if args.holes_only else rows
    print(f"\n{'대응표에 없는 것' if args.holes_only else '많이 쓰는 것'} "
          f"상위 {min(args.top, len(show))}종")
    print(f"  {'뱅크':>4} {'키':<14} {'바이트':<14} {'글자':<4} "
          f"{'출처':<10} {'쓰임':>7} {'필드':>6} {'메뉴':>6}")
    for r in show[:args.top]:
        print(f"  {r['뱅크']:>4} {str(r['키']):<14} {r['바이트']:<14} "
              f"{r['글자'] or '·':<4} {r['출처']:<10} {r['쓰임']:>7,} "
              f"{r['필드']:>6,} {r['메뉴']:>6,}")

    args.out.mkdir(parents=True, exist_ok=True)
    table = args.out / "glyph-usage.tsv"
    with table.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n→ {table}")

    # 대응표에 있으나 원본이 한 번도 안 쓰는 것. 읽느라 들인 품이 헛일이었는지,
    # 그리고 배치에서 뺄 수 있는지 판단하는 데 쓴다.
    unused0 = [k for k in b0_map if int(k) not in tally.bank0]
    unused1 = [k for k in b1_map if k not in tally.shapes]
    print(f"대응표에 있으나 원본이 안 쓰는 것  뱅크0 {len(unused0)}자, "
          f"뱅크1 {len(unused1)}자")

    codes = args.out / "control-codes.tsv"
    with codes.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["코드", "인자있음", "쓰임"])
        for (code, has), count in sorted(tally.ctrl.items(), key=lambda x: -x[1]):
            writer.writerow([f"{code:02X}", int(has), count])
    print(f"→ {codes}   제어 코드 {len(tally.ctrl)}종")
    return 1 if (tally.failed or holes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
