#!/usr/bin/env python3
"""필드 대사와 메뉴를 함께 세어 글리프 배치를 만든다.

`count_korean_syllables.py` 는 필드 텍스트만 받는다. 실제로 쓰는 배치는 메뉴까지
합친 것인데 그동안 즉석 코드로 만들어 재현할 방법이 없었다. 여기로 옮긴다.

## 1바이트 구간 224칸을 누가 쓰는가

인덱스 0~223 은 한 바이트로 실린다. 224 부터는 두 바이트다. 그래서 이 224칸을
어떻게 나누느냐가 곧 텍스트 크기다.

    못 박는 자리    영문자 페이지가 쓰는 55칸. **원래 글리프를 그대로 둔다**
    이름 음절       이름 입력 글자판이 쓸 음절. 글자판 한 줄이 **5바이트 고정**
                    이라 여기 있는 것만 글자판에 놓을 수 있다
    나머지          빈도순 한글

## 왜 못 박는가

이름 입력 화면의 영문자·숫자·기호 페이지를 손대지 않고 그대로 쓰려면 그
바이트들이 원래 글자를 가리켜야 한다. 빈도순 한글이 덮으면 `ABCDE` 가 한글
음절로 나온다.

    id130  'ABCDE'  ce cf d0 d1 d2   -> 인덱스 174~178

## 글자판 한 줄은 5바이트다

원본 36줄이 **예외 없이 정확히 5바이트**이고 쓰인 글리프가 전부 1바이트
구간이다. 빈칸은 `0x5f`(공백)로 메운다. 게임이 줄을 바이트로 센다는 뜻이므로
글자판에 2바이트 글자를 놓을 수 없다.

    id 62  'や ゆ よ'   b1 5f b3 5f b5
    id140  'Z    '     e7 5f 5f 5f 5f

    python3 scripts/build_layout_all.py --measure
    python3 scripts/build_layout_all.py
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                     # noqa: E402

TOKEN = re.compile(r"\{[^}]*\}")
SINGLE = 224                    # 여기부터 두 바이트
SLOTS = 1512                    # 21 x 18 셀 x 4 평면

# 이름 입력 화면의 영문자 페이지(서브1 그룹1 id130~152)가 쓰는 바이트다.
# `python3 scripts/build_layout_all.py --pins` 로 다시 뽑을 수 있다.
LATIN_PAGE = (
    "cecfd0d1d2", "d3d4d5d6d7", "d8d9dadbdc", "dddedfe0e1", "e2e3e4e5e6",
    "e75f5f5f5f", "5354555657", "58595a5b5c", "e8e9eaebec", "eeeff0f1ed",
    "fafbfcfd5f", "f2f3f4f55f",
)


def pinned(japanese: GT.GlyphMap) -> dict[int, str]:
    """제자리에 둘 인덱스 -> 원래 글자."""
    out: dict[int, str] = {}
    for row in LATIN_PAGE:
        for k in range(0, len(row), 2):
            index = int(row[k:k + 2], 16) - 32
            char = japanese.char.get(index)
            if char:
                out[index] = char
    return out


def counts(field_root: Path, menu: Path,
           extra: list[Path] | None = None) -> collections.Counter:
    """번역문에 실제로 그려지는 글자를 센다.

    **화면에 글자를 내보내는 자료는 빠짐없이 여기에 넣는다.** 한때 필드와
    메뉴만 세다가 `kernel.bin`(아이템·마법·GF어빌리티·전투커맨드·결과창,
    그리고 **이름을 못 바꾸는 캐릭터의 이름**)을 빠뜨려, 그 번역이 쓰는 음절
    47자가 배치에 없었다. 빠뜨린 자료는 넣는 날 조용히 깨진다.
    """
    tally: collections.Counter = collections.Counter()
    for path in sorted(field_root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document.get("entries", []):
            tally.update(TOKEN.sub("", entry.get("ko", "")))
    if menu.exists():
        for row in json.loads(menu.read_text(encoding="utf-8")):
            tally.update(TOKEN.sub("", row.get("ko", "")))
    for path in extra or []:
        if not path.exists():
            print(f"  경고: {path} 가 없다 — 그 자료의 음절이 배치에서 빠진다",
                  file=sys.stderr)
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            tally.update(TOKEN.sub("", row.get("ko", "") or ""))
    return tally


BANK0 = 756                     # 슬롯 756 부터는 뱅크1 이다

# **절대 쓰면 안 되는 18칸.** 메뉴 항목 이름은 텍스처 폰트가 아니라 EXE 안의
# 벡터 폰트 루틴(`0x8002c540`)이 그린다. 그 루틴은 표 `0x800528a8` 에서 글자의
# 파트 수를 상위 16비트로 읽어 그만큼 20바이트 프리미티브를 찍는데, 색인
# 484~511 자리는 쓰레기 항목이라 파트 수가 49,312 로 나온다. 한 글자에 약 1MB 다.
#
#     8002c5e8  addiu v1, v1, 0xe0     색인 = 둘째바이트 + 448  (선두 0x19)
#     8002c60c  srl   t1, v0, 16       파트 수를 그대로 믿는다
#     8002c6a0  addiu a1, a1, 0x14     **경계 검사가 없다**
#
# 두 글자면 RAM 2MB 를 넘겨 0 으로 되감기고 BIOS 예외 벡터를 깔아뭉갠다. 그
# 뒤로는 인터럽트마다 쓰레기를 실행해 「없는 명령」 예외가 무한 반복된다 —
# 아이템·마법 메뉴가 멈추던 것이 이것이다. `つかう` 를 `사용` 으로 옮겼는데
# `용` 이 하필 색인 228 이었다.
#
# 색인 256 이상은 둘째 바이트가 0x40 을 넘어 `v1 >= 512` 가 되고, 루틴이
# 스스로 빠져나가므로 안전하다. 위험한 것은 이 18칸뿐이다.
VECTOR_BOMB = frozenset({228, 230, 232, 234, 236, 238, 240, 241, 242,
                         244, 246, 248, 250, 251, 252, 253, 254, 255})


def build(tally: collections.Counter, pins: dict[int, str],
          keyboard: list[str], menu_chars: set[str]) -> list[str]:
    """배치를 만든다. 세 가지를 지킨다.

    1. 못 박은 자리는 비켜 간다
    2. 이름 음절은 **1바이트 구간**에 — 글자판 한 줄이 5바이트 고정이다
    3. 메뉴가 쓰는 글자는 **뱅크0** 에 — 메뉴를 그리는 오버레이가 뱅크 비트를
       안 더한다. 뱅크1 글자를 쓰면 그 글자만 엉뚱한 평면으로 나온다
       (「기본값」의 `값` 이 깨져 보이던 것이 이것이다)
    """
    taken = set(pins.values())
    ordered = [c for c, _ in tally.most_common() if c not in taken]
    need = [c for c in keyboard if c not in taken]
    menu_only = [c for c in ordered if c in menu_chars and c not in need]

    free_single = [i for i in range(SINGLE) if i not in pins]
    if len(need) > len(free_single):
        raise ValueError(f"이름 음절 {len(need)}자가 1바이트 빈자리 "
                         f"{len(free_single)}칸을 넘는다")

    chars: dict[int, str] = dict(pins)
    placed = set(taken)

    def fill(slots, queue):
        for index in slots:
            while queue and queue[0] in placed:
                queue.pop(0)
            if not queue:
                return
            chars[index] = queue.pop(0)
            placed.add(chars[index])

    rest = [c for c in ordered if c not in need and c not in menu_only]
    fill(free_single, need + menu_only + rest[:])
    # 뱅크0 의 남은 자리: 아직 안 놓인 메뉴 글자를 먼저 넣는다
    free_bank0 = [i for i in range(SINGLE, BANK0)
                  if i not in pins and i not in VECTOR_BOMB]
    queue = ([c for c in menu_only if c not in placed]
             + [c for c in ordered if c not in placed])
    fill(free_bank0, queue)
    left = [c for c in ordered if c not in placed]
    if left:
        fill(range(BANK0, SLOTS), left)

    top = max(chars) + 1
    return [chars.get(i, " ") for i in range(top)]


def cost(tally: collections.Counter, chars: list[str]) -> tuple[int, int]:
    """그려지는 글자의 총 바이트와, 1바이트로 실리는 글자 수."""
    index = {c: i for i, c in enumerate(chars)}
    total = single = 0
    for char, n in tally.items():
        i = index.get(char)
        if i is None:
            total += 2 * n
        elif i < SINGLE:
            total += n
            single += n
        else:
            total += 2 * n
    return total, single


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fields", type=Path, default=Path("work/translate"))
    parser.add_argument("--menu", type=Path,
                        default=Path("work/text/menu-messages.json"))
    parser.add_argument("--names", type=Path,
                        default=Path("data/nameable-entities.json"))
    # **정본(data/glyph-layout.json)을 절대 기본값으로 두지 않는다.** 배치는
    # 디스크 1~4 가 공유하는 계약이라, 빈도표가 바뀔 때마다 재생성되면 이미
    # 인코딩한 텍스트가 전부 어긋난다. 이 도구는 '제안'만 내고, 정본 반영은
    # 사람이 diff 를 보고 결정한다. `scripts/verify_layout.py` 가 관문이다.
    parser.add_argument("--output", type=Path,
                        default=Path("work/glyph-layout-proposed.json"))
    # 화면에 글자를 내보내는 다른 자료. 빠지면 그 음절이 배치에 없다.
    parser.add_argument("--also", type=Path, nargs="*",
                        default=[Path("work/text/kernel-text-ko.json")],
                        help="함께 셀 번역 JSON (기본: kernel)")
    parser.add_argument("--measure", action="store_true",
                        help="현재 배치와 견주기만 하고 쓰지 않는다")
    args = parser.parse_args()

    japanese = GT.GlyphMap.load()
    pins = pinned(japanese)
    names = json.loads(args.names.read_text(encoding="utf-8"))
    keyboard: list[str] = []
    words = ([names["korean"][n] for n in names["order"]]
             + list(names.get("extra", {}).get("items", [])))
    for word in words:
        for char in word:
            if char not in keyboard:
                keyboard.append(char)

    tally = counts(args.fields, args.menu, args.also)

    # **고정폭 슬롯은 어림으로 재지 말고 실제로 인코딩해 본다.**
    #
    # 처음엔 「한국어를 전부 1바이트로 봐도 여유가 2바이트 미만인 줄」만
    # 보호했다. 두 번 새 나갔다 — 제어 토큰(`{0A:27}`)이 먹는 바이트를 안 셌고,
    # 배치가 바뀌면 어느 음절이 2바이트가 되는지도 달라진다.
    #
    # 그래서 배치를 만들고 → **삽입기가 쓰는 인코더로 실제로 재고** → 넘친
    # 줄의 음절을 1바이트 구간에 못 박고 → 다시 만든다. 넘치는 줄이 없을
    # 때까지 되풀이한다. 필요한 만큼만 보호하므로 본문이 덜 늘어난다.
    import insert_menu_text as IM
    import patch_disc as PD
    import tempfile

    strict = []
    if args.menu.exists():
        for row in json.loads(args.menu.read_text(encoding="utf-8")):
            if row.get("sub") == 1 and row.get("group") in IM.STRICT_GROUPS \
                    and (row.get("ko") or "").strip():
                strict.append(row)

    def overflowing(candidate: list[str]) -> list[dict]:
        """이 배치로 실제 인코딩해서 자리를 넘는 줄을 고른다."""
        if not strict:
            return []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump({"chars": candidate, "single_byte_limit": SINGLE},
                      handle, ensure_ascii=False)
            probe = Path(handle.name)
        try:
            glyphs, bank1, _ = PD.korean_map(probe)
        finally:
            probe.unlink(missing_ok=True)
        bad = []
        for row in strict:
            try:
                room = len(GT.encode(row.get("ja", ""), japanese, None))
                if len(GT.encode(row["ko"], glyphs, bank1)) > room:
                    bad.append(row)
            except Exception:                                # noqa: BLE001
                continue
        return bad

    # **뱅크0 에 먼저 넣어야 하는 글자.** 메뉴를 그리는 오버레이는 뱅크 비트를
    # 안 더하므로 뱅크1(게임 인덱스 >= 882) 글자는 그것만 엉뚱한 평면으로 나온다.
    # 메뉴만 챙기고 **kernel 을 빠뜨렸다가 `닉`(피닉스의 꼬리)을 비롯한 104자가
    # 뱅크1 로 가 화면에서 깨졌다.** kernel 은 아이템·마법·어빌리티·전투커맨드·
    # 전투결과를 같은 오버레이가 그린다 — 같은 제약을 받는다.
    menu_chars: set[str] = set()
    if args.menu.exists():
        for row in json.loads(args.menu.read_text(encoding="utf-8")):
            menu_chars.update(TOKEN.sub("", row.get("ko", "")))
    only_menu = len(menu_chars)
    for path in args.also or []:
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            menu_chars.update(TOKEN.sub("", row.get("ko", "") or ""))
    print(f"뱅크0 우선 {len(menu_chars)}종 (메뉴 {only_menu} + kernel 등)")

    chars = build(tally, pins, keyboard, menu_chars)
    for _ in range(8):
        bad = overflowing(chars)
        if not bad:
            break
        added = 0
        for row in bad:
            for char in TOKEN.sub("", row["ko"]):
                if char.strip() and char not in keyboard:
                    keyboard.append(char)
                    added += 1
        print(f"  고정폭 {len(bad)}줄이 넘쳐 음절 {added}자를 1바이트 구간에 못 박고 다시 만든다")
        if not added:
            print("  **더 못 박을 음절이 없다 — 번역을 줄여야 한다**", file=sys.stderr)
            break
        chars = build(tally, pins, keyboard, menu_chars)

    print(f"못 박은 자리 {len(pins)}칸  이름 음절 {len(keyboard)}자")
    print(f"배치 {len(chars)}자 / {SLOTS}칸")
    total, single = cost(tally, chars)
    drawn = sum(tally.values())
    print(f"그려지는 글자 {drawn:,}자 -> {total:,}바이트 "
          f"(1바이트로 실리는 것 {single * 100 / drawn:.1f}%)")

    if args.output.exists():
        old = json.loads(args.output.read_text(encoding="utf-8"))["chars"]
        before, _ = cost(tally, old)
        delta = total - before
        print(f"지금 배치 {before:,}바이트 -> {total:,}바이트  "
              f"({delta:+,}바이트, {delta * 100 / before:+.2f}%)")

    missing = [c for c in keyboard if c not in chars]
    if missing:
        print(f"**이름 음절이 배치에 없다**: {''.join(missing)}", file=sys.stderr)
        return 1
    late = [c for c in keyboard if chars.index(c) >= SINGLE]
    if late:
        print(f"**이름 음절이 2바이트 구간에 있다**: {''.join(late)}",
              file=sys.stderr)
        return 1
    stray = [c for c in sorted(menu_chars)
             if c in chars and chars.index(c) >= BANK0]
    if stray:
        print(f"**메뉴 글자가 뱅크1 에 있다**: {''.join(stray)}", file=sys.stderr)
        return 1
    print(f"뱅크0 우선 {len(menu_chars)}종이 모두 뱅크0 안에 있다")

    if args.measure:
        print("\n--measure 라 쓰지 않았다")
        return 0
    args.output.write_text(json.dumps(
        {"note": "필드+메뉴 빈도순. 영문자 페이지 55칸은 제자리에 못 박았고 "
                 "이름 입력 글자판이 쓸 음절은 1바이트 구간에 두었다.",
         "banks": 2, "pins": len(pins), "chars": chars},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
