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
import text_metrics as TM                   # noqa: E402

GLYPHS = GT.GlyphMap.load()

# 이름은 대개 첫 줄에 홀로 오고 다음 줄이 「 로 시작한다.
SPEAKER = re.compile(r"^([^{「」]{1,12})\{02\}\s*「")
# `start` 는 「もりやのイベントテストページ」처럼 개발자 이름이 박힌
# 워프 메뉴다. `start0` 만 걸러서는 안 된다.
DEBUG_NAME = re.compile(r"^(test|debug|dummy|sample|gover|start)", re.I)
# 자리표시자 판정은 두 번 틀렸다. 표본을 보기 전에는 믿지 않는다.
#
#   1차 "제어 코드 없는 짧은 항목" → 표본 13건이 전부 진짜 대사였다
#      (「……はい」, 『エリクサー』を手にいれた!, (SeeDは魔女を倒す……))
#   2차 "라틴 소문자만 있는 줄" → 걸린 4건이 전부 진짜였다
#      (`ON/OFF` 토글, 지명 「FH」, 필드 983의 영어 가사)
#
# 남긴 것은 이름이 박힌 자리표시자와 숫자만 있는 줄뿐이다. 이 둘은 걸린 것을
# 전수로 눈으로 확인했다.
PLACEHOLDER = re.compile(r"^(だみー|ダミー|てすと|テスト|test|dummy|[0-9]+)$",
                         re.I)
DB_DEFAULT = Path("work/text/field-messages.json")

