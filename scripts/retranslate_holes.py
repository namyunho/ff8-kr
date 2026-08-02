#!/usr/bin/env python3
"""`{b1:N}` 구멍이 남은 대사를 **원문을 다시 읽어** 번역기에 되돌린다.

## 구멍은 번역 실패가 아니라 판독 미완이었다

`{b1:N}` 은 필드 전용 폰트(뱅크1)의 글리프인데 대응표에 없어서 문자로 못 푼
자리다. 그래서 **원문 쪽에도 구멍이 있었고**, 번역기는 옮길 대상 자체를 못 봤다.

    ja  「さあ、B班受け持ち地{b1:0}は … 【中{b1:2}広場】よ!」
    ko  「자, B반 담당 구{b1:0}는 … 『중{b1:2} 광장』이야!」

`build_bank1_map.py --sheets --holes` 로 그 도형들을 읽어 대응표를 채우면 원문이
온전해진다. 그 다음이 이 도구다 — **디스크에서 원문을 다시 디코드해** 번역기에
넘긴다. 워크시트의 낡은 `ja` 를 쓰면 구멍이 그대로 되돌아온다.

## 통과한 것만 반영한다

원문이 온전해졌다고 번역이 저절로 좋아지지는 않는다. 검사에 걸리면 **원래
번역을 그대로 둔다.** 구멍이 남은 채로 두는 편이 깨진 문장보다 낫다.

    python3 scripts/retranslate_holes.py --dry-run
    python3 scripts/retranslate_holes.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_hangul_font as BF              # noqa: E402
import build_text_db as DB                  # noqa: E402
import extract_field_text as FT             # noqa: E402
import glyph_text as GT                     # noqa: E402
import text_metrics as TM                   # noqa: E402

ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
HOLE = re.compile(r"\{b1:(\d+)\}")
KANA = re.compile(r"[぀-ヿ一-鿿]")
LAYOUT = Path("work/hangul-layout-all.json")
FONT = Path("work/font-all/font.bin")

SYSTEM = """당신은 게임 대사 번역가다. 파이널 판타지 8(PS1)의 일본어 대사를
한국어로 옮긴다.

규칙
- {02} {01} {06:25} 같은 중괄호 토큰은 **한 글자도 바꾸지 말고 같은 자리에** 둔다.
  개수도 순서도 원문과 똑같아야 한다.
- 한 줄({02} 사이)이 길어지면 화면을 벗어난다. 원문 길이를 넘지 않게 옮긴다.
- 일본어를 남기지 않는다. 가나도 한자도 전부 옮긴다.
- 설명이나 따옴표를 덧붙이지 않는다. 번역문만 낸다.
- 고유명사: スコール=스콜, リノア=리노아, キスティス=키스티스, ゼル=젤,
  セルフィ=셀피, アーヴァイン=어바인, サイファー=사이퍼, ガーデン=가든,
  バラム=발람, ドール=돌레트, ティンバー=팀버, マニアックス=매니악스,
  ヘッジヴァイパー=헤지바이퍼, SeeD=SeeD.
