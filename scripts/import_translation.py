#!/usr/bin/env python3
"""번역 결과를 워크시트 JSON 의 `ko` 칸에 되받는다.

번역은 다른 도구가 한다. 그쪽이 내는 것은 **`id<TAB>번역문` 한 줄씩**이고,
이 스크립트가 그것을 `work/translate/NNN-이름.json` 에 합친다.

TSV 로 받는 이유가 있다. 번역문 안의 줄바꿈은 실제 개행이 아니라 `{02}` 로
적히므로 **한 메시지는 반드시 한 줄**이다. 그래서 탭 구분이 깨지지 않는다.
JSON 을 통째로 다시 받으면 번역기가 `ja` 나 `byte_budget` 까지 건드릴 수 있어
원문이 조용히 오염된다. 여기서는 `ko` 말고는 아무것도 받지 않는다.

초안은 `work/` 안에 남는다. 내보내기를 다시 해도 번역이 날아가지 않도록
TSV 쪽을 원본으로 두고 필요할 때마다 다시 합친다.

    python3 scripts/import_translation.py work/translate-draft work/translate
    python3 scripts/import_translation.py work/translate-draft work/translate --dry-run
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402
import glyph_text as GT                     # noqa: E402

TOKEN = re.compile(r"\{[^}]*\}")
HANGUL = re.compile(r"[가-힣]")


SECTION = "=="
LONG = 100      # 이 길이부터 깨짐이 급증한다

SINGLE_HOW_TO = """# FF8 한국어화 — 남은 일 전부

두 부분이다. **1부는 새로 번역**하고 **2부는 걸린 것만 고친다.**
새로 번역할 것 {new}건, 고칠 자리 {fixes}건.

## 돌려줄 형식 — 두 부 모두 같다

```
== 필드이름 ==
0<탭>번역문
1<탭>번역문
```

- `== 필드이름 ==` 줄을 그대로 두고 그 아래 `id<탭>번역문` 을 한 줄에 하나씩
- **원문을 다시 적지 않는다.** id 와 번역문만
- 번역문 안에 실제 개행이나 탭 문자를 넣지 않는다. 줄바꿈은 `{{02}}` 로 적는다
- 번역하지 않는 항목은 그 줄을 쓰지 않는다
- 설명·요약·머리말을 붙이지 않는다
- `#` 로 시작하는 줄은 참고용이다. 돌려주지 않는다

## 2부에서 문제별로 할 일

| 문제 | 할 일 |
|---|---|
| 코드 누락·추가 | 제어 코드 `{{...}}` 의 **종류·개수·순서를 원문과 똑같이** 맞춘다. 원문의 화자 이름을 빠뜨렸으면 되살린다 |
| 구멍 남음 | `{{b1:N}}` 은 아직 못 읽은 원문 글자다. 문맥으로 메우고 **번역문에 남기지 않는다** |
| 줄 폭 초과 | 한 줄(=`{{02}}`/`{{01}}` 사이)이 **한글 25자**를 넘었다. 줄을 늘리지 말고 문장을 줄인다 |
| 줄 수 초과 | `{{02}}` 개수가 원문보다 많다 |
| 없는 글자 | 그 음절이 폰트에 안 들어간다. **뜻이 같은 다른 말로 바꿔** 그 글자를 피한다 |
| 폰트에 없는 음절 | 오타다. 맞는 글자로 고친다 |
| 긴 메시지 | 걸리진 않았지만 길다. 원문과 한 줄씩 대조한다 |

---

{spec}
---