SPEC = """# 번역 지침

`NNN-이름.txt` 하나가 게임의 필드(장면) 하나다. 한 줄이 대사 한 건이고
`id<탭>원문` 꼴이다. 메시지는 **저장 순서이며 극 진행 순서가 아니다.**
앞뒤를 문맥으로 참고하되 순서 자체는 믿지 않는다.

## 내는 형식

`work/translate-draft/NNN-이름.tsv` 에 `id<탭>번역문` 을 한 줄에 하나씩 쓴다.
머리글도 주석도 쓰지 않는다. **번역문 안에 실제 개행이나 탭 문자를 넣지 않는다.**
줄바꿈은 글자가 아니라 아래 `{02}` 코드로 적는다. 번역하지 않는 항목은 그 줄을
아예 쓰지 않는다.

## 제어 코드는 하나도 건드리지 않는다

`{...}` 는 게임 제어 코드다. **개수·종류·순서가 원문과 완전히 같아야 한다.**
검사기가 기계로 대조하며 하나라도 어긋나면 그 대사는 못 쓴다.

횟수는 필드 9,160건을 전수로 센 값이다. **확정**은 게임 코드를 읽어 밝힌 것이고
(`sub_8002E4A0`, `sub_8002F73C`), **관찰**은 원문에서의 쓰임을 본 것뿐이다.
관찰 항목은 뜻을 믿지 말고 다루는 법만 따른다.

| 표기 | 횟수 | 뜻 | 다루는 법 |
|---|---|---|---|
| `{02}` | 16,610 | 줄바꿈 (확정) | 개수를 바꾸지 않는다. 한국어에서 줄을 나눌 자리로 옮기는 것은 된다 |
| `{06:XX}` | 3,600 | 파라미터를 하나 먹는 코드(확정). 관찰: **여닫는 쌍.** 1,564건이 정확히 2개씩 쓰고 `25`→`27` 이 1,433회다 | **쌍째로 옮긴다.** 원문이 감싼 말에 해당하는 한국어를 다시 감싼다 |
| `{03:XX}` | 1,171 | 파라미터 상위 니블로 갈라 이름·수치를 끼워 넣는다 (확정) | 한 낱말처럼 문장 안에 그대로 둔다 |
| `{01}` | 1,113 | 행 카운터를 1로 되돌린다 (확정). 화면에서는 `」{01}「` 처럼 대사 사이에서 다시 첫 줄부터 쓴다 | 자리를 그대로 둔다 |
| `{0E:XX}` | 568 | `sub_8002F6C4` 표를 참조해 문자열을 끼워 넣는다 (확정) | 한 낱말처럼 그대로 둔다 |
| `{09:XX}` | 333 | 파라미터를 하나 먹는 코드(확정). 관찰: `{09:30}… {09:30}…` 꼴로 말줄임과 같이 온다 | 자리를 그대로 둔다 |
| `{05:XX}` | 110 | 변수 삽입. `sub_8002C700(id)` 이 준 문자열이 들어간다 (확정) | 한 낱말처럼 그대로 둔다 |
| `{04:XX}` | 60 | 캐릭터·GF 이름 또는 숫자 표 조회 (확정) | 한 낱말처럼 그대로 둔다 |
| `{b1:N}` | 4,629 | **제어 코드가 아니라 글자다.** 필드 전용 폰트(뱅크1)라 아직 못 읽었다 | 문맥으로 메워 번역한다. **번역문에 남기면 안 된다** |

`{03:30}` 은 주인공 이름 자리다. `「あぁ、{03:30}」` → `「아, {03:30}」` 처럼
코드를 그대로 둔 채 옮긴다.

`{06:25}【向かって右の道路】{06:27}` 는 가운데를 강조한 것이다.
`{06:25}【오른쪽 도로】{06:27}` 처럼 **감싼 채로** 옮긴다. 두 코드를 한 자리에
몰아 놓거나 한쪽만 남기면 그 뒤 문장이 전부 강조된 채로 흐른다.

## 길이 — 한 줄에 한글 25자

대사창 폭은 정해져 있다. 원본 26,883줄을 재보니 99.9%가 299px 안에 들고 상한이
**302px** 다. 한글 한 글자를 12px 로 잡으면 **한 줄에 25자**다.

`{02}` 와 `{01}` 이 줄을 끊는다. 그 사이가 한 줄이다. 제어 코드 개수를 못 바꾸므로
**줄 수는 원문과 같다.** 한 줄이 넘칠 것 같으면 줄을 늘리지 말고 문장을 줄인다.

글자 수는 원문과 비슷하게 유지한다. 번역이 길어지면 필드가 섹터에 들어갈 여유가
그만큼 준다. 한 줄 25자 안에서 되도록 짧게 옮긴다.

## 쓸 수 있는 글자

**서로 다른 글자 하나가 폰트 칸 하나를 먹는다.** 뱅크0 은 882칸뿐이고 한글 음절이
전부 여기 들어가야 한다. 부호를 여러 벌 쓰면 그만큼 음절이 밀려난다.

부호는 아래에서만 고르고 **한 벌로 통일한다.**

{CHARS}

- 따옴표 `"` `'` 는 폰트에 없다. 대사 인용은 원본대로 `「」` 를 쓴다.
- **`【】` 는 뱅크0 에 없다.** 원문에 있어도 그대로 베끼지 말고 `『』` 로 바꾼다.
  원문의 `【】` 는 필드 전용 폰트에서 온 글자라 한국어판에서는 칸을 새로 내줘야 한다.
- 일본어 `、` `。` 대신 `,` `.` 를 쓴다. 두 벌을 섞으면 칸만 낭비한다.
- 라틴 소문자는 `e` 하나뿐이다(`SeeD` 때문에 있다). 다른 소문자는 못 쓴다.
- `※ + = % & ×` 는 원문이 그 자리에 쓸 때만 따라 쓴다.

## 고유명사

`work/text/glossary.csv` 를 따른다. 없는 이름을 새로 정했으면 그 이름을 파일마다
똑같이 쓴다. 같은 인물이 필드마다 다른 이름이 되면 안 된다.

## 번역하지 않는 것

같은 디렉터리의 `do-not-translate.csv` 에 오른 항목은 건너뛴다. `だみー`,
`てすと`, 숫자만 있는 줄처럼 **내용이 자리표시자인 것**만 담았다.

**짧다고 건너뛰지 않는다.** 제어 코드가 없는 항목이 전체의 10.3%(812건)나
되지만 표본을 확인해 보니 전부 진짜 대사였다 — 「……はい」, (う……),
『エリクサー』を手にいれた!, 「セントラ」 같은 지명. 길이나 제어 코드 유무로
가르면 대사를 삼킨다. **일본어로 읽히면 번역한다.**
"""


