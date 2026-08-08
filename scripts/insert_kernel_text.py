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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", type=Path, default=CANON)
    parser.add_argument("--translation", type=Path, default=TRANSLATION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}", file=sys.stderr)
        return 2
    glyphs, bank1, _ = PD.korean_map(args.layout)
    korean = {row["offset"]: (row.get("ko") or "")
              for row in json.loads(args.translation.read_text(encoding="utf-8"))}

    lba, size = next((l, s) for i, l, s in OC.entries() if i == TOC_INDEX)
    raw = bytearray(PD.read_user(FT.BIN_PATH, lba, size))       # **원본에서 읽는다**

    total_changed = total_failed = total_long = 0
    per_section: list[str] = []
    for index, (start, end) in enumerate(sections(bytes(raw))):
        body = bytes(raw[start:end])
        if not body:
            continue
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
    print(f"\n  썼다. 되읽기 일치 → {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
