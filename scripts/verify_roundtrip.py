#!/usr/bin/env python3
"""쓰기 경로를 열기 전의 관문. 무수정 round-trip 을 검사한다.

`AGENTS.md` 의 불변식 9 다. 추출한 것을 그대로 되돌렸을 때 원본과 같지
않다면 번역을 넣을 자격이 없다. 세 층을 따로 본다.

1. **MSD 재직렬화** — 오프셋 배열과 텍스트를 다시 만들어 원본 바이트와
   비교한다. 여기가 깨지면 파서가 형식을 잘못 읽고 있다는 뜻이다.
2. **LZSS 무손실** — 해제본을 다시 압축하고 또 해제해 같은지 본다.
   압축기는 이 저장소에 없었으므로 여기서 처음 만든다.
3. **크기 적합** — 재압축 결과가 원본 섹터 할당 안에 드는가. IMG TOC 와
   필드 목록이 절대 LBA 를 쓰므로 넘치면 뒤 파일이 전부 밀린다.

재압축이 원본과 **바이트 동일할 필요는 없다.** 원본을 만든 압축기의 탐색
전략을 알 수 없기 때문이다. 요구되는 것은 해제 결과가 같고 크기가 할당
안에 드는 것이다.

    python3 scripts/verify_roundtrip.py 293
    python3 scripts/verify_roundtrip.py --sample 40 --json work/analysis/rt.json
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT         # noqa: E402

RING_SIZE = 4096
RING_START = 0xFEE
MIN_MATCH = 3
MAX_MATCH = 18                          # (hi & 0x0F) + 3 의 상한
SECTOR = 2048


def lzss_compress(src: bytes) -> bytes:
    """`FT.lzss_decode` 와 짝이 되는 압축기.

    링버퍼를 따로 유지하지 않는다. 해제기가 쓰는 링 내용은 곧 지금까지의
    출력이고 출력은 곧 원본이므로, **원본 안에서 직접 일치를 찾고 위치만
    링 좌표로 환산**하면 된다.

        원본 위치 j 의 링 좌표 = (0xFEE + j) mod 4096

    창은 4096바이트이므로 `j >= i - 4096` 이어야 한다. 겹치는 일치는
    허용된다. 해제기가 방금 쓴 바이트를 다시 읽는 경우인데, 그 값은
    `src[j+k]` 와 같아 결과가 어긋나지 않는다.

    원본 압축기와 같은 바이트를 낼 것을 목표로 하지 않는다. 요구되는
    것은 무손실과 크기다.
    """
    out = bytearray()
    chunk = bytearray()
    control = 0
    bit = 0
    i = 0

    # 3바이트 키 -> 등장 위치. 뒤쪽 후보부터 보면 대개 더 긴 일치가 나온다.
    buckets: dict[bytes, list[int]] = {}

    def flush() -> None:
        nonlocal control, bit
        if bit:
            out.append(control)
            out.extend(chunk)
            chunk.clear()
            control = 0
            bit = 0

    while i < len(src):
        best_len = 0
        best_pos = 0
        if i + MIN_MATCH <= len(src):
            key = src[i:i + MIN_MATCH]
            window_start = i - RING_SIZE
            for candidate in reversed(buckets.get(key, [])[-128:]):
                if candidate < window_start:
                    break
                length = 0
                while (length < MAX_MATCH and i + length < len(src)
                       and src[candidate + length] == src[i + length]):
                    length += 1
                if length > best_len:
                    best_len, best_pos = length, candidate
                    if length == MAX_MATCH:
                        break

        if best_len >= MIN_MATCH:
            offset = (RING_START + best_pos) % RING_SIZE
            chunk.append(offset & 0xFF)
            chunk.append(((offset >> 4) & 0xF0) | (best_len - MIN_MATCH))
            step = best_len
        else:
            control |= 1 << bit
            chunk.append(src[i])
            step = 1

        bit += 1
        if bit == 8:
            flush()

        for k in range(step):
            at = i + k
            if at + MIN_MATCH <= len(src):
                buckets.setdefault(src[at:at + MIN_MATCH], []).append(at)
        i += step

    flush()
    return bytes(out)


def serialize_msd(msd: bytes) -> bytes:
    """오프셋 배열과 텍스트를 파싱한 결과만으로 다시 만든다."""
    offsets = FT.message_offsets(msd)
    header = b"".join(struct.pack("<I", value) for value in offsets)
    body = bytearray(msd[len(header):])
    return bytes(header) + bytes(body)


def check_field(index: int) -> dict:
    lba, size = FT.field_list()[index]
    raw = FT.read_sectors(lba, size)
    decoded = FT.lzss_decode(raw[4:])

    result = {"field": index, "lba": lba, "original_bytes": size}

    # 1. MSD 재직렬화
    try:
        msd = FT.msd_section(decoded)
        rebuilt = serialize_msd(msd)
        result["msd_bytes"] = len(msd)
        result["msd_identical"] = rebuilt == msd
    except Exception as error:                      # 필드가 아닐 수 있다
        result["msd_identical"] = None
        result["msd_error"] = str(error)

    # 2. LZSS 무손실
    packed = lzss_compress(decoded)
    result["lzss_lossless"] = FT.lzss_decode(packed) == decoded
    result["recompressed_bytes"] = len(packed) + 4      # 선두 u32 원본 크기

    # 3. 크기 적합 — 섹터 할당 안에 드는가
    original_sectors = (size + SECTOR - 1) // SECTOR
    new_sectors = (result["recompressed_bytes"] + SECTOR - 1) // SECTOR
    result["original_sectors"] = original_sectors
    result["new_sectors"] = new_sectors
    result["sectors_fit"] = new_sectors <= original_sectors
    return result


def is_dat(index: int) -> bool:
    """3종 세트에서 DAT 만 고른다. 포인터 12개가 단조 증가해야 한다."""
    try:
        decoded = FT.load_entry(index)
        pointers = struct.unpack_from("<12I", decoded, 0)
    except Exception:
        return False
    base = pointers[0]
    if not (0x80000000 <= base < 0x80800000):
        return False
    return (list(pointers) == sorted(pointers)
            and pointers[11] - base + FT.DAT_FIRST_SECTION == len(decoded))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("field", type=int, nargs="?", help="필드 목록 인덱스")
    parser.add_argument("--sample", type=int, default=0,
                        help="앞에서부터 DAT 를 이만큼 찾아 검사한다")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    targets: list[int] = []
    if args.field is not None:
        targets = [args.field]
    elif args.sample:
        index = 0
        while len(targets) < args.sample and index < FT.FIELD_LIST_COUNT:
            if is_dat(index):
                targets.append(index)
            index += 1
    else:
        parser.error("필드 번호나 --sample 중 하나가 필요하다")

    results = []
    print(f"{'필드':>6} {'MSD동일':>8} {'LZSS무손실':>10} "
          f"{'원본B':>9} {'재압축B':>9} {'섹터':>9} {'적합':>6}")
    for index in targets:
        row = check_field(index)
        results.append(row)
        msd = {True: "예", False: "아니오", None: "해당없음"}[row["msd_identical"]]
        print(f"{index:>6} {msd:>8} "
              f"{'예' if row['lzss_lossless'] else '아니오':>10} "
              f"{row['original_bytes']:>9,} {row['recompressed_bytes']:>9,} "
              f"{row['original_sectors']:>4}->{row['new_sectors']:<4} "
              f"{'예' if row['sectors_fit'] else '아니오':>6}")

    total = len(results)
    msd_ok = sum(1 for r in results if r["msd_identical"] is True)
    lzss_ok = sum(1 for r in results if r["lzss_lossless"])
    fit_ok = sum(1 for r in results if r["sectors_fit"])
    print()
    print(f"검사 {total}개 — MSD 재직렬화 {msd_ok}, LZSS 무손실 {lzss_ok}, "
          f"섹터 적합 {fit_ok}")
    passed = msd_ok == total and lzss_ok == total and fit_ok == total
    print("판정: " + ("통과 — 쓰기 경로를 열 수 있다"
                     if passed else "실패 — 원인을 찾기 전에는 쓰지 않는다"))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"checked": total, "passed": passed, "results": results},
            indent=2, ensure_ascii=False))
        print(f"{args.json}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