{glossary}
---
"""


def read_reply(path: Path) -> tuple[dict[str, dict[int, str]], list[str]]:
    """번역 결과를 읽는다. 두 가지 모양을 다 받는다.

    파일 하나가 필드 하나면 파일 이름이 곧 필드다. 여러 필드를 한 파일에
    담아 오면 `== 필드이름 ==` 줄로 나눈다. 다른 AI 에게 꾸러미로 넘길 때는
    여러 필드가 한 답으로 돌아오므로 후자가 필요하다.

    깨진 줄은 버리지 않고 이유와 함께 돌려준다. 코드 울타리(```)와 주석은
    붙여 넣기 과정에서 섞여 들어오므로 조용히 걷어낸다.
    """
    fields: dict[str, dict[int, str]] = {}
    complaints: list[str] = []
    stem = path.stem
    last: int | None = None
    fenced = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip("\r").strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith("#"):
            continue
        if line.startswith(SECTION) and line.endswith(SECTION):
            stem, last = line.strip(SECTION).strip(), None
            continue
        if "\t" not in line:
            # 파일로 주고받으면 긴 번역문이 실제 개행으로 쪼개져 온다. 탭이
            # 없는 줄은 대개 **앞 줄의 뒷토막**이므로 이어 붙인다. 버리면
            # 그 대사가 문장 중간에서 잘린 채 게임에 들어간다.
            if last is not None and stem in fields:
                fields[stem][last] += line
                complaints.append(f"{path.name}:{number} 앞 줄에 이어 붙였다"
                                  f" ({stem} id {last}): {line[:40]}")
            else:
                complaints.append(f"{path.name}:{number} 탭이 없다: {line[:60]}")
            continue
        head, _, text = line.partition("\t")
        try:
            identifier = int(head.strip())
        except ValueError:
            complaints.append(f"{path.name}:{number} id 가 숫자가 아니다: {head[:20]}")
            continue
        rows = fields.setdefault(stem, {})
        if identifier in rows and rows[identifier] != text.strip():
            complaints.append(f"{path.name}:{number} {stem} id {identifier}"
                              f" 가 다른 내용으로 중복 — 뒤엣것을 쓴다")
        rows[identifier] = text.strip()
        last = identifier
    return fields, complaints


def merge_field(rows: dict[int, str], sheet: Path, label: str,
                dry_run: bool) -> tuple[int, list[str]]:
    """한 필드를 합친다. 워크시트에 없는 id 는 넣지 않고 알린다."""
    complaints: list[str] = []
    document = json.loads(sheet.read_text(encoding="utf-8"))
    known = {entry["id"] for entry in document["entries"]}
    for identifier in sorted(set(rows) - known):
        complaints.append(f"{label} id {identifier} 가 워크시트에 없다")

    filled = 0
    for entry in document["entries"]:
        text = rows.get(entry["id"])
        if text is None or not text.strip():
            continue
        entry["ko"] = text
        filled += 1
    if not dry_run:
        sheet.write_text(json.dumps(document, indent=2, ensure_ascii=False)
                         + "\n", encoding="utf-8")
    return filled, complaints


def sheets(target: Path) -> list[tuple[Path, dict]]:
    """워크시트를 파일 순서대로 읽는다."""
    out = []
    for path in target.glob("*.json"):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" in document:
            out.append((path, document))
    # 파일 이름이 아니라 **필드 번호** 순이다. 문자열로 정렬하면
    # "1001-tiyane3" 이 "161-bccent_1" 앞에 와서 장면 순서가 깨진다.
    out.sort(key=lambda row: row[1]["field"])
    return out


def propagate(target: Path, dry_run: bool) -> int:
    """같은 원문에는 같은 번역을 넣는다.

    전체 9,160건 중 **3,172건(34.6%)이 다른 곳에도 똑같이 있는 원문**이다.
    서로 다른 원문은 6,667종뿐이다. 카드 규칙 안내문 하나가 60곳에 그대로
    반복되는 식이라, 필드마다 따로 번역시키면 같은 문장이 60가지로 갈린다.

    이미 번역된 것을 원문 기준으로 모아 아직 빈 자리에 채운다. 번역량이 줄고
    표기가 필드마다 흔들리지 않는다. 같은 원문에 다른 번역이 이미 들어와
    있으면 **가장 많이 나온 쪽**을 쓰고 갈린 사실을 알린다.
    """
    documents = sheets(target)
    votes: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for _, document in documents:
        for entry in document["entries"]:
            if entry.get("ko", "").strip():
                votes[entry["ja"]][entry["ko"]] += 1

    memory = {ja: box.most_common(1)[0][0] for ja, box in votes.items()}
    split = {ja: box for ja, box in votes.items() if len(box) > 1}

    filled = 0
    for path, document in documents:
        touched = False
        for entry in document["entries"]:
            if entry.get("ko", "").strip():
                if entry["ko"] != memory[entry["ja"]]:
                    entry["ko"] = memory[entry["ja"]]
                    touched = True
                continue
            text = memory.get(entry["ja"])
            if text:
                entry["ko"] = text
                filled += 1
                touched = True
        if touched and not dry_run:
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8")

    print(f"같은 원문으로 채운 자리 {filled:,}건"
          + (" (시험 실행, 쓰지 않음)" if dry_run else ""))
    if split:
        print(f"  같은 원문인데 번역이 갈린 것 {len(split)}종 — 많은 쪽으로 맞췄다")
        for ja, box in sorted(split.items(), key=lambda r: -sum(r[1].values()))[:5]:
            picks = " | ".join(f"{ko}({n})" for ko, n in box.most_common(3))
            print(f"    {ja[:40]} → {picks}")
    return 0


# 뱅크0 에도 그 필드 원문에도 없는 글자를 갈아 끼우는 표.
# 대상은 전부 뱅크0 에 실재하고 모양이 같은 것만 골랐다.
SWAP = {
    "~": "～",          # ASCII 물결 → 전각. 같은 번역문이 이미 ～ 를 쓴다
    "·": "・",          # 가운뎃점
    "―": "-", "—": "-", "–": "-",
    "‘": "", "’": "", "'": "", "“": "", "”": "", '"': "",
}


def normalize(target: Path, dry_run: bool) -> int:
    """폰트가 못 그리는 글자를 뱅크0 에 있는 것으로 바꾼다.

    판정 기준이 두 겹이다. **뱅크0 에 있으면** 그대로 두고, 없더라도 **그
    필드의 원문이 쓰는 글자면** 필드 전용 폰트에서 오므로 그대로 둔다.
    둘 다 아닌 것만 바꾼다.

    라틴 소문자는 뱅크0 에 `e` 하나뿐이므로 대문자로 올린다. 표에 없는
    글자는 손대지 않고 알린다 — 모르는 것을 조용히 지우면 대사가 샌다.
    """
    glyphs = GT.GlyphMap.load()
    changed = collections.Counter()
    stuck = collections.Counter()
    touched = 0
    for path, document in sheets(target):
        native = {char for entry in document["entries"]
                  for char in TOKEN.sub("", entry["ja"])}
        def convert(run: str) -> str:
            """제어 코드 **바깥**의 글자만 손본다."""
            out = []
            for char in run:
                if (HANGUL.match(char) or char.isspace()
                        or char in glyphs.index or char in native):
                    out.append(char)
                elif char in SWAP:
                    changed[f"{char} → {SWAP[char] or '(지움)'}"] += 1
                    out.append(SWAP[char])
                elif char.isalpha() and char.upper() in glyphs.index:
                    changed[f"{char} → {char.upper()}"] += 1
                    out.append(char.upper())
                else:
                    stuck[char] += 1
                    out.append(char)
            return "".join(out)

        dirty = False
        for entry in document["entries"]:
            text = entry.get("ko") or ""
            if not text.strip():
                continue
            # `{b1:5}` 의 `b` 를 글자로 보면 `{B1:5}` 가 돼 코드가 깨진다.
            pieces, position = [], 0
            for token in TOKEN.finditer(text):
                pieces.append(convert(text[position:token.start()]))
                pieces.append(token.group())
                position = token.end()
            pieces.append(convert(text[position:]))
            fixed = "".join(pieces)
            if fixed != text:
                entry["ko"] = fixed
                touched += 1
                dirty = True
        if dirty and not dry_run:
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8")

    print(f"글자를 바꾼 메시지 {touched:,}건"
          + (" (시험 실행, 쓰지 않음)" if dry_run else ""))
    for label, count in changed.most_common():
        print(f"    {label}  {count:,}회")
    if stuck:
        head = " ".join(f"{c}({n})" for c, n in stuck.most_common(12))
        print(f"  바꿀 짝이 없어 그대로 둔 글자: {head}")
    return 0


def refresh_prompts(target: Path) -> int:
    """아직 번역이 안 된 항목만 남겨 프롬프트 시트를 다시 쓴다.

    이미 채워진 자리를 번역기에 또 보내면 그만큼이 낭비다. 되받고 전파한
    뒤 이것을 돌리면 다음 배치는 남은 것만 본다.
    """
    written = pending = 0
    for path, document in sheets(target):
        rest = [entry for entry in document["entries"]
                if not entry.get("ko", "").strip()]
        sheet = path.with_suffix(".txt")
        if not rest:
            sheet.unlink(missing_ok=True)
            continue
        head = (f"# 필드 {document['field']} {document['name']} — "
                f"남은 {len(rest)}건 / 전체 {document['count']}건\n"
                f"# 지침은 같은 디렉터리의 README.md 를 따른다.\n"
                f"# 한 줄이 한 건이다. id<탭>원문.\n")
        sheet.write_text(head + "".join(
            f"{entry['id']}\t{entry['ja']}\n" for entry in rest),
            encoding="utf-8")
        written += 1
        pending += len(rest)
    print(f"프롬프트 시트 {written}개 갱신 — 남은 메시지 {pending:,}건")
    return 0


HOW_TO = """# FF8 한국어화 초벌번역 — 꾸러미 {number} / {count}

이 파일 하나가 한 번 넘길 분량이다. 메시지 {messages}건, 필드 {fields}개.

**돌려줄 것은 번역뿐이다.** 아래와 똑같은 모양으로 답한다.

```
== 필드이름 ==
0<탭>번역문
1<탭>번역문
```

- `== 필드이름 ==` 줄을 그대로 두고 그 아래 `id<탭>번역문` 을 한 줄에 하나씩.
- **원문을 다시 적지 않는다.** id 와 번역문만.
- 번역문 안에 실제 개행이나 탭 문자를 넣지 않는다. 줄바꿈은 `{{02}}` 로 적는다.
- 번역하지 않는 항목은 그 줄을 쓰지 않는다.
- 설명·요약·머리말을 붙이지 않는다.

받은 답을 파일로 저장해 `work/translate-reply/` 에 두면 되받아진다.

---

{spec}
---

{glossary}
---

# 원문

"""


FIX_HOW_TO = """# FF8 한국어화 — 고칠 자리 {number} / {count}

검사기가 짚은 자리다. **번역을 새로 하는 것이 아니라 걸린 것만 고친다.**
메시지 {messages}건.

각 항목이 이렇게 온다.

```
== 필드이름 ==
# 12  [문제] 설명
#   원문: ...
#   지금: ...
12<탭>고친 번역문
```

`#` 로 시작하는 줄은 참고용이다. **`id<탭>고친 번역문` 줄만 돌려준다.**
고칠 필요가 없다고 판단하면 그 줄을 쓰지 않는다.

## 문제별로 할 일

| 문제 | 할 일 |
|---|---|
| 코드 누락·추가 | 제어 코드 `{{...}}` 의 **종류·개수·순서를 원문과 똑같이** 맞춘다. 원문에 있는 화자 이름을 빠뜨렸으면 되살린다 |
| 구멍 남음 | `{{b1:N}}` 은 아직 못 읽은 원문 글자다. 문맥으로 메워 옮기고 **번역문에 남기지 않는다** |
| 줄 폭 초과 | 한 줄(=`{{02}}`/`{{01}}` 사이)이 **한글 25자**를 넘었다. 줄을 늘리지 말고 문장을 줄인다 |
| 줄 수 초과 | `{{02}}` 개수가 원문보다 많다 |
| 일본어 남음 | 옮기다 만 가나·한자가 있다 |
| 없는 글자 | 그 음절이 폰트에 안 들어간다. **뜻이 같은 다른 말로 바꿔** 그 글자를 피한다 |

번역문 안에 실제 개행이나 탭을 넣지 않는다. 줄바꿈은 `{{02}}` 로 적는다.

---

{spec}
---

{glossary}
---

# 고칠 자리

"""


def single(target: Path, report: Path, out: Path) -> int:
    """남은 일을 **파일 하나**로 묶는다. 안 된 것과 고칠 것을 함께 담는다.

    꾸러미를 여러 개로 나누면 새 대화창을 그만큼 열어야 하고, 그때마다 지침을
    다시 읽혀야 한다. 남은 양이 한 번에 들어갈 만하면 하나로 주는 편이 낫다.
    디버그 필드는 뺀다 — 플레이어가 볼 수 없는 개발용 메뉴다.
    """
    pending: list[tuple[str, list[dict]]] = []
    for path, document in sheets(target):
        if document["debug"]:
            continue
        rest = [entry for entry in document["entries"]
                if not entry.get("ko", "").strip()]
        if rest:
            pending.append((path.stem, rest))

    rows: dict[tuple[int, str, int], dict] = {}
    if report.exists():
        found = json.loads(report.read_text(encoding="utf-8"))["findings"]
        for finding in found:
            if finding["kind"] == "메시지 예산 초과(참고)":
                continue
            key = (finding["field"], Path(finding["file"]).stem,
                   finding["id"])
            row = rows.setdefault(key, {"ja": finding["ja"],
                                        "ko": finding["ko"], "why": []})
            row["why"].append(f"{finding['kind']}: {finding['detail']}")

    spec = (target / "README.md").read_text(encoding="utf-8")
    head, marker, rest = spec.partition("## 제어 코드")
    spec = head.split("## 내는 형식")[0] + marker + rest
    glossary_path = Path("work/text/glossary.csv")
    glossary = ("# 고유명사\n\n```\n"
                + (glossary_path.read_text(encoding="utf-8")
                   if glossary_path.exists() else "(없음)")
                + "```\n")

    new = sum(len(items) for _, items in pending)
    parts = [SINGLE_HOW_TO.format(new=new, fixes=len(rows),
                                  spec=spec, glossary=glossary)]

    parts.append("\n# 1부 — 아직 번역 안 된 것\n")
    for stem, items in pending:
        parts.append(f"\n== {stem} ==")
        parts.extend(f"{entry['id']}\t{entry['ja']}" for entry in items)

    parts.append("\n\n# 2부 — 고칠 자리\n")
    seen = None
    for key in sorted(rows):
        _, name, identifier = key
        row = rows[key]
        if name != seen:
            parts.append(f"\n== {name} ==")
            seen = name
        for why in row["why"]:
            parts.append(f"# {identifier}  [{why}]")
        parts.append(f"#   원문: {row['ja']}")
        parts.append(f"#   지금: {row['ko']}")
        parts.append(f"{identifier}\t{row['ko']}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    print(f"{out}  {len(text):,}자 / {out.stat().st_size:,}B")
    print(f"  1부 새로 번역할 것 {new:,}건 (필드 {len(pending)}개)")
    print(f"  2부 고칠 자리 {len(rows):,}건")
    print("  디버그 필드는 뺐다. 답은 work/translate-reply/ 에 저장하면 된다.")
    return 0


def fix_bundle(target: Path, report: Path, out: Path, size: int) -> int:
    """검사기가 짚은 자리만 모아 밖에 넘길 꾸러미로 낸다.

    전수를 다시 돌릴 이유가 없다. 8,120건 중 걸린 것은 몇 백 건이고, 그
    자리에는 **무엇이 왜 걸렸는지**가 붙어 있어야 고칠 수 있다. 원문과 현재
    번역을 주석으로 같이 넣는다.
    """
    document = json.loads(report.read_text(encoding="utf-8"))
    rows: dict[tuple[int, str, int], dict] = {}
    for finding in document["findings"]:
        if finding["kind"] == "메시지 예산 초과(참고)":
            continue
        key = (finding["field"], Path(finding["file"]).stem, finding["id"])
        row = rows.setdefault(key, {"ja": finding["ja"], "ko": finding["ko"],
                                    "why": []})
        row["why"].append(f"{finding['kind']}: {finding['detail']}")

    # 긴 메시지는 걸리지 않았어도 넣는다. 깨짐이 길이에 몰려 있는데
    # (200자 이상 26%, 100~200자 4.7%, 그 아래 0.5%) 그 오류의 상당수는
    # `녕석`·`무서다고` 처럼 **정상 음절**이라 검사기가 못 잡는다. 즉 검사기는
    # 긴 메시지에서 과소 보고한다. 통과했다고 무사한 것이 아니다.
    for path, sheet in sheets(target):
        for entry in sheet["entries"]:
            text = entry.get("ko") or ""
            if len(TOKEN.sub("", text)) < LONG:
                continue
            key = (sheet["field"], path.stem, entry["id"])
            row = rows.setdefault(key, {"ja": entry["ja"], "ko": text,
                                        "why": []})
            row["why"].append(f"긴 메시지({len(TOKEN.sub('', text))}자): "
                              f"검사기가 못 잡는 오류가 섞였을 수 있다."
                              f" 원문과 한 줄씩 대조한다")
    if not rows:
        print("고칠 자리가 없다.")
        return 0

    spec = (target / "README.md").read_text(encoding="utf-8")
    head, marker, rest = spec.partition("## 제어 코드")
    spec = head.split("## 내는 형식")[0] + marker + rest
    glossary_path = Path("work/text/glossary.csv")
    glossary = ("# 고유명사\n\n```\n"
                + (glossary_path.read_text(encoding="utf-8")
                   if glossary_path.exists() else "(없음)")
                + "```\n")

    groups: list[list] = []
    load = 0
    for key in sorted(rows):
        if not groups or load >= size:
            groups.append([])
            load = 0
        groups[-1].append((key, rows[key]))
        load += 1

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("fix-*.md"):
        old.unlink()
    for number, group in enumerate(groups, 1):
        lines, seen = [], None
        for (_, name, identifier), row in group:
            if name != seen:
                lines.append(f"\n== {name} ==")
                seen = name
            for why in row["why"]:
                lines.append(f"# {identifier}  [{why}]")
            lines.append(f"#   원문: {row['ja']}")
            lines.append(f"#   지금: {row['ko']}")
            lines.append(f"{identifier}\t{row['ko']}")
        text = FIX_HOW_TO.format(number=number, count=len(groups),
                                 messages=len(group), spec=spec,
                                 glossary=glossary)
        (out / f"fix-{number:02d}.md").write_text(
            text + "\n".join(lines) + "\n", encoding="utf-8")

    print(f"고칠 자리 {len(rows)}건 → 꾸러미 {len(groups)}개 → {out}")
    kinds = collections.Counter(
        why.split("(")[0].split(":")[0]
        for row in rows.values() for why in row["why"])
    for kind, count in kinds.most_common():
        print(f"    {kind}: {count}건")
    print("  고친 답은 work/translate-reply/ 에 두고 되받는다.")
    return 0


def bundle(target: Path, out: Path, size: int) -> int:
    """남은 원문을 다른 도구에 넘길 크기로 잘라 낸다.

    번역을 밖에서 시킬 때는 **한 번에 붙여 넣을 분량**이 단위다. 필드 하나가
    1건부터 216건까지라 파일 개수로 자르면 못 쓴다. 메시지 수로 자르되
    **필드 번호 순서를 지켜** 자른다 — 번호가 가까운 필드는 게임에서도 붙어
    있는 장면이라 앞뒤 문맥이 살아 있어야 번역이 나아진다.

    지침과 용어집을 꾸러미마다 통째로 넣는다. 새 대화창에 이 파일 하나만
    붙여 넣으면 바로 일이 되게 하려는 것이다.
    """
    # 지침의 "내는 형식" 절은 필드 하나짜리 시트를 전제로 쓰였다. 꾸러미는
    # 머리말에서 형식을 따로 정하므로 그 절을 빼야 두 설명이 안 부딪힌다.
    spec = (target / "README.md").read_text(encoding="utf-8")
    head, marker, rest = spec.partition("## 제어 코드")
    spec = head.split("## 내는 형식")[0] + marker + rest

    glossary_path = Path("work/text/glossary.csv")
    glossary = ("# 고유명사\n\n```\n"
                + (glossary_path.read_text(encoding="utf-8")
                   if glossary_path.exists() else "(없음)")
                + "```\n")

    # 디버그 필드는 개발용 메뉴라 플레이어가 볼 수 없다. 번역해도 쓰이지
    # 않으면서 음절 칸만 먹으므로 뒤로 몰아 건너뛸 수 있게 한다.
    real: list[tuple[str, list[dict]]] = []
    debug: list[tuple[str, list[dict]]] = []
    for path, document in sheets(target):
        rows = [entry for entry in document["entries"]
                if not entry.get("ko", "").strip()]
        if rows:
            (debug if document["debug"] else real).append((path.stem, rows))
    if not real and not debug:
        print("남은 것이 없다.")
        return 0

    def cut(items: list[tuple[str, list[dict]]]) -> list[list]:
        groups: list[list] = []
        load = 0
        for item in items:                # sheets() 가 이미 필드 번호 순이다
            if not groups or load + len(item[1]) > size:
                groups.append([])
                load = 0
            groups[-1].append(item)
            load += len(item[1])
        return groups

    groups = [(g, False) for g in cut(real)] + [(g, True) for g in cut(debug)]
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("bundle-*.md"):
        old.unlink()
    for number, (group, is_debug) in enumerate(groups, 1):
        messages = sum(len(rows) for _, rows in group)
        body = "".join(
            f"\n== {stem} ==\n"
            + "".join(f"{entry['id']}\t{entry['ja']}\n" for entry in rows)
            for stem, rows in group)
        note = ("\n> **이 꾸러미는 개발용 디버그 필드다.** 플레이어가 볼 수 없는"
                " 메뉴이므로 건너뛰어도 된다.\n" if is_debug else "")
        text = HOW_TO.format(number=number, count=len(groups),
                             messages=messages, fields=len(group),
                             spec=spec, glossary=glossary)
        (out / f"bundle-{number:02d}.md").write_text(
            text.replace("\n---\n", note + "\n---\n", 1) + body,
            encoding="utf-8")

    live = sum(len(rows) for _, rows in real)
    dead = sum(len(rows) for _, rows in debug)
    print(f"꾸러미 {len(groups)}개 → {out}")
    print(f"  실제 대사 {live:,}건 (꾸러미 1~{len(cut(real))})")
    print(f"  디버그 필드 {dead:,}건 (나머지 꾸러미, 건너뛰어도 된다)")
    print(f"  하나에 최대 {size}건. 지침과 용어집이 꾸러미마다 들어 있다.")
    print("  받은 답은 아무 이름으로나 work/translate-reply/ 에 두고")
    print(f"  python3 scripts/import_translation.py work/translate-reply"
          f" {target}")
    return 0


def status(target: Path, batch_size: int) -> int:
    """아직 안 된 것을 센다. 도중에 끊겨도 이어서 하려면 필요하다.

    끝났는지는 TSV 가 있는지가 아니라 **워크시트의 `ko` 가 찼는지**로 본다.
    같은 원문 전파로 TSV 없이 채워지는 필드가 있기 때문이다. 배치는 파일
    개수가 아니라 메시지 수로 고르게 나눈다 — 필드 하나가 1건부터 216건까지라
    개수로 나누면 한쪽만 무거워진다.
    """
    total = done = 0
    todo: list[tuple[str, int]] = []
    for path, document in sheets(target):
        rest = sum(1 for entry in document["entries"]
                   if not entry.get("ko", "").strip())
        total += document["count"]
        done += document["count"] - rest
        if rest:
            todo.append((path.stem, rest))

    print(f"메시지 {total:,}건")
    print(f"  끝남 : {done:,}건 ({done / max(total, 1):.1%})")
    print(f"  남음 : 필드 {len(todo)}개 / {total - done:,}건")
    if not todo:
        return 0

    batches: list[list[tuple[str, int]]] = []
    load = 0
    for item in sorted(todo, key=lambda row: -row[1]):
        if not batches or load + item[1] > batch_size:
            batches.append([])
            load = 0
        batches[-1].append(item)
        load += item[1]
    print(f"\n메시지 {batch_size}건씩 나누면 배치 {len(batches)}개")
    for number, batch in enumerate(batches, 1):
        names = " ".join(stem for stem, _ in batch)
        print(f"  {number:>2} ({sum(c for _, c in batch):>4}건) {names}")
    return 0


def run(source: Path, target: Path, dry_run: bool) -> int:
    if source.is_dir():
        files = sorted(path for path in source.iterdir()
                       if path.suffix in (".tsv", ".txt", ".md"))
    else:
        files = [source]
    if not files:
        print(f"번역 결과 파일이 없다: {source}", file=sys.stderr)
        return 2

    collected: dict[str, dict[int, str]] = {}
    complaints: list[str] = []
    damaged: list[tuple[str, int, int]] = []
    font = BF.covered()
    for path in files:
        fields, said = read_reply(path)
        complaints.extend(said)
        # **파일 단위로 손상을 먼저 잰다.** 폰트가 못 담는 음절은 화면에서
        # 빈칸이 되고 거의 예외 없이 출력 사고다. 한 번은 이걸 20단계 뒤에야
        # 발견했다 — 18개 답장 중 4개에만 몰려 있었고 나머지는 완전히
        # 깨끗했다. 들어올 때 걸러야 뒤에서 헤매지 않는다.
        total = broken = 0
        for rows in fields.values():
            for text in rows.values():
                for char in TOKEN.sub("", text):
                    if HANGUL.match(char):
                        total += 1
                        broken += char not in font
        if broken:
            damaged.append((path.name, broken, total))
        for stem, rows in fields.items():
            collected.setdefault(stem, {}).update(rows)

    total, merged, missing = 0, 0, []
    for stem, rows in sorted(collected.items()):
        sheet = target / f"{stem}.json"
        if not sheet.exists():
            missing.append(stem)
            continue
        filled, said = merge_field(rows, sheet, stem, dry_run)
        total += filled
        merged += 1
        complaints.extend(said)

    if damaged:
        print("**받은 파일에 폰트가 못 담는 음절이 있다 — 출력 사고를 의심한다**")
        for name, broken, seen in damaged:
            print(f"    {name}  깨진 음절 {broken}개 / 한글 {seen:,}자"
                  f"  ({broken * 10000 // max(seen, 1)} / 만)")
        print("    다시 받는 편이 낫다. 검사기가 못 잡는 오류가 더 있다 —"
              " 깨진 음절은 정상 음절로 바뀐 것까지는 못 짚는다.")
    print(f"필드 {merged}개 / 메시지 {total:,}건 되받음"
          + (" (시험 실행, 쓰지 않음)" if dry_run else ""))
    if missing:
        print(f"  짝이 되는 워크시트가 없다 {len(missing)}개: "
              f"{', '.join(missing[:5])}")
    if complaints:
        print(f"  걸린 줄 {len(complaints)}건")
        for line in complaints[:10]:
            print(f"    {line}")
        if len(complaints) > 10:
            print(f"    … 외 {len(complaints) - 10}건")
    return 1 if complaints or missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="TSV 파일 또는 디렉터리")
    parser.add_argument("target", type=Path, help="번역 내보내기 디렉터리")
    parser.add_argument("--dry-run", action="store_true",
                        help="합치지 않고 검사만 한다")
    parser.add_argument("--status", action="store_true",
                        help="아직 번역이 안 된 필드를 배치로 나눠 보여준다")
    parser.add_argument("--batch-size", type=int, default=350,
                        help="배치 하나의 메시지 수 (기본 350)")
    parser.add_argument("--propagate", action="store_true",
                        help="같은 원문에 같은 번역을 채운다")
    parser.add_argument("--refresh-prompts", action="store_true",
                        help="남은 항목만 남겨 프롬프트 시트를 다시 쓴다")
    parser.add_argument("--single", type=Path, metavar="파일",
                        help="남은 일을 파일 하나로 묶는다")
    parser.add_argument("--fix-bundle", type=Path,
                        help="검사 보고서(JSON)가 짚은 자리만 꾸러미로 낸다")
    parser.add_argument("--report", type=Path,
                        default=Path("work/translate-check.json"),
                        help="check_translation.py --report 가 낸 JSON")
    parser.add_argument("--normalize", action="store_true",
                        help="폰트가 못 그리는 글자를 뱅크0 에 있는 것으로 바꾼다")
    parser.add_argument("--bundle", type=Path,
                        help="남은 원문을 밖에 넘길 꾸러미로 잘라 낸다")
    parser.add_argument("--bundle-size", type=int, default=300,
                        help="꾸러미 하나의 메시지 수 (기본 300)")
    args = parser.parse_args()
    if not args.target.is_dir():
        print(f"디렉터리가 아니다: {args.target}", file=sys.stderr)
        return 2
    if args.single:
        return single(args.target, args.report, args.single)
    if args.fix_bundle:
        return fix_bundle(args.target, args.report,
                          args.fix_bundle, args.bundle_size)
    if args.normalize:
        return normalize(args.target, args.dry_run)
    if args.bundle:
        return bundle(args.target, args.bundle, args.bundle_size)
    if args.status:
        return status(args.target, args.batch_size)
    if args.propagate:
        return propagate(args.target, args.dry_run)
    if args.refresh_prompts:
        return refresh_prompts(args.target)
    return run(args.source, args.target, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
