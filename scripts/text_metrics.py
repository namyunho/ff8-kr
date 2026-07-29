#!/usr/bin/env python3
"""대사 한 건이 창에 들어가는지 재는 공용 자. 내보내기와 검사기가 함께 쓴다.

세 도구가 같은 계산을 따로 했고 둘 다 같은 곳에서 틀렸다. 여기로 모은다.

**줄을 끊는 것은 `{02}` 하나가 아니다.** `{01}` 은 창을 비우고 다음 쪽으로
넘긴다. 이것으로도 줄이 끊기므로 폭과 줄 수를 잴 때 함께 나눠야 한다. 나누지
않으면 쪽 경계를 넘는 글자가 한 줄로 합산돼 폭이 부풀었다 — 전수 9,160건에서
최대 659px / 302px 초과 303건이던 값이, 나누면 448px / 3건이 된다.

**`{b1:N}` 은 제어 코드가 아니라 글자다.** 필드 전용 폰트(뱅크1)의 글리프이며
화면에 실제로 그려진다. 아직 어떤 문자인지 못 읽었을 뿐이므로 폭 계산에서
빼면 안 된다. 폭 테이블이 필드마다 다르므로 여기서는 대체폭으로 센다.
"""

from __future__ import annotations

import re

# 원본 26,883줄을 `{01}` 까지 나눠 실측한 값이다. 99.9% 가 299px 이하이고
# 302px 를 넘는 줄은 3개뿐이며 셋 다 디버그·미판독 텍스트다. 창 폭 자체는
# 아직 확정하지 못했으므로 실측 상한을 그대로 쓴다.
LINE_PIXELS = 302
FALLBACK_WIDTH = 12                 # 폭 테이블에 없는 글리프의 진행폭
SINGLE_BYTE = 224                   # 인덱스 224 부터는 2바이트로 실린다

LINE_BREAKS = ("{02}", "{01}")      # 줄바꿈, 쪽 넘김
BANK1 = "{b1:"

TOKEN = re.compile(r"\{[^}]*\}")
BANK1_TOKEN = re.compile(r"\{b1:\d+\}")


def split_lines(text: str) -> list[str]:
    """줄을 끊는 코드로 모두 나눈다. 폭·줄 수의 기준이다."""
    pieces = [text]
    for mark in LINE_BREAKS:
        pieces = [part for piece in pieces for part in piece.split(mark)]
    return pieces


def control_codes(text: str) -> list[str]:
    """제어 코드만 순서대로. `{b1:N}` 은 글자이므로 뺀다."""
    return [token for token in TOKEN.findall(text)
            if not token.startswith(BANK1)]


def drawn(line: str) -> list[str]:
    """한 줄에서 실제로 그려지는 것들. 글자 하나 또는 `{b1:N}` 하나."""
    out: list[str] = []
    position = 0
    for token in BANK1_TOKEN.finditer(line):
        out.extend(TOKEN.sub("", line[position:token.start()]))
        out.append(token.group())
        position = token.end()
    out.extend(TOKEN.sub("", line[position:]))
    return out


def glyph_width(char: str, widths: list[int], index: int | None) -> int:
    """폭 테이블이 정본이다(`sub_8002E3EC`). 모르는 글리프는 대체폭."""
    if index is None or index >= len(widths):
        return FALLBACK_WIDTH
    return widths[index]


def line_pixels(text: str, widths: list[int], lookup) -> list[int]:
    """줄별 픽셀 폭. `lookup(char)` 이 글리프 인덱스 또는 None 을 준다."""
    return [sum(glyph_width(item, widths,
                            None if item.startswith("{") else lookup(item))
                for item in drawn(line))
            for line in split_lines(text)]


def byte_cost(text: str, lookup, cheap: set[str] | None = None) -> int:
    """이 문자열이 차지할 바이트. 배치를 모르면 한글을 2바이트로 본다.

    `cheap` 은 1바이트 자리(인덱스 0..223)에 놓기로 한 문자 집합이다.
    비워 두면 원본 폰트에 없는 문자를 전부 2바이트로 세는 보수적 추정이 된다.
    """
    total = 0
    for token in control_codes(text):
        total += 2 if ":" in token else 1
    for line in split_lines(text):
        for item in drawn(line):
            if item.startswith(BANK1):
                total += 2          # 0x1C..0x1F + 1바이트
                continue
            index = lookup(item)
            if index is not None:
                total += 1 if index < SINGLE_BYTE else 2
            else:
                total += 1 if cheap and item in cheap else 2
    return total


def line_count(text: str) -> int:
    """창에 찍히는 줄 수. **`{02}` 만 센다.**

    폭을 잴 때와 기준이 다르다. `{01}` 은 창을 비우고 다시 첫 줄부터 쓰므로
    폭 계산에서는 줄을 끊지만, 줄 수는 쪽마다 다시 세어 누적되지 않는다.
    `split_lines` 로 세면 `{01}` 을 쓰는 842건이 전부 줄이 는 것처럼 보인다.
    """
    return 1 + text.count(LINE_BREAKS[0])
