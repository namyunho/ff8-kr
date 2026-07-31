#!/usr/bin/env python3
"""디스크 사본에 필드 데이터를 다시 쓴다. 원본은 절대 건드리지 않는다.

`work/patched/` 에 원본 사본을 두고 거기에만 쓴다. 바꾼 섹터는 EDC/ECC 를 다시
만들고 **바꾸지 않은 섹터는 한 바이트도 손대지 않는다**(AGENTS 불변식 4).

필드 데이터는 `[u32 해제 크기][LZSS 스트림]` 이다. 재압축한 결과가 원본보다
작아도 `field.bin` 의 크기 값을 고치지 않는다. 게임은 선두 u32 만큼만 풀고
멈추므로 남는 꼬리는 읽지 않는다. 크기를 고치려면 `field.bin` 자체를 다시
압축해야 하는데 그럴 이유가 없다.

    python3 scripts/patch_disc.py --init
    python3 scripts/patch_disc.py --rewrite 350
    python3 scripts/patch_disc.py --check 350
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import lzss                                 # noqa: E402
import psx_sector as PS                     # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = PROJECT_ROOT / "work" / "patched"
PATCH_BIN = PATCH_DIR / FT.BIN_PATH.name
SECTOR_USER = PS.USER_SIZE


def init(force: bool) -> int:
    if PATCH_BIN.exists() and not force:
        size = PATCH_BIN.stat().st_size
        print(f"이미 있다: {PATCH_BIN} ({size:,}B). 다시 만들려면 --force")
        return 0
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"복사 중 … {FT.BIN_PATH.stat().st_size:,}B")
    shutil.copy2(FT.BIN_PATH, PATCH_BIN)
    cue = FT.BIN_PATH.with_suffix(".cue")
    if cue.exists():
        shutil.copy2(cue, PATCH_DIR / cue.name)
    print(f"사본 {PATCH_BIN}")
    return 0


def read_user(path: Path, lba: int, nbytes: int) -> bytes:
    out = bytearray()
    with path.open("rb") as handle:
        for index in range((nbytes + SECTOR_USER - 1) // SECTOR_USER):
            handle.seek((lba + index) * PS.RAW_SECTOR)
            sector = handle.read(PS.RAW_SECTOR)
            out += sector[PS.USER_AT:PS.USER_AT + SECTOR_USER]
    return bytes(out[:nbytes])


def write_user(path: Path, lba: int, data: bytes) -> int:
    """사용자 데이터만 갈아 끼우고 EDC/ECC 를 다시 만든다. 꼬리는 남긴다."""
    touched = 0
    with path.open("r+b") as handle:
        for index in range((len(data) + SECTOR_USER - 1) // SECTOR_USER):
            chunk = data[index * SECTOR_USER:(index + 1) * SECTOR_USER]
            at = (lba + index) * PS.RAW_SECTOR
            handle.seek(at)
            raw = bytearray(handle.read(PS.RAW_SECTOR))
            if not PS.is_form1(bytes(raw)):
                raise ValueError(f"LBA {lba + index} 가 Mode 2 Form 1 이 아니다")
            raw[PS.USER_AT:PS.USER_AT + len(chunk)] = chunk
            handle.seek(at)
            handle.write(PS.rebuild(bytes(raw)))
            touched += 1
    return touched


def field_bytes(dat: bytes) -> bytes:
    """필드 파일 한 벌. 선두 u32 는 해제 크기다."""
    return struct.pack("<I", len(dat)) + lzss.compress(dat)


def rewrite(index: int, dat: bytes | None = None) -> int:
    """필드를 다시 쓴다. `dat` 이 없으면 내용을 바꾸지 않고 재압축만 한다."""
    if not PATCH_BIN.exists():
        print("먼저 --init 으로 사본을 만든다.", file=sys.stderr)
        return 2
    lba, size = FT.field_list()[index]
    original = FT.load_entry(index)
    payload = field_bytes(dat if dat is not None else original)

    sectors_before = (size + SECTOR_USER - 1) // SECTOR_USER
    sectors_after = (len(payload) + SECTOR_USER - 1) // SECTOR_USER
    print(f"필드 {index}  LBA {lba}  원본 {size:,}B ({sectors_before}섹터)"
          f" → {len(payload):,}B ({sectors_after}섹터)")
    if sectors_after > sectors_before:
        print("섹터가 늘어난다. 뒤 파일을 밀게 되므로 쓰지 않는다.", file=sys.stderr)
        return 1

    touched = write_user(PATCH_BIN, lba, payload)
    print(f"  섹터 {touched}개를 다시 썼다")
    return 0


def rebuild_msd(msd: bytes, replacements: dict[int, bytes],
                keep_size: bool = True) -> bytes:
    """메시지 몇 개를 갈아 끼운 MSD 를 만든다. 오프셋 배열을 다시 쓴다.

    작아지면 **원본 크기까지 채운다.** R7 은 MSD 가 커져 뒤 섹션이 밀릴 때
    생기는데, 크기를 그대로 두면 섹션 오프셋이 한 바이트도 안 움직여 애초에
    발생하지 않는다. 메시지 수는 `offset[0] / 4` 로 정해지므로 마지막 메시지
    뒤의 남는 바이트는 읽히지 않는다.

    **커지면 채울 방법이 없으므로 멈춘다.** 예전에는 `keep_size` 가 작아질
    때만 채우고 커질 때는 아무 말 없이 통과시켰다. 그러면 R7 을 막는다고
    적어 놓고 실제로는 그대로 일어난다 — 뒤 섹션이 밀리고, 밀린 오프셋이
    4의 배수를 벗어나면 정렬을 확인하지 않는 고속 memcpy(`0x800394FC`)가
    첫 `lw` 에서 예외를 낸다. 게임이 죽는 자리와 원인이 멀어 추적이 어렵다.
    지금 실패하는 편이 낫다.
    """
    bodies = []
    for index, offset in enumerate(FT.message_offsets(msd)):
        end = msd.find(b"\x00", offset)
        if end < 0:
            end = len(msd)
        bodies.append(replacements.get(index, bytes(msd[offset:end])))
    header_size = len(bodies) * 4
    offsets, cursor = [], header_size
    for body in bodies:
        offsets.append(cursor)
        cursor += len(body) + 1
    header = b"".join(struct.pack("<I", value) for value in offsets)
    out = header + b"".join(body + b"\x00" for body in bodies)
    if keep_size:
        if len(out) > len(msd):
            raise ValueError(
                f"MSD 가 {len(out) - len(msd):,}바이트 커졌다 "
                f"({len(msd):,} -> {len(out):,}). 그대로 쓰면 뒤 섹션이 밀려 "
                "R7 이 일어난다. 번역문을 줄이거나 섹션 표를 고쳐야 한다.")
        out += b"\x00" * (len(msd) - len(out))
    return out


def korean_map(layout: Path):
    """배치 후보를 글리프 대응표로 바꾼다.

    두 형식을 받는다. `base` 가 있으면 그 인덱스부터 한글이고 앞은 원본 그대로
    다(수직 관통 시험이 쓰던 방식). 없으면 **뱅크0 전체가 우리 것**이다.
    """
    import glyph_text as GT

    document = json.loads(layout.read_text(encoding="utf-8"))
    chars = document["chars"]
    base = document.get("base")
    if base is None:
        return GT.GlyphMap({index: char for index, char in enumerate(chars)}), 0
    glyphs = GT.GlyphMap.load()
    entries = {i: c for i, c in glyphs.char.items() if i < base}
    entries.update({base + n: c for n, c in enumerate(chars)})
    return GT.GlyphMap(entries), base


def fit(layout: Path, root: Path) -> int:
    """번역문을 넣은 MSD 가 **원본 크기 안에 들어가는지** 필드마다 잰다.

    R7 은 MSD 를 갈아 끼우면 뒤 섹션이 밀려 깨지는 문제다. 우회안은 "원본과
    같은 크기를 유지" 인데, 그게 몇 필드에서 가능한지 잰 적이 없다. 남으면
    종료 바이트로 채우면 되므로 **작거나 같으면 안전**하고 커지면 R7 에 걸린다.

    인코딩도 여기서 함께 검증된다. 배치에 없는 글자가 있으면 실패로 잡힌다.
    """
    import build_text_db as DB
    import glyph_text as GT

    glyphs, base = korean_map(layout)
    print(f"배치 {len(glyphs.char)}자" + (f", {base} 이후가 한글" if base else ""))

    safe = tight = over = broken = 0
    worst: list[tuple[int, str, int, int]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        messages = {entry["id"]: entry["ko"] for entry in document["entries"]
                    if entry.get("ko", "").strip()}
        if not messages:
            continue
        try:
            dat = FT.load_entry(document["field"])
            msd = FT.msd_section(dat)
        except Exception:
            continue
        bank1 = DB.bank1_for(document["field"], msd, glyphs)
        replacements, failed = {}, []
        for identifier, text in messages.items():
            try:
                replacements[identifier] = GT.encode(text, glyphs, bank1)
            except ValueError as error:
                failed.append(f"{document['name']}#{identifier} {error}")
        if failed:
            broken += 1
            if len(worst) < 6:
                worst.append((0, failed[0], 0, 0))
            continue
        grown = len(rebuild_msd(msd, replacements, keep_size=False)) - len(msd)
        if grown <= 0:
            safe += 1
        elif grown <= 32:
            tight += 1
        else:
            over += 1
        worst.append((grown, document["name"], len(msd), grown))

    worst.sort(reverse=True)
    total = safe + tight + over
    print(f"\n필드 {total}개 — MSD 를 원본 크기 안에 넣을 수 있는가")
    print(f"  안전 (같거나 작다)   {safe:>3}개  ({safe / max(total, 1):.1%})")
    print(f"  근소 초과 (≤32B)     {tight:>3}개")
    print(f"  초과                 {over:>3}개")
    if broken:
        print(f"  인코딩 실패          {broken:>3}개")
    print("\n가장 많이 늘어난 필드")
    for grown, name, size, _ in worst[:8]:
        print(f"    {name:<12} 원본 {size:>6,}B  {grown:+,}B")
    return 0


def apply(path: Path, layout: Path | None) -> int:
    """`{"필드": {"메시지 id": "번역문"}}` 을 받아 해당 필드를 다시 쓴다."""
    import json

    import build_text_db as DB
    import glyph_text as GT

    plan = json.loads(path.read_text(encoding="utf-8"))
    glyphs = GT.GlyphMap.load()
    if layout and layout.exists():
        # 한글을 써넣은 자리는 원래 한자였다. 그 구간의 대응을 갈아 끼운다.
        placed = json.loads(layout.read_text(encoding="utf-8"))
        base = placed["base"]
        entries = {i: c for i, c in glyphs.char.items() if i < base}
        entries.update({base + n: c for n, c in enumerate(placed["chars"])})
        glyphs = GT.GlyphMap(entries)
        print(f"배치 적용: {base} 이후 {len(placed['chars'])}자가 한글이다")
    failed = 0
    for raw_field, messages in plan.items():
        index = int(raw_field)
        dat = FT.load_entry(index)
        msd = FT.msd_section(dat)
        bank1 = DB.bank1_for(index, msd, glyphs)
        replacements = {}
        for raw_id, text in messages.items():
            try:
                replacements[int(raw_id)] = GT.encode(text, glyphs, bank1)
            except ValueError as error:
                print(f"  실패 {index}#{raw_id}: {error}")
                failed += 1
        if not replacements:
            continue
        import analyze_text_budget as AB

        new_dat = AB.splice_msd(dat, rebuild_msd(msd, replacements))
        print(f"필드 {index}: 메시지 {len(replacements)}건 교체, "
              f"DAT {len(dat):,} → {len(new_dat):,}B")
        if rewrite(index, new_dat):
            failed += 1
    return 1 if failed else 0


def check(index: int, show: int = 0) -> int:
    """사본에서 되읽어 게임이 읽을 수 있는 상태인지 본다.

    원본과 같은지가 아니라 **온전한지**를 본다. 일부러 바꾼 필드는 당연히
    원본과 다르다. 다른 자리가 어디인지는 참고로 함께 낸다.
    """
    import build_text_db as DB
    import glyph_text as GT

    lba, size = FT.field_list()[index]
    original = FT.load_entry(index)
    raw = read_user(PATCH_BIN, lba, size)
    declared = struct.unpack_from("<I", raw, 0)[0]
    decoded = FT.lzss_decode(raw[4:])
    if len(decoded) < declared:
        print(f"해제가 모자란다: {len(decoded):,} < 선언 {declared:,}")
        return 1
    dat = decoded[:declared]

    print(f"필드 {index}: 선두 u32 {declared:,}B 만큼 해제됨")
    if DB.dat_pointers(dat) is None:
        print("  DAT 포인터가 어긋난다. 게임이 읽지 못한다.")
        return 1
    print("  DAT 포인터 정합")
    msd = FT.msd_section(dat)
    offsets = FT.message_offsets(msd)
    print(f"  MSD {len(msd):,}B, 메시지 {len(offsets)}개")

    glyphs = GT.GlyphMap.load()
    bank1 = DB.bank1_for(index, msd, glyphs)
    changed = []
    for number, offset in enumerate(offsets):
        end = msd.find(b"\x00", offset)
        body = bytes(msd[offset:end if end >= 0 else len(msd)])
        try:
            text = GT.decode(body, glyphs, bank1)
        except Exception as error:
            print(f"  메시지 {number} 를 읽지 못했다: {error}")
            return 1
        if body != _original_message(original, number):
            changed.append((number, text))
    print(f"  원본과 다른 메시지 {len(changed)}건")
    for number, text in changed[:show or 3]:
        print(f"    #{number} {text}")
    return 0


def _original_message(dat: bytes, number: int) -> bytes:
    msd = FT.msd_section(dat)
    offsets = FT.message_offsets(msd)
    if number >= len(offsets):
        return b""
    offset = offsets[number]
    end = msd.find(b"\x00", offset)
    return bytes(msd[offset:end if end >= 0 else len(msd)])


def untouched(count: int) -> int:
    """사본이 원본과 바이트 동일한지 표본으로 본다."""
    import hashlib

    if not PATCH_BIN.exists():
        print("먼저 --init 으로 사본을 만든다.", file=sys.stderr)
        return 2
    if PATCH_BIN.stat().st_size != FT.BIN_PATH.stat().st_size:
        print("크기가 다르다.", file=sys.stderr)
        return 1
    step = max(1, (FT.BIN_PATH.stat().st_size // PS.RAW_SECTOR) // count)
    diff = 0
    with FT.BIN_PATH.open("rb") as a, PATCH_BIN.open("rb") as b:
        for n in range(count):
            at = n * step * PS.RAW_SECTOR
            a.seek(at), b.seek(at)
            if a.read(PS.RAW_SECTOR) != b.read(PS.RAW_SECTOR):
                diff += 1
    print(f"표본 {count}섹터 중 다른 섹터 {diff}개")
    return 1 if diff else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true", help="원본 사본을 만든다")
    parser.add_argument("--force", action="store_true", help="사본을 다시 만든다")
    parser.add_argument("--rewrite", type=int, metavar="필드",
                        help="내용을 바꾸지 않고 재압축해 다시 쓴다")
    parser.add_argument("--check", type=int, metavar="필드",
                        help="사본에서 되읽어 원본과 같은지 본다")
    parser.add_argument("--apply", type=Path, metavar="JSON",
                        help="{필드: {메시지 id: 문자열}} 을 적용한다")
    parser.add_argument("--layout", type=Path,
                        default=PATCH_DIR / "hangul-layout.json",
                        help="한글 배치 JSON (inject_hangul_font.py 가 낸다)")
    parser.add_argument("--fit", type=Path, metavar="워크시트",
                        help="번역문이 원본 MSD 크기에 들어가는지 잰다")
    parser.add_argument("--untouched", type=int, metavar="N",
                        help="사본이 원본과 같은지 표본 N섹터로 본다")
    args = parser.parse_args()

    if args.init:
        return init(args.force)
    if args.fit:
        return fit(args.layout, args.fit)
    if args.untouched:
        return untouched(args.untouched)
    if args.apply:
        return apply(args.apply, args.layout)
    if args.rewrite is not None:
        return rewrite(args.rewrite)
    if args.check is not None:
        return check(args.check)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
