#!/usr/bin/env python3
"""메시지 JSON 의 `ja` 칸을 대응표로 채운다.

`ja` 는 원본에서 기계로 유도한 값이다. 손으로 고치는 칸은 `ko` 이며 이 도구는
`ko` 를 건드리지 않는다. 대응표가 나아지면 다시 돌려 `ja` 를 갱신한다.

**쓰기 전에 왕복을 검사한다.** `ja` 를 다시 바이트로 되돌려 원본과 한 바이트라도
다르면 아무것도 쓰지 않는다. 되돌아가지 않는 표기는 번역문 삽입에 쓸 수 없다.

    python3 scripts/fill_japanese.py work/text/opening-scenes.json
    python3 scripts/fill_japanese.py work/text/opening-scenes.json --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                     # noqa: E402


def messages(document: dict):
    for field in document["fields"]:
        for entry in field["entries"]:
            if entry.get("raw_hex"):
                yield field, entry


def fill(path: Path, write: bool) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    glyphs = GT.GlyphMap.load()

    decoded: list[tuple[dict, str]] = []
    problems: list[str] = []
    bank1 = 0
    for field, entry in messages(document):
        data = bytes.fromhex(entry["raw_hex"])
        text = GT.decode(data, glyphs)
        if GT.encode(text, glyphs) != data:
            problems.append(f"필드 {field['field']} 메시지 {entry['id']}")
            continue
        bank1 += text.count("{b1:")
        decoded.append((entry, text))

    print(f"메시지 {len(decoded) + len(problems)}건")
    print(f"  왕복 검사 통과 {len(decoded)}건, 실패 {len(problems)}건")
    print(f"  필드 전용 폰트(뱅크1) 글리프 {bank1}개 — 뱅크0 표의 대상이 아니다")
    for problem in problems[:10]:
        print(f"  실패: {problem}")

    if problems:
        print("왕복에 실패한 메시지가 있어 쓰지 않는다.")
        return 1
    if not write:
        print("--check 이므로 쓰지 않는다.")
        return 0

    changed = 0
    for entry, text in decoded:
        if entry.get("ja") != text:
            entry["ja"] = text
            changed += 1
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"`ja` {changed}건을 갱신했다 → {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="메시지 JSON")
    parser.add_argument("--check", action="store_true",
                        help="왕복만 검사하고 쓰지 않는다")
    args = parser.parse_args()
    return fill(args.path, write=not args.check)


if __name__ == "__main__":
    raise SystemExit(main())