- 가타카나 의성어도 한글로 옮긴다. きゃ～=꺄～ 처럼 소리를 그대로 옮기면 된다."""


def metrics() -> tuple[list[int], dict[str, int]]:
    """배치 슬롯 -> 폭. 폭표는 게임 인덱스로 색인되므로 변환이 필요하다."""
    chars = json.loads(LAYOUT.read_text(encoding="utf-8"))["chars"]
    data = FONT.read_bytes()
    off = int.from_bytes(data[:4], "little")
    widths = []
    for slot in range(len(chars)):
        i = BF.game_index(slot)
        byte = data[off + (i >> 1)]
        widths.append((byte >> 4) if (i & 1) else (byte & 0xF))
    return widths, {char: i for i, char in enumerate(chars)}


THINK = re.compile(r"<think>.*?</think>|</?think>", re.S)


def clean(answer: str) -> str:
    """모델이 흘린 것을 걷어낸다.

    추론 모델은 `<think>…</think>` 를 답변에 섞어 낸다. 그대로 두면 제어 코드
    개수가 안 맞아 검사에 걸리는데, 번역이 나빠서가 아니라 껍데기 때문이다.
    """
    answer = THINK.sub("", answer)
    return answer.strip().strip("`").strip()


def ask(prompt: str, model: str, timeout: float) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode()
    request = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read())["choices"][0]["message"]["content"]


def check(ja: str, ko: str, widths, lookup, covered: set[str]) -> str | None:
    """검사기의 여덟 가지 중 이 자리에 걸리는 것들."""
    ko = ko.strip()
    if not ko:
        return "빈 답"
    if HOLE.search(ko):
        return "구멍이 그대로 남았다"
    if TM.control_codes(ja) != TM.control_codes(ko):
        return f"제어 코드가 다르다 {TM.control_codes(ja)} -> {TM.control_codes(ko)}"
    if TM.line_count(ja) != TM.line_count(ko):
        return f"줄 수가 다르다 {TM.line_count(ja)} -> {TM.line_count(ko)}"
    # **어디가 걸렸는지 짚어 준다.** "일본어가 남았다 かさま" 만 주면 모델이
    # 어느 낱말인지 못 찾는다. "줄 폭 321px" 만 주면 엉뚱한 줄을 줄여 오히려
    # 늘어난 적이 있다(321 -> 329). 걸린 자리를 통째로 보여 준다.
    if KANA.search(TM.TOKEN.sub("", ko)):
        spots = [line for line in TM.split_lines(ko)
                 if KANA.search(TM.TOKEN.sub("", line))]
        return f"일본어가 남았다 — 이 줄이다: {' / '.join(spots)[:120]}"
    missing = sorted({c for c in TM.TOKEN.sub("", ko)
                      if "가" <= c <= "힣" and c not in covered})
    if missing:
        return f"폰트에 없는 음절 {''.join(missing)}"
    lines = TM.split_lines(ko)
    pixels = TM.line_pixels(ko, widths, lookup.get)
    for line, width in zip(lines, pixels):
        if width > TM.LINE_PIXELS:
            return (f"줄 폭 {width}px > {TM.LINE_PIXELS}px — 이 줄을 줄인다: "
                    f"{line[:80]}")
    return None


def targets(root: Path, glyphs) -> list[tuple[Path, dict, int, str]]:
    """구멍이 남은 항목과 **디스크에서 새로 읽은 원문**을 짝지어 돌려준다."""
    out = []
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        hits = [e for e in document["entries"] if HOLE.search(e.get("ko", ""))]
        if not hits:
            continue
        index = document["field"]
        msd = FT.msd_section(FT.load_entry(index))
        bank1 = DB.bank1_for(index, msd, glyphs)
        offsets = FT.message_offsets(msd)
        for entry in hits:
            start = offsets[entry["id"]]
            stop = msd.find(b"\x00", start)
            fresh = GT.decode(bytes(msd[start:stop if stop >= 0 else len(msd)]),
                              glyphs, bank1)
            out.append((path, document, entry["id"], fresh))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path("work/translate"))
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    glyphs = GT.GlyphMap.load()
    jobs = targets(args.root, glyphs)
    still = [j for j in jobs if HOLE.search(j[3])]
    print(f"구멍이 남은 대사 {len(jobs)}건")
    print(f"  원문이 온전해진 것 {len(jobs) - len(still)}건")
    if still:
        print(f"  아직 판독이 모자란 것 {len(still)}건 — 건너뛴다")
    jobs = [j for j in jobs if not HOLE.search(j[3])]
    if args.dry_run or not jobs:
        for _, document, number, fresh in jobs[:3]:
            print(f"\n  필드 {document['field']} #{number}\n    {fresh[:100]}")
        return 0

    widths, lookup = metrics()
    covered = BF.covered()
    good = bad = 0
    problems: list[tuple[int, int, str, str]] = []
    touched: dict[Path, dict] = {}
    for path, document, number, fresh in jobs:
        # **한 번 걸리면 이유를 알려 주고 다시 시킨다.** 실패의 대부분은
        # 번역이 나빠서가 아니라 가타카나 의성어를 안 옮겼거나 줄이 길거나
        # 껍데기가 섞인 것이다 — 무엇이 걸렸는지 말해 주면 대개 고쳐 낸다.
        prompt, why, answer = fresh, None, ""
        for attempt in range(2):
            try:
                answer = clean(ask(prompt, args.model, args.timeout))
            except (urllib.error.URLError, OSError, KeyError) as error:
                print(f"\n로컬 모델에 붙지 못했다: {error}", file=sys.stderr)
                return 1
            why = check(fresh, answer, widths, lookup, covered)
            if not why:
                break
            prompt = (f"{fresh}\n\n"
                      f"# 방금 낸 번역이 검사에 걸렸다: {why}\n"
                      f"# 같은 원문을 다시 옮긴다. 이번에는 그 문제가 없어야 한다.\n"
                      f"# 가타카나 의성어·고유명사도 한글로 옮긴다.")
        if why:
            bad += 1
            problems.append((document["field"], number, answer[:50], why))
            continue
        good += 1
        for entry in document["entries"]:
            if entry["id"] == number:
                entry["ja"] = fresh
                entry["ko"] = answer
        touched[path] = document
        for target, doc in touched.items():
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        print(f"  {good + bad}/{len(jobs)}  통과 {good} 실패 {bad}")

    print(f"\n통과 {good}건, 검사에 걸림 {bad}건 (걸린 것은 원래 번역을 그대로 뒀다)")
    for field, number, text, why in problems[:12]:
        print(f"  필드 {field} #{number}  {text!r}\n      {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
