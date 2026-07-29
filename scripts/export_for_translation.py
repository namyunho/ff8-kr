#!/usr/bin/env python3
"""번역에 넘길 형식으로 필드 대사를 내보낸다.

`build_text_db.py` 가 만든 전수 DB 를 받아 **필드 하나가 파일 하나**가 되게
쪼갠다. 대사는 앞뒤 문맥이 있어야 제대로 번역되므로 메시지를 낱개로 흩지 않고
저장 순서대로 묶어 둔다.

각 메시지에 **지켜야 할 한계 세 가지**를 같이 적는다.

    byte_budget   원본 길이. 필드 전체가 배정 섹터에 들어가야 한다.
    lines         원본 줄 수. 넘으면 창 높이를 벗어난다.
    line_pixels   원본에서 가장 긴 줄의 픽셀 폭. 넘으면 창 밖으로 흘러넘친다.

폭은 고정이 아니라 폰트의 폭 테이블이 정한다(`sub_8002E3EC`). 한글 글리프의
폭은 아직 정해지지 않았으므로 여기서는 **원본이 실제로 쓴 폭**만 적는다.

제어 코드는 `ja` 안에 그대로 있다. 지우면 창이 깨진다. 자세한 규칙은 함께
내는 `README.md` 에 적는다.

    python3 scripts/export_for_translation.py work/translate
    python3 scripts/export_for_translation.py work/translate --skip-debug
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogue_editor as DE                # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402

GLYPHS = GT.GlyphMap.load()

TOKEN = re.compile(r"\{[^}]*\}")
# 이름은 대개 첫 줄에 홀로 오고 다음 줄이 「 로 시작한다.
SPEAKER = re.compile(r"^([^{「」]{1,12})\{02\}\s*「")
DEBUG_NAME = re.compile(r"^(test|debug|dummy|sample|gover|start0)", re.I)
DB_DEFAULT = Path("work/text/field-messages.json")

SPEC = """# 번역 지침

이 디렉터리의 `NNN-이름.json` 하나가 게임의 필드(장면) 하나다. 메시지는 저장
순서이며 **극 진행 순서가 아니다.** 앞뒤를 참고하되 순서 자체는 믿지 않는다.

## 채우는 곳

`ko` 칸만 채운다. 나머지는 손대지 않는다. `ja` 는 원문이고 기계로 유도한 값이라
고쳐도 반영되지 않는다.

## 제어 코드는 반드시 남긴다

`{...}` 는 게임 제어 코드다. **지우거나 순서를 바꾸면 대사창이 깨진다.**

| 표기 | 뜻 | 다루는 법 |
|---|---|---|
| `{02}` | 줄바꿈 | 번역문에서도 줄을 나눌 자리에 그대로 둔다 |
| `{01}` | 줄 초기화 | 위치를 그대로 둔다 |
| `{03:XX}` | 이름·수치 삽입 | **한 글자처럼** 취급해 문장 안에 그대로 둔다 |
| `{06:XX}` | 삽입/강조 | 위 항목과 같다 |
| `{0E:XX}` | 지명 등 삽입 | 위 항목과 같다 |
| `{b1:N}` | 아직 못 읽은 원문 글자 | 문맥으로 메워 번역한다. 번역문에는 남기지 않는다 |

`{03:30}` 같은 코드는 주인공 이름이 들어가는 자리다. 「あぁ、{03:30}」 는
"아, {03:30}" 처럼 코드를 그대로 둔 채 번역한다.

## 길이 제약

세 가지를 넘기면 안 된다. 넘겨야 말이 되면 `note` 에 적어 둔다.

- `byte_budget` — 원본 바이트 길이. 한글은 대체로 한 글자 2바이트로 잡는다.
- `lines` — 원본 줄 수. `{02}` 개수 + 1 이다.
- `line_pixels` — 원본에서 가장 긴 줄의 픽셀 폭. 창 폭의 실측 상한은 302px 다.

## 고유명사

`glossary.csv` 를 따른다. 없는 이름을 새로 정하면 `glossary-additions.csv` 에
적어 둔다. 같은 이름이 파일마다 달라지면 안 된다.

## 번역하지 않는 것

`translate` 가 `false` 인 항목과 `do-not-translate.csv` 에 오른 항목은 비워 둔다.
식별자나 빈 슬롯이라 번역하면 게임이 깨진다.
"""


def line_widths(text: str, widths: list[int]) -> list[int]:
    """제어 코드를 뺀 각 줄의 픽셀 폭. 폭 테이블이 정본이다."""
    out = []
    for line in text.split("{02}"):
        plain = TOKEN.sub("", line)
        total = 0
        for char in plain:
            index = GLYPHS.index.get(char)
            total += (widths[index] if index is not None and index < len(widths)
                      else DE.FALLBACK_WIDTH)
        out.append(total)
    return out


def speaker_of(text: str) -> str:
    found = SPEAKER.match(text)
    return found.group(1) if found else ""


def export(db_path: Path, out: Path, skip_debug: bool) -> int:
    document = json.loads(db_path.read_text(encoding="utf-8"))
    widths = DE.glyph_widths(FT.SYSFNT.read_bytes())
    out.mkdir(parents=True, exist_ok=True)

    manifest, files, total, holes = [], 0, 0, 0
    for field in document["fields"]:
        name = field["name"] or f"field{field['field']}"
        debug = bool(DEBUG_NAME.match(name))
        if debug and skip_debug:
            continue
        entries = []
        for entry in field["entries"]:
            if not entry["translate"]:
                continue
            text = entry["ja"]
            entries.append({
                "id": entry["id"],
                "ja": text,
                "ko": "",
                "note": "",
                "speaker": speaker_of(text),
                "byte_budget": entry["byte_budget"],
                "lines": entry["lines"],
                "line_pixels": max(line_widths(text, widths), default=0),
            })
            holes += text.count("{b1:")
        if not entries:
            continue
        path = out / f"{field['field']:03d}-{name}.json"
        path.write_text(json.dumps(
            {"field": field["field"], "name": name, "debug": debug,
             "count": len(entries), "entries": entries},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest.append({"file": path.name, "field": field["field"],
                         "name": name, "debug": debug, "count": len(entries)})
        files += 1
        total += len(entries)

    (out / "README.md").write_text(SPEC, encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(
        {"files": files, "messages": total, "entries": manifest},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"필드 {files}개 / 메시지 {total:,}건 → {out}")
    print(f"  아직 못 읽은 원문 글자 {{b1:N}} {holes:,}개 "
          f"(메시지당 평균 {holes / max(total, 1):.2f}개)")
    print(f"  지침 {out / 'README.md'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path, help="내보낼 디렉터리")
    parser.add_argument("--db", type=Path, default=DB_DEFAULT,
                        help="build_text_db.py 가 만든 JSON")
    parser.add_argument("--skip-debug", action="store_true",
                        help="test/debug 계열 필드를 뺀다")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"먼저 build_text_db.py 로 {args.db} 를 만든다.", file=sys.stderr)
        return 2
    return export(args.db, args.out, args.skip_debug)


if __name__ == "__main__":
    raise SystemExit(main())
