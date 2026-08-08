#!/usr/bin/env python3
"""번역한 `kernel.bin` 문자열을 디스크 사본에 써넣는다.

## 무엇이 들어 있나

`kernel.bin`(IMG TOC#129, LBA 831)은 `[개수=56][u32 오프셋]×56` 뒤로 56개
섹션이 이어진다. **텍스트 범주마다 자기 섹션**이다.

    #31 전투 커맨드    #32 마법        #34 어빌리티      #35 무기
    #37 **캐릭터 이름** #38 아이템 이름  #39 아이템 설명   #55 전투 결과

`#37`이 특히 중요하다 — **이름을 못 바꾸는 캐릭터**(젤·어바인·키스티스·셀피·
사이퍼·이데아·라그나·키로스·워드)의 이름이 여기 있다. 전투 화면의 이름도
이것을 쓴다.

## 자리를 절대 밀지 않는다

처음엔 섹션을 다시 짜서 짧아진 만큼 당겨 썼다. **캐릭터 이름이 통째로 공백이
됐다.** 구조체 섹션이 문자열을 **절대 오프셋으로 가리키기 때문이다**(파일
앞부분에 문자열 풀을 가리키는 u32 가 234개 있다). 당기는 순간 전부 어긋난다.

그래서 **문자열마다 원래 자리와 원래 NUL 위치를 그대로 둔다.**

    [한국어 바이트][공백 0x5f 로 메움][원래 자리의 NUL]

남는 자리를 NUL 로 메우면 **널 개수가 늘어** 문자열을 널로 세는 코드가 어긋난다
— 그래서 공백(`0x5f`)으로 메운다. `insert_menu_text.py` 가 같은 이유로 같은
방법을 쓴다. 자리보다 긴 번역은 **넣지 않고 세어서 알려 준다.**

    python3 scripts/insert_kernel_text.py --dry-run
    python3 scripts/insert_kernel_text.py
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT              # noqa: E402
import glyph_text as GT                      # noqa: E402
import patch_disc as PD                      # noqa: E402
import patch_overlay_clut as OC              # noqa: E402

TOC_INDEX = 129
CANON = Path("data/glyph-layout.json")
TRANSLATION = Path("work/text/kernel-text-ko.json")


def strip_name(text: str) -> str:
    """`이름 :: 설명` 으로 온 것은 설명만 쓴다.

    번역 단계에서 설명 문자열에 아이템 이름을 접두로 붙여 놓은 것이 105건
    있다. 원본은 설명만이라 그대로 쓰면 길이가 크게 넘친다.
    """
    return (text.split("::", 1)[1] if "::" in text else text).strip()


def sections(raw: bytes) -> list[tuple[int, int]]:
    count = struct.unpack_from("<I", raw, 0)[0]
    offsets = list(struct.unpack_from(f"<{count}I", raw, 4)) + [len(raw)]
    return [(offsets[i], offsets[i + 1]) for i in range(count)]


SPACE = 0x5F                    # 원본이 빈칸에 쓰는 글리프


def rebuild(body: bytes, base: int, korean: dict[int, str],
            glyphs, bank1) -> tuple[bytes, int, int, int]:
    """섹션 하나를 **자리마다 제자리에서** 갈아 끼운다.

    `(새 몸통, 바꾼 건수, 실패 건수, 길어서 못 넣은 건수)`.

    **섹션을 압축하지 않는다.** 처음엔 짧아진 만큼 당겨 쓰고 꼬리를 NUL 로
    메웠는데, 그러면 모든 문자열의 시작이 앞으로 밀린다. 구조체 섹션이
    **절대 오프셋으로 이름을 가리키므로**(파일 앞부분에 그런 u32 가 234개
    있다) 캐릭터 이름이 통째로 공백이 됐다.

    그래서 문자열마다 **원래 자리와 원래 NUL 위치를 그대로 둔다.** 짧아진
    나머지는 공백(`0x5f`)으로 메운다 — NUL 로 메우면 널 개수가 늘어 문자열을
    널로 세는 코드가 어긋난다(`insert_menu_text.py` 가 같은 이유로 그렇게 한다).
    """
    out = bytearray(body)
    changed = failed = toolong = 0
    at = 0
    while at < len(body):
        end = body.find(b"\x00", at)
        if end < 0:
            break
        room = end - at
        text = strip_name(korean.get(base + at, ""))
        if room and text:
            try:
                encoded = GT.encode(text, glyphs, bank1)
            except ValueError:
                failed += 1
                at = end + 1
                continue
            if len(encoded) > room:
                toolong += 1
            else:
                out[at:at + len(encoded)] = encoded
                out[at + len(encoded):end] = bytes([SPACE]) * (room - len(encoded))
                changed += 1
        at = end + 1
    return bytes(out), changed, failed, toolong


def split(body: bytes) -> tuple[list[bytes], int]:
    """섹션을 `문자열 목록` 과 `꼬리 정렬 패딩 길이` 로 가른다.

    글자를 담은 섹션 25개가 예외 없이 이 모양이다 —
    `s1\\0 s2\\0 … sN\\0` 뒤에 4바이트 정렬용 NUL 이 0~3개.
    """
    parts = body.split(b"\x00")
    pad = 0
    while parts and parts[-1] == b"":
        parts.pop()
        pad += 1
    return parts, max(0, pad - 1)      # 마지막 문자열의 NUL 은 패딩이 아니다


def repack(body: bytes, base: int, korean: dict[int, str],
           glyphs, bank1) -> tuple[bytes | None, int, int, int]:
    """섹션 하나를 **처음부터 다시 짠다.** 자리를 옮겨도 되는 경우다.

    `(새 몸통 또는 None, 바꾼 건수, 실패 건수, 모자란 바이트)`.

    ## 왜 옮겨도 되는가

    게임은 **N번째 문자열을 널을 N번 세어** 찾는다. 글자를 담은 섹션 25개가
    예외 없이 「섹션 시작부터 널로 이어 붙인 순열」이고, 번역 자료가 아는
    문자열 1,320개의 자리가 그 순열과 정확히 일치한다. 그러면 지켜야 할 것은
    **개수와 차례**지 절대 위치가 아니다 — 길이는 바꿔도 된다.

    ## 그래도 지키는 것

    - **섹션 크기**가 그대로다 (뒤 섹션도 파일 크기도 안 건드린다)
    - **널 개수**가 그대로다 (세는 코드가 안 밀린다)
    - **차례**가 그대로다
    - 꼬리 정렬 패딩 길이가 그대로다

    남는 자리는 **마지막 문자열 뒤에 공백(`0x5f`)** 으로 붙인다. 널로 메우면
    개수가 늘고, 마지막 널 뒤에 두면 없던 문자열이 하나 생긴다.

    ## 위험

    구조체 섹션이 문자열을 **절대 오프셋으로 가리키는 곳이 있으면** 그것만
    어긋난다(예전에 캐릭터 이름이 공백이 된 것이 이 증상이다). 참조가
    규칙적인 표를 이루지 않아 자료만으로는 못 가린다 — 그래서 `--repack` 은
    **섹션을 하나씩 켤 수 있게** 해 두었다. 실기로 가린다.
    """
    parts, pad = split(body)
    fresh: list[bytes] = []
    changed = failed = 0
    at = 0
    for piece in parts:
        text = strip_name(korean.get(base + at, ""))
        use = piece
        if piece and text:
            try:
                use = GT.encode(text, glyphs, bank1)
                changed += 1
            except ValueError:
                failed += 1
        fresh.append(use)
        at += len(piece) + 1

    budget = len(body) - pad
    need = sum(len(p) + 1 for p in fresh)
    if need > budget:
        return None, 0, failed, need - budget

    fresh[-1] += bytes([SPACE]) * (budget - need)      # 남는 자리는 끝에 공백으로
    out = b"".join(p + b"\x00" for p in fresh) + b"\x00" * pad

    # 지켜야 할 것을 그 자리에서 검산한다
    assert len(out) == len(body), "섹션 크기가 바뀌었다"
    assert out.count(0) == body.count(0), "널 개수가 바뀌었다"
    back, back_pad = split(out)
    assert len(back) == len(parts) and back_pad == pad, "문자열 개수·차례가 바뀌었다"
    return out, changed, failed, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", type=Path, default=CANON)
    parser.add_argument("--translation", type=Path, default=TRANSLATION)
    parser.add_argument("--dry-run", action="store_true")
    # **자리를 옮겨도 되는지는 실기로 가린다.** 그래서 섹션을 하나씩 켠다.
    # `--repack 34,38` 처럼 쓰고, `all` 이면 되는 섹션 전부.
    parser.add_argument("--repack", default="",
                        help="이 섹션은 제자리가 아니라 처음부터 다시 짠다 "
                             "(쉼표로 구분, 'all' 이면 되는 것 전부)")
    args = parser.parse_args()
    want = (set(range(64)) if args.repack.strip() == "all"
            else {int(x) for x in args.repack.replace(",", " ").split()})

    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}", file=sys.stderr)
        return 2
    glyphs, bank1, _ = PD.korean_map(args.layout)
    rows = json.loads(args.translation.read_text(encoding="utf-8"))
    korean = {row["offset"]: (row.get("ko") or "") for row in rows}
    # **다시 짜기는 글자 섹션에만 한다.** 구조체 섹션은 이진 자료라 널로 갈라
    # 이어 붙이면 안 되고, 남는 자리를 공백(0x5f)으로 메우면 값이 망가진다.
    # `--repack all` 은 「되는 섹션 전부」이지 「모든 섹션」이 아니다.
    textual = {row["section"] for row in rows if "section" in row}
    want &= textual

    lba, size = next((l, s) for i, l, s in OC.entries() if i == TOC_INDEX)
    raw = bytearray(PD.read_user(FT.BIN_PATH, lba, size))       # **원본에서 읽는다**

    total_changed = total_failed = total_long = 0
    per_section: list[str] = []
    repacked: list[str] = []
    for index, (start, end) in enumerate(sections(bytes(raw))):
        body = bytes(raw[start:end])
        if not body:
            continue
        if index in want:
            fresh, changed, failed, short = repack(body, start, korean, glyphs, bank1)
            if fresh is not None:
                raw[start:end] = fresh
                total_changed += changed
                total_failed += failed
                repacked.append(f"#{index}  다시 짬  넣음 {changed:>3}")
                continue
            repacked.append(f"#{index}  **{short}B 모자라 제자리로 되돌림**")
        fresh, changed, failed, toolong = rebuild(body, start, korean, glyphs, bank1)
        if not (changed or toolong or failed):
            continue
        assert len(fresh) == len(body), "제자리 삽입은 크기를 바꾸지 않는다"
        raw[start:end] = fresh
        total_changed += changed
        total_failed += failed
        total_long += toolong
        if toolong:
            per_section.append(f"#{index}  넣음 {changed:>3}  **자리보다 긴 것 {toolong:>3}**")

    print(f"kernel.bin  LBA {lba}  {size:,}B  (크기 불변 — 제자리 삽입)")
    print(f"  넣은 문자열 {total_changed:,}건")
    print(f"  자리보다 길어 못 넣은 것 {total_long:,}건, 인코딩 실패 {total_failed}건")
    if repacked:
        print("\n  다시 짠 섹션:")
        for line in repacked:
            print(f"    {line}")
    if per_section:
        print("\n  섹션별 남은 것:")
        for line in per_section:
            print(f"    {line}")
    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    PD.write_user(PD.PATCH_BIN, lba, bytes(raw))
    back = PD.read_user(PD.PATCH_BIN, lba, size)
    if back != bytes(raw):
        print("  **쓴 대로 안 읽힌다**", file=sys.stderr)
        return 1

    # 다시 짠 섹션은 **널 개수와 차례**가 목숨이다. 원본과 맞대어 검산한다.
    origin = PD.read_user(FT.BIN_PATH, lba, size)
    for index, (start, end) in enumerate(sections(back)):
        if index not in want:
            continue
        was, now = origin[start:end], back[start:end]
        a, apad = split(was)
        b, bpad = split(now)
        if (len(was), was.count(0), len(a), apad) != (len(now), now.count(0), len(b), bpad):
            print(f"  **#{index} 이 불변식을 깼다** — "
                  f"크기 {len(was)}->{len(now)} 널 {was.count(0)}->{now.count(0)} "
                  f"문자열 {len(a)}->{len(b)} 패딩 {apad}->{bpad}", file=sys.stderr)
            return 1
    if want:
        print(f"  다시 짠 섹션 {len(want)}개: 크기·널 개수·문자열 차례가 원본과 같다")
    print(f"\n  썼다. 되읽기 일치 → {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
