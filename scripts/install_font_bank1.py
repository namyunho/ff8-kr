#!/usr/bin/env python3
"""뱅크1 폰트와 패치한 EXE 를 디스크 사본에 설치한다.

`patch_font_bank1.py` 가 만든 결과물을 실제로 디스크에 넣는다. 원본은 절대
건드리지 않는다 — `work/patched/` 의 사본에만 쓴다.

## 어디에 넣는가

폰트가 두 파일로 늘어 원래 자리(LBA 849, 17섹터)에 안 들어간다. 디스크를 읽어
확인한 결과 쓸 수 있는 곳은 **끝의 150섹터**뿐이다.

    LBA      24 ~     825   SLPS_018.80        (EXE. 뒤쪽 0 은 빈 공간이 아니라
                                                EXE 이미지의 일부라 RAM 으로 실린다)
    LBA     826 ~ 311,368   FF8DISC1.IMG
    LBA 311,369 ~ 311,518   어느 파일 범위에도 TOC 참조에도 없다  ← 여기

그 150섹터는 Mode 2 **Form 2** 패딩이다. 데이터로 읽히려면 Form 1 로 바꿔야
한다 — 서브헤더의 Form2 비트를 내리고 EDC/ECC 를 새로 만든다. 서브모드 값은
스톡을 그대로 베낀다(데이터 `0x08`, 파일 마지막 `0x89`).

TOC 엔트리 #130 의 LBA 와 크기를 파일 A 로 돌리면 부팅 코드가 그만큼 읽어 온다.
파일 B 의 LBA 는 훅 안에 박혀 있으므로 `bank1-plan.json` 을 그대로 따른다 —
두 값이 어긋나면 엉뚱한 섹터를 읽는다.

    python3 scripts/install_font_bank1.py --dry-run
    python3 scripts/install_font_bank1.py
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_disc as PD                     # noqa: E402
import psx_disc as PXD                      # noqa: E402
import psx_sector as PS                     # noqa: E402

TOC_LBA = 826               # FF8DISC1.IMG 첫 섹터 = (LBA, 크기) 색인
FONT_ENTRY = 130            # 폰트가 놓인 TOC 자리
EXE_LBA = 24                # SLPS_018.80 의 시작
TAIL_LBA = 311_369
TAIL_SECTORS = 150

SUBMODE_DATA = 0x08
SUBMODE_LAST = 0x89         # EOF | EOR | Data — 스톡의 파일 마지막 섹터


def to_form1(sector: bytes, last: bool) -> bytearray:
    """Form 2 패딩 섹터를 Form 1 데이터 섹터로 바꾼다. 헤더 MSF 는 그대로 둔다."""
    out = bytearray(sector)
    for at in (PS.SUBHEADER_AT, PS.SUBHEADER_AT + 4):
        out[at + 0] = 0                                   # 파일 번호
        out[at + 1] = 0                                   # 채널
        out[at + 2] = SUBMODE_LAST if last else SUBMODE_DATA
        out[at + 3] = 0                                   # 코딩 정보
    return out


def blank(path: Path, lba: int, count: int) -> list[int]:
    """정말로 비어 있는지 읽어서 확인한다. 0 으로 보이는 것만으로는 모자라다."""
    dirty: list[int] = []
    with path.open("rb") as handle:
        for index in range(count):
            handle.seek((lba + index) * PS.RAW_SECTOR)
            raw = handle.read(PS.RAW_SECTOR)
            if len(raw) != PS.RAW_SECTOR or any(raw[PS.USER_AT:PS.USER_AT + 2324]):
                dirty.append(lba + index)
    return dirty


def write_tail(path: Path, lba: int, data: bytes) -> int:
    """Form 2 꼬리에 Form 1 데이터를 앉힌다."""
    count = -(-len(data) // PS.USER_SIZE)
    with path.open("r+b") as handle:
        for index in range(count):
            at = (lba + index) * PS.RAW_SECTOR
            handle.seek(at)
            raw = to_form1(handle.read(PS.RAW_SECTOR), last=index == count - 1)
            chunk = data[index * PS.USER_SIZE:(index + 1) * PS.USER_SIZE]
            raw[PS.USER_AT:PS.USER_AT + PS.USER_SIZE] = \
                chunk + bytes(PS.USER_SIZE - len(chunk))
            handle.seek(at)
            handle.write(PS.rebuild(bytes(raw)))
    return count


def toc_entry(path: Path, index: int) -> tuple[int, int]:
    table = PD.read_user(path, TOC_LBA, PS.USER_SIZE)
    return struct.unpack_from("<II", table, index * 8)


def set_toc_entry(path: Path, index: int, lba: int, size: int) -> None:
    table = bytearray(PD.read_user(path, TOC_LBA, PS.USER_SIZE))
    struct.pack_into("<II", table, index * 8, lba, size)
    PD.write_user(path, TOC_LBA, bytes(table))


ORIGINAL_FONT = (Path("work") / "extracted" / "img_130_lba849.bin")
ORIGINAL_EXE = (Path("work") / "disc1" / "SLPS_018.80")


def revert() -> int:
    """폰트 패치만 물린다. 꼬리에 쓴 것은 아무도 안 보므로 그냥 둔다.

    원본 폰트는 LBA 849 에 그대로 있다 — 우리는 TOC 가 가리키는 곳만 옮겼지
    그 자리를 덮은 적이 없다. 그래서 되돌리는 데 필요한 것은 TOC 한 항목과
    EXE 뿐이다.
    """
    if not ORIGINAL_EXE.exists():
        print(f"원본 EXE 가 없다: {ORIGINAL_EXE}", file=sys.stderr)
        return 2
    lba, size = toc_entry(PD.PATCH_BIN, FONT_ENTRY)
    print(f"TOC #{FONT_ENTRY}  {lba:,} / {size:,} -> 849 / 33,764")
    set_toc_entry(PD.PATCH_BIN, FONT_ENTRY, 849, 33764)

    exe = ORIGINAL_EXE.read_bytes()
    print(f"EXE {PD.write_user(PD.PATCH_BIN, EXE_LBA, exe)}섹터를 원본으로 되썼다")

    if toc_entry(PD.PATCH_BIN, FONT_ENTRY) != (849, 33764):
        print("TOC 가 되돌아가지 않았다", file=sys.stderr)
        return 1
    if PD.read_user(PD.PATCH_BIN, EXE_LBA, len(exe)) != exe:
        print("EXE 가 되돌아가지 않았다", file=sys.stderr)
        return 1
    if ORIGINAL_FONT.exists():
        on_disc = PD.read_user(PD.PATCH_BIN, 849, 33764)
        print(f"LBA 849 의 원본 폰트: "
              f"{'그대로다' if on_disc == ORIGINAL_FONT.read_bytes() else '**다르다**'}")
    print("\n폰트 패치를 물렸다. 필드 데이터는 그대로다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--patch", type=Path, default=Path("work/patch"))
    parser.add_argument("--dry-run", action="store_true",
                        help="쓰지 않고 무엇을 할지만 말한다")
    parser.add_argument("--force", action="store_true",
                        help="꼬리에 이미 데이터가 있어도 덮어쓴다 (다시 설치할 때)")
    parser.add_argument("--revert", action="store_true",
                        help="폰트 패치만 되돌린다. TOC #130 을 원래 자리(LBA 849)로 "
                             "돌리고 EXE 를 원본으로 되쓴다. 필드 데이터는 건드리지 "
                             "않는다 — 원인이 폰트 쪽인지 대사 쪽인지 가를 때 쓴다")
    args = parser.parse_args()

    if args.revert:
        return revert()

    plan_path = args.patch / "bank1-plan.json"
    if not plan_path.exists():
        print(f"없는 파일: {plan_path} — 먼저 patch_font_bank1.py 를 돌린다",
              file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    head = (args.patch / "font-head.bin").read_bytes()
    bank1 = (args.patch / "font-bank1.bin").read_bytes()
    exe = (args.patch / "SLPS_018.80").read_bytes()

    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}\n"
              "  python3 scripts/patch_disc.py --init", file=sys.stderr)
        return 2

    head_lba, bank1_lba = plan["head_lba"], plan["bank1_lba"]
    span = bank1_lba + plan["bank1_sectors"] - head_lba
    if head_lba < TAIL_LBA or span > TAIL_SECTORS:
        print(f"쓰려는 범위 LBA {head_lba:,}~{head_lba + span - 1:,} 가 "
              f"꼬리 {TAIL_LBA:,}~{TAIL_LBA + TAIL_SECTORS - 1:,} 밖이다",
              file=sys.stderr)
        return 1
    if bank1_lba < head_lba + plan["head_sectors"]:
        print("파일 B 가 파일 A 를 침범한다 — 계획이 어긋났다", file=sys.stderr)
        return 1

    old_lba, old_size = toc_entry(PD.PATCH_BIN, FONT_ENTRY)
    print(f"TOC #{FONT_ENTRY}  지금 LBA {old_lba:,} / {old_size:,}바이트")
    print(f"파일 A  LBA {head_lba:,}  {len(head):,}바이트 "
          f"({plan['head_sectors']}섹터)  ← TOC #{FONT_ENTRY} 가 여기를 가리킨다")
    print(f"파일 B  LBA {bank1_lba:,}  {len(bank1):,}바이트 "
          f"({plan['bank1_sectors']}섹터)  ← 훅 안에 박힌 LBA")
    print(f"EXE     LBA {EXE_LBA} ~ {EXE_LBA + -(-len(exe) // PS.USER_SIZE) - 1}")

    dirty = blank(PD.PATCH_BIN, head_lba, span)
    if dirty and not args.force:
        print(f"\n쓰려는 자리에 데이터가 있다: LBA {dirty[:6]}"
              f"{f' 외 {len(dirty) - 6}개' if len(dirty) > 6 else ''}\n"
              "  전에 설치한 것이면 --force, 아니면 "
              "python3 scripts/patch_disc.py --init --force", file=sys.stderr)
        return 1
    print(f"  꼬리 {span}섹터 — "
          f"{'비어 있다 (읽어서 확인)' if not dirty else f'{len(dirty)}섹터에 데이터가 있지만 --force'}")

    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    print(f"파일 A {write_tail(PD.PATCH_BIN, head_lba, head)}섹터를 썼다")
    print(f"파일 B {write_tail(PD.PATCH_BIN, bank1_lba, bank1)}섹터를 썼다")
    print(f"EXE {PD.write_user(PD.PATCH_BIN, EXE_LBA, exe)}섹터를 썼다")
    set_toc_entry(PD.PATCH_BIN, FONT_ENTRY, head_lba, len(head))
    print(f"TOC #{FONT_ENTRY} 을 고쳤다")

    problems: list[str] = []
    if toc_entry(PD.PATCH_BIN, FONT_ENTRY) != (head_lba, len(head)):
        problems.append("TOC 가 쓴 대로 안 읽힌다")
    for label, lba, data in (("파일 A", head_lba, head),
                             ("파일 B", bank1_lba, bank1),
                             ("EXE", EXE_LBA, exe)):
        if PD.read_user(PD.PATCH_BIN, lba, len(data)) != data:
            problems.append(f"{label} 가 쓴 대로 안 읽힌다")
    disc = PXD.Disc(PD.PATCH_BIN)
    for lba in (head_lba, head_lba + span - 1):
        form = disc.sector_form(lba)
        if form.get("form") != 1 or not form.get("sync_ok"):
            problems.append(f"LBA {lba:,} 가 Form 1 이 아니다: {form}")

    if problems:
        print("\n되읽기가 어긋난다:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("\n되읽기 확인 — TOC, 두 폰트 파일, EXE, 섹터 형식 모두 일치")
    print(f"→ {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
