#!/usr/bin/env python3
"""한글 뱅크0 폰트를 원래 자리에 그대로 갈아 끼운다. **코드 패치가 없다.**

원본 폰트(IMG TOC #130)는 LBA 849 에 33,764바이트로 있다. 우리 뱅크0 도 같은
크기·같은 구조로 만들므로 그 자리에 덮어쓰기만 하면 된다. TOC 도, EXE 도,
훅도 건드리지 않는다.

## 왜 뱅크1 을 포기했는가

뱅크1 재활용은 실기에서 무너졌다. 뱅크1 이 놓이는 VRAM `(832,256)` 을
**동영상이 지운다.** 스톡에서는 필드에 들어갈 때 한자표가 다시 올라와 그
자리를 채우는데, 그 적재를 막는 것이 뱅크1 재활용의 전제였다. 즉 자리를
뺏는 순간 **되살릴 방법도 같이 없앤 것**이다.

옮겨 갈 안전한 VRAM 도 없었다. 예전에 비어 보이던 `x=448..831` 은 타이틀
화면 시점의 관측이고, 게임이 돌기 시작하면 차 있다.

대신 서브셋으로 간다. 828칸이 본문의 99.79% 를 덮고, 다시 써야 하는 것은
8,120건 중 264건(3.3%)이다. 그 대가로 **패치가 하나도 없는 상태**를 얻는다.

    python3 scripts/install_font_bank0.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_disc as PD                     # noqa: E402

FONT_LBA = 849              # IMG TOC #130
FONT_BYTES = 33_764         # 원본과 같아야 한다


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font", type=Path,
                        default=Path("work/font-bank0/bank00.bin"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.font.exists():
        print(f"없는 파일: {args.font}", file=sys.stderr)
        return 2
    if not PD.PATCH_BIN.exists():
        print(f"디스크 사본이 없다: {PD.PATCH_BIN}\n"
              "  python3 scripts/patch_disc.py --init", file=sys.stderr)
        return 2

    font = args.font.read_bytes()
    if len(font) != FONT_BYTES:
        print(f"{args.font} 가 {len(font):,}바이트다. 원본과 같은 "
              f"{FONT_BYTES:,}바이트여야 제자리에 들어간다.", file=sys.stderr)
        return 1

    print(f"폰트 {len(font):,}바이트를 LBA {FONT_LBA} 에 덮어쓴다 "
          f"({-(-len(font) // 2048)}섹터)")
    print("  TOC·EXE·훅 — 건드리지 않는다")
    if args.dry_run:
        print("\n--dry-run 이라 쓰지 않았다")
        return 0

    touched = PD.write_user(PD.PATCH_BIN, FONT_LBA, font)
    if PD.read_user(PD.PATCH_BIN, FONT_LBA, len(font)) != font:
        print("쓴 대로 안 읽힌다", file=sys.stderr)
        return 1
    print(f"  섹터 {touched}개를 썼다. 되읽기 일치")
    print(f"→ {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
