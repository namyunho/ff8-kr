#!/usr/bin/env python3
"""전투 캐릭터 이름 글꼴을 디스크 사본에 써넣는다.

## 어디에 있는가

전투 화면의 이름(`キスティス` 등)은 낱글자 조립이 아니라 **통짜 이름 그림**으로
VRAM 에 얹힌다. 그 그림을 만드는 글꼴은 뱅크0 도 TOC#131 시트도 아닌 **제3의
일본어 가나 시트**이고, 전투 진입 때마다 디스크에서 다시 실린다.

찾는 데 오래 걸렸다. 디스크에 평문으로 없고(732MB 전수 검색, 니블 뒤집기·바이트
역순·16비트 스왑까지), IMG 목차 134개의 LZSS 블록도 아니고, 아카이브 하위
항목도 아니었다. **IMG 목차가 가리키는 것은 IMG 636MB 의 극히 일부**라는 것이
함정이었다 — 목차를 무시하고 전 섹터를 훑어야 나왔다.

    LBA 111,617      [u32 압축크기=103,238][LZSS]   51섹터
      해제 171,164B  TIM 4개 (0x14, 0x9be0, 0xc904, 0x183ec)
      0x183ec        전투 이름 글꼴 TIM 17,184B
        CLUT   VRAM (256,224)  16x16   — 팔레트 16벌
        이미지 64halfword x 132행 = 256x132 px 4bpp
        런타임 0x801b8cec 에 얹힌다 (픽셀은 +12바이트 헤더 뒤)

## 크기 제약

재압축이 원본보다 작아야 한다. `lzss.py` 의 최적 파싱이 101,324B 로 줄여
50섹터에 들어간다(원본 51섹터). 남는 꼬리는 게임이 선두 u32 만큼만 풀고
멈추므로 읽지 않는다 — `patch_disc.py` 와 같은 규칙이다.

    python3 scripts/patch_battle_font.py --dry-run
    python3 scripts/patch_battle_font.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import lzss                                 # noqa: E402
import patch_disc as PD                     # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "work" / "analysis" / "battle-font"
CONTAINER_LBA = 111_617
TIM_OFFSET = 0x183EC
ORIGINAL_SECTORS = 51


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tim", type=Path, default=ART / "battle-name-font-patched.tim")
    parser.add_argument("--expect", type=Path, default=ART / "battle-name-font.tim",
                        help="원본 TIM. 자리를 잘못 짚지 않았는지 확인한다")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for path in (args.tim, args.expect):
        if not path.exists():
            print(f"없는 파일: {path}", file=sys.stderr)
            return 2
    new_tim = args.tim.read_bytes()
    old_tim = args.expect.read_bytes()
    if len(new_tim) != len(old_tim):
        print(f"TIM 크기가 다르다: {len(new_tim):,} != {len(old_tim):,}", file=sys.stderr)
        return 1

    # 원본 디스크에서 읽는다. 사본에 이미 쓴 것 위에 또 쓰면 어긋난다.
    head = PD.read_user(FT.BIN_PATH, CONTAINER_LBA, 4)
    packed_size = struct.unpack_from("<I", head, 0)[0]
    raw = PD.read_user(FT.BIN_PATH, CONTAINER_LBA, packed_size + 4)
    plain = bytearray(FT.lzss_decode(raw[4:4 + packed_size]))
    print(f"LBA {CONTAINER_LBA:,}  압축 {packed_size:,}B -> 해제 {len(plain):,}B")

    at = TIM_OFFSET
    if plain[at:at + len(old_tim)] != old_tim:
        found = bytes(plain).find(old_tim)
        print(f"**{at:#x} 에 원본 TIM 이 없다.** 파일 안 위치: "
              f"{'못 찾음' if found < 0 else hex(found)}", file=sys.stderr)
        return 1
    plain[at:at + len(new_tim)] = new_tim
    print(f"  {at:#x} 의 TIM {len(new_tim):,}B 를 갈아 끼웠다")

    repacked = lzss.compress(bytes(plain))
    if FT.lzss_decode(repacked)[:len(plain)] != bytes(plain):
        print("**재압축이 무손실이 아니다**", file=sys.stderr)
        return 1
    total = len(repacked) + 4
    sectors = (total + 2047) // 2048
    print(f"  재압축 {len(repacked):,}B  ({sectors}섹터 / 배정 {ORIGINAL_SECTORS}섹터)")
    if sectors > ORIGINAL_SECTORS:
        print("**섹터를 넘는다.** 뒤 파일이 밀리므로 쓰지 않는다", file=sys.stderr)
        return 1

    blob = struct.pack("<I", len(repacked)) + repacked
    if args.dry_run:
        print("--dry-run 이라 쓰지 않았다")
        return 0
    PD.write_user(PD.PATCH_BIN, CONTAINER_LBA, blob)
    back = PD.read_user(PD.PATCH_BIN, CONTAINER_LBA, len(blob))
    if back != blob:
        print("**쓴 대로 안 읽힌다**", file=sys.stderr)
        return 1
    check = FT.lzss_decode(back[4:4 + len(repacked)])
    if check[at:at + len(new_tim)] != new_tim:
        print("**되읽어 해제하니 TIM 이 다르다**", file=sys.stderr)
        return 1
    print(f"  썼다. 되읽기·해제까지 일치 → {PD.PATCH_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
