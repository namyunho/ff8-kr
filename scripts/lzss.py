#!/usr/bin/env python3
"""FF8 의 LZSS 압축기. 해제기는 `extract_field_text.lzss_decode` 가 정본이다.

Okumura 계열이다. 링버퍼 4096, 초기 위치 `0xFEE`, 제어 바이트 LSB 우선,
일치 길이 3..18, 오프셋 12비트.

링버퍼를 따로 유지하지 않는다. 해제기가 쓰는 링 내용은 곧 지금까지의 출력이고
출력은 곧 원본이므로, **원본 안에서 직접 일치를 찾고 위치만 링 좌표로 환산**하면
된다.

    원본 위치 j 의 링 좌표 = (0xFEE + j) mod 4096

원본 압축기와 같은 바이트를 낼 것을 목표로 하지 않는다. 요구되는 것은 무손실과
크기다.

## 왜 최적 파싱인가

토큰 비용이 둘뿐이다. 리터럴은 1바이트 + 제어 1비트 = 9비트, 일치는 2바이트 +
제어 1비트 = 17비트다. **오프셋은 비용에 영향이 없고**, 일치는 잘라 써도 유효하다
(같은 오프셋에 길이만 줄이면 된다). 따라서 위치마다 최장 일치 길이만 알면 뒤에서
앞으로 DP 를 돌려 최소 비용 경로를 정확히 구할 수 있다.

탐욕적 매칭은 지금 긴 일치를 잡느라 다음 위치의 더 긴 일치를 놓친다. 실측에서
필드 167 이 18,467 → 17,601바이트, 281 이 28,712 → 27,250바이트로 줄었다.
둘 다 원본보다 작아 배정 섹터에 들어간다.

    python3 scripts/lzss.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402

RING_SIZE = 4096
RING_START = 0xFEE
MIN_MATCH = 3
MAX_MATCH = 18                              # (hi & 0x0F) + 3 의 상한
LITERAL_COST = 9                            # 1바이트 + 제어 1비트
MATCH_COST = 17                             # 2바이트 + 제어 1비트


def _match_length(src: bytes, a: int, b: int, limit: int) -> int:
    """`a` 와 `b` 에서 시작하는 두 구간이 몇 바이트나 같은지.

    겹치는 일치를 허용한다. 해제기가 방금 쓴 바이트를 다시 읽는 경우인데 그
    값은 `src[a+k]` 와 같아 결과가 어긋나지 않는다.
    """
    length = 0
    while length < limit and src[a + length] == src[b + length]:
        length += 1
    return length


def _longest_matches(src: bytes, candidates: int) -> list[int]:
    """위치별 최장 일치 길이. 3 미만은 0 으로 둔다."""
    size = len(src)
    best = [0] * size
    buckets: dict[bytes, list[int]] = {}
    for i in range(size):
        if i + MIN_MATCH > size:
            break
        key = src[i:i + MIN_MATCH]
        bucket = buckets.get(key)
        if bucket:
            window = i - RING_SIZE
            limit = min(MAX_MATCH, size - i)
            longest = 0
            for position in reversed(bucket[-candidates:]):
                if position < window:
                    break
                length = _match_length(src, position, i, limit)
                if length > longest:
                    longest = length
                    if longest == limit:
                        break
            if longest >= MIN_MATCH:
                best[i] = longest
            bucket.append(i)
        else:
            buckets[key] = [i]
    return best


def _plan(src: bytes, longest: list[int]) -> list[int]:
    """뒤에서 앞으로 최소 비용. 각 위치에서 소비할 길이를 돌려준다."""
    size = len(src)
    cost = [0] * (size + 1)
    step = [1] * (size + 1)
    for i in range(size - 1, -1, -1):
        cheapest = cost[i + 1] + LITERAL_COST
        chosen = 1
        for length in range(MIN_MATCH, longest[i] + 1):
            value = cost[i + length] + MATCH_COST
            if value < cheapest:
                cheapest, chosen = value, length
        cost[i] = cheapest
        step[i] = chosen
    return step


def _emit(src: bytes, step: list[int]) -> bytes:
    """정해진 계획대로 실제 바이트를 낸다. 길이가 정해졌으니 오프셋만 고른다."""
    size = len(src)
    out = bytearray()
    chunk = bytearray()
    control = 0
    bit = 0
    buckets: dict[bytes, list[int]] = {}
    i = 0

    def flush() -> None:
        nonlocal control, bit
        if bit:
            out.append(control)
            out.extend(chunk)
            chunk.clear()
            control = 0
            bit = 0

    while i < size:
        take = step[i]
        if take >= MIN_MATCH:
            window = i - RING_SIZE
            found = -1
            for position in reversed(buckets[src[i:i + MIN_MATCH]]):
                if position < window:
                    break
                if _match_length(src, position, i, take) == take:
                    found = position
                    break
            if found < 0:                   # 계획과 어긋나면 리터럴로 떨어진다
                take = 1
            else:
                offset = (RING_START + found) % RING_SIZE
                chunk.append(offset & 0xFF)
                chunk.append(((offset >> 4) & 0xF0) | (take - MIN_MATCH))
        if take < MIN_MATCH:
            control |= 1 << bit
            chunk.append(src[i])
            take = 1

        bit += 1
        if bit == 8:
            flush()
        for k in range(take):
            at = i + k
            if at + MIN_MATCH <= size:
                buckets.setdefault(src[at:at + MIN_MATCH], []).append(at)
        i += take

    flush()
    return bytes(out)


def compress(src: bytes, candidates: int = 256) -> bytes:
    """최적 파싱으로 압축한다. `candidates` 는 위치당 살펴볼 후보 수다."""
    if not src:
        return b""
    return _emit(src, _plan(src, _longest_matches(src, candidates)))


def check(limit: int | None) -> int:
    """필드 DAT 를 전수로 압축해 무손실과 크기를 본다."""
    import build_text_db as DB               # 순환을 피해 여기서 들여온다

    sector = 2048
    lossy = over = done = 0
    smaller = larger = 0
    total_original = total_packed = 0
    for index, (lba, size) in enumerate(FT.field_list()):
        try:
            dat = FT.load_entry(index)
        except Exception:
            continue
        if DB.dat_pointers(dat) is None:
            continue
        packed = compress(dat)
        if FT.lzss_decode(packed) != dat:
            lossy += 1
            print(f"  무손실 실패: 필드 {index}")
        nbytes = len(packed) + 4             # 선두 u32 원본 크기
        total_original += size
        total_packed += nbytes
        smaller += nbytes < size
        larger += nbytes > size
        if (nbytes + sector - 1) // sector > (size + sector - 1) // sector:
            over += 1
            print(f"  섹터 초과: 필드 {index} {size:,}B → {nbytes:,}B")
        done += 1
        if limit and done >= limit:
            break

    print(f"필드 DAT {done}개")
    print(f"  무손실 실패 {lossy}개")
    print(f"  섹터 초과   {over}개")
    print(f"  원본보다 작아진 필드 {smaller}개, 커진 필드 {larger}개")
    print(f"  합계 {total_original:,}B → {total_packed:,}B "
          f"({total_packed / total_original:.3%})")
    return 1 if (lossy or over) else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="필드 DAT 를 전수로 압축해 무손실과 섹터를 본다")
    parser.add_argument("--limit", type=int, help="앞에서 이만큼만 본다")
    args = parser.parse_args()
    if args.check:
        return check(args.limit)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
