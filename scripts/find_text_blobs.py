#!/usr/bin/env python3
"""목차 전체를 훑어 **일본어 텍스트가 들어 있는 파일**을 찾아낸다.

## 왜 만드는가

필드 대사와 메뉴는 찾았지만 아이템·어빌리티·GF 이름과 설명, 그리고 **전투 중
대사**가 어디 있는지 모른다. 목차 134개를 손으로 열어 보는 대신 기계로 센다.

## 어떻게 판별하는가

FF8 인코딩은 `0x20~0xFF` 가 1바이트 글자, `0x19~0x1B`+1바이트가 2바이트 글자,
`0x00` 이 끝이다. 그래서 **널로 끊기는 짧은 조각이 줄줄이 이어지고, 그 조각이
전부 아는 글자로 풀리면** 텍스트 덩어리다.

무작위 자료나 코드는 이 조건을 거의 못 넘는다. 코드에는 `0x00` 이 많지만
그 사이가 아는 글자로 안 풀린다.

점수는 **아는 글자로 풀린 문자열의 비율과 개수**다. 압축된 파일은 안 걸리므로
(필드가 그렇다) 걸린 것만 믿고, 안 걸렸다고 없다고 단정하지 않는다.

## 쓰는 법

    python3 scripts/find_text_blobs.py
    python3 scripts/find_text_blobs.py --entry 24 --dump 40
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import patch_disc as PD                     # noqa: E402
import patch_overlay_clut as PO             # noqa: E402

MIN_RUN = 2                 # 글자 두 개 미만은 우연히 나온다
MIN_HITS = 12               # 이만큼은 나와야 텍스트 덩어리로 본다

# **「풀린다」는 것은 신호가 아니다.** 바이트 0x20~0xFF 가 전부 어떤 글자로든
# 대응되므로 아무 이진 자료나 글자로 풀린다. 처음 짠 판별기가 목차 134개 중
# 132개를 텍스트라고 했다.
#
# 진짜 신호는 **분포**다. 일본어 문장에는 흔한 가나가 몰려 나온다. 무작위
# 바이트에서는 이 글자들이 1/200 확률로 흩어질 뿐이다.
COMMON = set("のはをにがでとしいたんーるれаなこそつてきカルンイシトスラー、。")
MIN_COMMON = 0.18           # 흔한 글자가 이 비율은 되어야 문장이다


def runs(data: bytes, glyphs: GT.GlyphMap) -> tuple[int, int, float, list[str]]:
    """널로 끊어 조각을 만들고 **일본어답게 풀리는 것**을 센다.

    `(그럴듯한 조각, 시도한 조각, 흔한글자비율, 예시)` 를 돌려준다.
    """
    good = tried = 0
    chars = common = 0
    samples: list[str] = []
    start = 0
    while True:
        stop = data.find(b"\x00", start)
        if stop < 0:
            break
        chunk = data[start:stop]
        start = stop + 1
        if not 2 <= len(chunk) <= 120:
            continue
        tried += 1
        try:
            text = GT.decode(bytes(chunk), glyphs, None)
        except (IndexError, ValueError):
            continue
        if "{g:" in text or "{b1:" in text:
            continue                        # 못 읽는 글자가 섞이면 자료다
        body = "".join(c for c in text if not c.isascii() or c.isalnum())
        if len(body) < MIN_RUN:
            continue
        good += 1
        chars += len(body)
        common += sum(1 for c in body if c in COMMON)
        if len(samples) < 4:
            samples.append(text[:26])
    return good, tried, (common / chars if chars else 0.0), samples


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entry", type=int, help="이 목차 항목만 자세히 본다")
    parser.add_argument("--dump", type=int, default=0, help="예시를 몇 줄 볼까")
    parser.add_argument("--out", type=Path,
                        default=Path("work/glyph-audit/text-blobs.json"))
    args = parser.parse_args()

    if not FT.BIN_PATH.exists():
        print(f"원본이 없다: {FT.BIN_PATH}", file=sys.stderr)
        return 2
    glyphs = GT.GlyphMap.load()

    found = []
    entries = PO.entries()
    targets = [e for e in entries if args.entry is None or e[0] == args.entry]
    print(f"목차 {len(targets)}개를 훑는다 …\n")
    for index, lba, size in targets:
        data = PD.read_user(FT.BIN_PATH, lba, min(size, 4 << 20))
        good, tried, share, samples = runs(data, glyphs)
        if args.entry is None and (good < MIN_HITS or share < MIN_COMMON):
            continue
        print(f"#{index:<4} LBA {lba:>7,}  {size:>10,}B   "
              f"조각 {good:>6,}/{tried:<7,}   흔한글자 {share * 100:4.1f}%")
        for text in samples[:args.dump or 2]:
            print(f"        {text!r}")
        found.append({"entry": index, "lba": lba, "size": size,
                      "chunks": good, "tried": tried,
                      "common": round(share, 3)})

    print(f"\n텍스트가 들어 있어 보이는 항목 {len(found)}개")
    print("압축된 파일은 안 걸린다 — **안 걸렸다고 없다고 단정하지 않는다.**")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(found, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
