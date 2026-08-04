#!/usr/bin/env python3
"""롬 전체에서 **읽히는 문자열을 용도 불문하고 전부** 긁어낸다.

## 왜 「전부」인가

지금까지는 쓰임을 아는 것만 뽑았다 — 필드 대사와 메뉴. 아이템·어빌리티·GF
이름과 설명, 전투 중 대사는 어디 있는지 모른 채 남았다.

무엇에 쓰이는지 모른다고 안 뽑으면 영영 모른다. **먼저 전부 긁고, 쓰임은
나중에 붙인다.** 걸러 내는 것은 사람이 표를 보고 할 일이지 추출기가 할 일이
아니다.

## 왜 압축 해제를 함께 시도하는가

목차를 날것으로 훑으면 134개 중 2개만 걸린다. 나머지가 자료가 없어서가 아니라
**LZSS 로 눌려 있어서**다. 필드가 그랬다. 그래서 항목마다 두 번 본다 —
날것 그대로 한 번, 풀어서 한 번.

## 무엇을 내는가

널로 끊기는 조각마다 한 줄이다. 거른 것은 「두 글자 미만」뿐이다.

    출처            어디서 나왔는가 (목차 번호, 압축 여부)
    오프셋          그 안에서의 자리
    바이트          원본 바이트 (되돌릴 수 있게)
    본문            디코드 결과
    미인식          못 읽은 글자가 섞였는가
    흔한글자        일본어 문장다움 점수 — 정렬해서 훑어보기 위한 것

## 쓰는 법

    python3 scripts/extract_all_text.py
    python3 scripts/extract_all_text.py --entry 24
    python3 scripts/extract_all_text.py --min-common 0.15   # 문장다운 것만
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402
import patch_overlay_clut as PO             # noqa: E402

COMMON = set("のはをにがでとしいたんーるれаなこそつてきカルンイシトスラー、。")
MAX_READ = 4 << 20

# **선택 분기.** `{06:xx}` 는 선택 구간을 여닫는 짝이다. 통계로 확정했다 —
# 이 코드가 든 대사 1,677건 중 1,662건에서 두 번 이상 나오고, 정확히 두 번인
# 1,564건 중 1,368건이 `{06:25} … {06:27}` 이다. 닫는 쪽은 언제나 27 이다.
#
# 줄머리에 오는 비율이 24% 뿐이라 **줄마다 붙는 커서 표지가 아니다.** 처음엔
# 목록 표지로 봤는데 개수 분포가 아니라고 말해 줬다.
CHOICE = "{06:"


def unpack(data: bytes) -> bytes | None:
    """LZSS 로 눌린 것이면 푼다. 아니면 None.

    필드와 같은 규칙이다 — 선두 u32 가 **눌린 스트림의 길이**다. 그 값이
    파일 크기와 아귀가 맞을 때만 시도한다.
    """
    if len(data) < 8:
        return None
    length = int.from_bytes(data[:4], "little")
    if not 8 <= length + 4 <= len(data):
        return None
    try:
        out = FT.lzss_decode(data[4:4 + length])
    except (IndexError, ValueError):
        return None
    return out if len(out) > len(data) // 2 else None


def chunks(data: bytes, glyphs: GT.GlyphMap, source: str):
    """널로 끊어 조각마다 한 줄씩 낸다. **거르지 않는다.**"""
    start = 0
    while True:
        stop = data.find(b"\x00", start)
        if stop < 0:
            break
        raw = bytes(data[start:stop])
        offset, start = start, stop + 1
        if not 2 <= len(raw) <= 300:
            continue
        try:
            text = GT.decode(raw, glyphs, None)
        except (IndexError, ValueError):
            continue
        body = "".join(c for c in text if not c.isascii() or c.isalnum())
        if len(body) < 2:
            continue
        hits = sum(1 for c in body if c in COMMON)
        yield {
            "출처": source,
            "오프셋": offset,
            "바이트수": len(raw),
            "바이트": raw.hex(),
            # **제어 코드는 본문에 그대로 남긴다.** `{02}` `{06:27}` 처럼
            # 중괄호로 싸여 보이지만 지우거나 옮기면 게임이 깨진다. 번역할 때도
            # 한 글자도 바꾸지 않고 같은 자리에 둔다.
            "본문": text,
            "선택분기": int(CHOICE in text),
            "미인식": int("{g:" in text or "{b1:" in text),
            "흔한글자": round(hits / len(body), 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entry", type=int, help="이 목차 항목만")
    parser.add_argument("--min-common", type=float, default=0.0,
                        help="흔한글자 비율이 이 값 이상인 것만 (기본 0 = 전부)")
    parser.add_argument("--out", type=Path,
                        default=Path("work/glyph-audit/all-strings.tsv"))
    args = parser.parse_args()

    if not FT.BIN_PATH.exists():
        print(f"원본이 없다: {FT.BIN_PATH}", file=sys.stderr)
        return 2
    glyphs = GT.GlyphMap.load()

    rows: list[dict] = []
    targets = [e for e in PO.entries()
               if args.entry is None or e[0] == args.entry]
    print(f"목차 {len(targets)}개를 날것과 압축해제 양쪽으로 훑는다 …\n")
    packed = 0
    for number, (index, lba, size) in enumerate(targets, 1):
        data = PD.read_user(FT.BIN_PATH, lba, min(size, MAX_READ))
        rows.extend(chunks(data, glyphs, f"#{index}"))
        plain = unpack(data)
        if plain is not None:
            packed += 1
            rows.extend(chunks(plain, glyphs, f"#{index}:풀림"))
        if number % 30 == 0:
            print(f"   {number}/{len(targets)}   지금까지 {len(rows):,}줄")

    if args.min_common:
        before = len(rows)
        rows = [r for r in rows if r["흔한글자"] >= args.min_common]
        print(f"\n흔한글자 {args.min_common} 미만을 걸러 {before:,} -> {len(rows):,}줄")

    rows.sort(key=lambda r: (-r["흔한글자"], r["출처"], r["오프셋"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    holes = sum(r["미인식"] for r in rows)
    likely = sum(1 for r in rows if r["흔한글자"] >= 0.15)
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["출처"]] = by_source.get(r["출처"], 0) + 1

    print(f"\n뽑은 문자열 {len(rows):,}줄   (압축이 풀린 항목 {packed}개)")
    print(f"  못 읽는 글자가 섞인 줄   {holes:,}")
    print(f"  일본어 문장다운 줄       {likely:,} (흔한글자 15% 이상)")
    print("\n줄이 많은 출처 상위 12")
    for source, count in sorted(by_source.items(), key=lambda x: -x[1])[:12]:
        print(f"   {source:<14} {count:>7,}줄")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
