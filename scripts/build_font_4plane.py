#!/usr/bin/env python3
"""4중 인터리브 폰트 파일 한 벌을 만든다. 배치 파일이 곧 입력이다.

`build_hangul_font.py` 는 원래 뱅크를 파일로 하나씩 내는 도구였다. 4중
인터리브는 **텍스처 하나 + 팔레트 32벌 + 통합 폭표**라 형태가 다르다. 예전에는
즉석 코드로 만들었는데 그러면 다시 만들 방법이 남지 않는다. 여기로 옮긴다.

## 슬롯과 인덱스가 다르다

두 가지 번호가 돌아다닌다. 섞으면 폭이 통째로 밀리므로 이름을 나눠 쓴다.

    배치 슬롯   0 ~ 1511    우리가 실제로 채우는 자리. 뱅크당 756
    게임 인덱스 0 ~ 1763    게임의 인코딩 공간. 뱅크당 882

    게임인덱스 = (슬롯 // 756) * 882 + (슬롯 % 756)

뱅크마다 756~881 은 **가리킬 셀이 없어** 비워 둔다. 텍스처가 21x18=378칸
뿐이기 때문이다 (셀 하나에 글리프 넷 -> 1,512).

## 왜 378칸인가

팔레트 32벌을 **뱅크0 텍스처 사각형 안쪽**에 두기 때문이다. 원본 CLUT 아래
16줄 `(288,240)` 은 남의 CLUT 가 덮는 자리였다 — 실기에서 뱅크1 글자만
노이즈로 나왔다. 자세한 것은 `docs/font-analysis.md` 참고.

    텍스처   (960,256)  256 x 216
    CLUT     (960,472)  16 x 32

    python3 scripts/build_font_4plane.py
    python3 scripts/build_font_4plane.py --layout work/hangul-layout-all.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402

TEXTURE_VRAM = (960, 256)


def widths_by_index(chars: list[str],
                    cells: dict[str, list[list[int]]]) -> list[int]:
    """폭표를 **게임 인덱스 순서**로 편다. 안 쓰는 자리는 0 이다."""
    table = [0] * (BF.PER_BANK * 2)
    for slot, char in enumerate(chars):
        table[BF.game_index(slot)] = BF.advance(cells[char])
    return table


def build(chars: list[str], ttf: Path, size: int) -> tuple[bytes, dict]:
    if len(chars) > BF.PER_TEXTURE:
        raise ValueError(f"배치가 {len(chars)}자다. {BF.PER_TEXTURE}칸을 넘는다")
    cells = BF.rasterize(ttf, size, chars)
    ordered = [cells[c] for c in chars]
    data = BF.build_texture(ordered, widths_by_index(chars, cells),
                            *TEXTURE_VRAM, BF.clut32())
    stats = {
        "글자": len(chars),
        "칸": BF.PER_TEXTURE,
        "셀": BF.CELLS_USED,
        "크기": len(data),
        "섹터": (len(data) + 2047) // 2048,
    }
    return data, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", type=Path,
                        default=Path("work/hangul-layout-all.json"))
    parser.add_argument("--ttf", type=Path, default=BF.DEFAULT_TTF)
    parser.add_argument("--size", type=int, default=12)
    parser.add_argument("--output", type=Path,
                        default=Path("work/font-all/font.bin"))
    args = parser.parse_args()

    if not args.layout.exists():
        print(f"배치 파일이 없다: {args.layout}", file=sys.stderr)
        return 2
    document = json.loads(args.layout.read_text(encoding="utf-8"))
    chars = document["chars"] if isinstance(document, dict) else list(document)

    try:
        data, stats = build(chars, args.ttf, args.size)
    except (ValueError, FileNotFoundError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    for key, value in stats.items():
        print(f"  {key:<4} {value:>8,}")
    print(f"  텍스처  VRAM{TEXTURE_VRAM}  {BF.TEX_W}x{BF.TEX_H_USED}")
    print(f"  CLUT    VRAM{BF.CLUT_VRAM}  16x{BF.CLUT_ROWS}"
          f"  (테마 32 + 그림자 {BF.PLANES})")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
