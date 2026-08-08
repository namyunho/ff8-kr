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

## 왜 제자리에만 쓰는가

문자열은 섹션 안에서 **NUL 로 끊어 순서대로** 읽힌다 — 섹션 안 오프셋 표가
없다(u16 이 문자열 시작을 가리키는 횟수를 세 보면 0~23 으로 우연 수준이다).
그래서 길이가 달라져도 순서와 개수만 지키면 된다. **다만 섹션 크기가 바뀌면
파일 머리의 u32 오프셋 56개를 다시 써야 하고, 그때부터 위험이 커진다.**

그래서 기본은 **원래 크기 안에 들어가는 섹션만** 쓴다. 남는 자리는 NUL 로
메워 뒤 문자열의 시작이 안 밀리게 한다. 넘치는 섹션은 건드리지 않고
**목록으로 알려 준다** — 번역을 줄이거나 섹션을 옮기는 판단은 사람이 한다.

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


def rebuild(body: bytes, base: int, korean: dict[int, str],
            glyphs, bank1) -> tuple[bytes, int, int]:
    """섹션 하나를 다시 짠다. `(새 몸통, 바꾼 건수, 실패 건수)`."""
    out = bytearray()
    changed = failed = 0
    at = 0
    while at < len(body):
        end = body.find(b"\x00", at)
        if end < 0:
            out += body[at:]
            break
        original = body[at:end]
        text = strip_name(korean.get(base + at, ""))
        if original and text:
            try:
                out += GT.encode(text, glyphs, bank1)
                changed += 1
            except ValueError:
                out += original
                failed += 1
        else:
            out += original
        out += b"\x00"
        at = end + 1
    return bytes(out), changed, failed


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

    wrote = skipped = total_changed = total_failed = 0
    over: list[str] = []
    for index, (start, end) in enumerate(sections(bytes(raw))):
        body = bytes(raw[start:end])
        if not body:
            continue
        fresh, changed, failed = rebuild(body, start, korean, glyphs, bank1)
        if not changed:
            continue
        total_failed += failed
        if len(fresh) > len(body):
            over.append(f"#{index} {len(body):,}B 에 {len(fresh):,}B "
                        f"({len(fresh) - len(body):+,})  번역 {changed}건")
            skipped += 1
            continue
        # **남는 자리는 NUL 로 메운다.** 문자열 개수를 NUL 로 세는 코드가
        # 있으므로 개수가 늘면 안 된다 — 꼬리를 원본 그대로 두면 안전하다.
        raw[start:start + len(fresh)] = fresh
        raw[start + len(fresh):end] = b"\x00" * (end - start - len(fresh))
        wrote += 1
        total_changed += changed

    print(f"kernel.bin  LBA {lba}  {size:,}B")
    print(f"  제자리로 쓴 섹션 {wrote}개, 문자열 {total_changed:,}건")
    print(f"  인코딩 실패 {total_failed}건")
    if over:
        print(f"\n  **넘쳐서 건드리지 않은 섹션 {skipped}개** — 번역을 줄여야 한다")
        for line in over:
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