def placeholder_reason(text: str) -> str:
    """대사가 아니라 자리표시자로 보이면 그 이유를, 아니면 빈 문자열을 준다."""
    plain = TM.TOKEN.sub("", text).strip().strip("「」『』()")
    return "내용이 자리표시자" if PLACEHOLDER.match(plain) else ""


def usable_characters() -> str:
    """번역이 쓸 수 있는 비-한글 글자를 폰트에서 뽑아 적는다.

    손으로 적으면 대응표가 바뀔 때 지침만 남는다. 뱅크0 대응표가 정본이므로
    거기서 만든다. 한글 음절은 우리가 새로 넣을 것이라 여기 없다.
    """
    groups = [
        ("숫자", "0123456789"),
        ("라틴 대문자", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        ("라틴 소문자", "e"),
        ("문장 부호", ".,?!…:-/～・"),
        ("따옴 기호", "「」『』()"),
        ("그 밖", "+=%&※×、。"),
    ]
    lines = []
    for label, candidates in groups:
        have = "".join(char for char in candidates if char in GLYPHS.index)
        missing = "".join(char for char in candidates
                          if char not in GLYPHS.index)
        row = f"- {label} : `{have}`"
        if missing:
            row += f"  (폰트에 없음: `{missing}`)"
        lines.append(row)
    lines.append("- 공백 한 칸 (낱말을 띄어 쓴다)")
    return "\n".join(lines)


def prompt_sheet(field: int, name: str, entries: list[dict]) -> str:
    """번역기에 그대로 주는 압축 형식. `id<탭>원문` 한 줄에 한 건.

    JSON 워크시트는 검사와 집계에 필요한 값을 다 담느라 한 건에 200바이트쯤
    쓴다. 9,160건을 그대로 읽히면 대부분이 번역에 쓰이지 않는 토큰이다.
    번역에 실제로 필요한 것은 `id` 와 원문뿐이므로 이쪽만 따로 낸다.

    되받는 형식도 같은 모양(`id<탭>번역문`)이라 배우고 지킬 규칙이 하나다.
    """
    head = (f"# 필드 {field} {name} — {len(entries)}건\n"
            f"# 지침은 같은 디렉터리의 README.md 를 따른다.\n"
            f"# 한 줄이 한 건이다. id<탭>원문.\n")
    body = "".join(f"{entry['id']}\t{entry['ja']}\n" for entry in entries)
    return head + body


def speaker_of(text: str) -> str:
    found = SPEAKER.match(text)
    return found.group(1) if found else ""


def export(db_path: Path, out: Path, skip_debug: bool) -> int:
    document = json.loads(db_path.read_text(encoding="utf-8"))
    widths = DE.glyph_widths(FT.SYSFNT.read_bytes())
    out.mkdir(parents=True, exist_ok=True)

    manifest, files, total, holes = [], 0, 0, 0
    skip = ["field,id,ja,reason"]
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
            reason = placeholder_reason(text)
            if reason:
                skip.append(f'{field["field"]},{entry["id"]},'
                            f'"{text}",{reason}')
                continue
            entries.append({
                "id": entry["id"],
                "ja": text,
                "ko": "",
                "note": "",
                "speaker": speaker_of(text),
                "byte_budget": entry["byte_budget"],
                "lines": entry["lines"],
                "line_pixels": max(
                    TM.line_pixels(text, widths, GLYPHS.index.get), default=0),
            })
            holes += text.count("{b1:")
        if not entries:
            continue
        stem = f"{field['field']:03d}-{name}"
        path = out / f"{stem}.json"
        path.write_text(json.dumps(
            {"field": field["field"], "name": name, "debug": debug,
             "count": len(entries), "entries": entries},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out / f"{stem}.txt").write_text(
            prompt_sheet(field["field"], name, entries), encoding="utf-8")
        manifest.append({"file": path.name, "field": field["field"],
                         "name": name, "debug": debug, "count": len(entries)})
        files += 1
        total += len(entries)

    (out / "README.md").write_text(
        SPEC.replace("{CHARS}", usable_characters()), encoding="utf-8")
    (out / "do-not-translate.csv").write_text("\n".join(skip) + "\n",
                                              encoding="utf-8")
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
