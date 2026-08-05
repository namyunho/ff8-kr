#!/usr/bin/env python3
"""kernel.bin 에서 Notion 용어집과 안 맞은 문자열을 로컬 모델로 초벌번역한다.

`scripts/match_kernel_text.py` 가 걸러낸 `work/text/kernel-hidden-translatable.json`
(488건, 2026-08-06 기준)이 대상이다. 적 기술 이름, GF 소환 공격명, 상급 마법,
전투 UI 메시지 등 Notion 공략집에 없던 짧은 문자열들이다.

메뉴 텍스트(`translate_menu_local.py`)와 달리 폭 예산 검사는 하지 않는다 —
전투 텍스트라 메뉴 폭 테이블과 무관하고, 표본이 이름/짧은 메시지 위주라
그림으로 그려지는 아이콘 자리 제약이 없다.

    python3 scripts/translate_kernel_local.py --status
    python3 scripts/translate_kernel_local.py --limit 100
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

import text_metrics as TM                   # noqa: E402

SOURCE = Path("work/text/kernel-hidden-translatable.json")
ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"

SYSTEM = """당신은 게임 현지화 번역가다. 파이널 판타지 8(PS1)의 일본어 전투
텍스트를 한국어로 옮긴다. 아래 종류가 섞여 있다.

- 적의 기술 이름(예: クラッシュアーム, マイティガード)
- G.F. 소환 공격 이름(예: サンダーストーム, ダイアモンドダスト)
- 마법 이름(예: ウォール, フルケア)
- 전투 커맨드(예: 連続剣, リミット)
- 전투 중 UI 메시지(예: お金がたりない, 全滅した……)
- 아이템을 먹었을 때 나오는 짧은 반응 문구(예: うまい!!, げんきひゃくばい!)

규칙
- 한 줄에 하나씩, 입력과 같은 순서로, 번호를 붙여 답한다.
- {02} {0A:23} {03:40} 같은 중괄호 토큰은 **한 글자도 바꾸지 말고 같은 자리에** 둔다.
- 이름류는 기존 파이널 판타지 시리즈 한국어 정발판/통용 번역 관용구를 따른다
  (예: マイティガード=마이티가드, リミット=리미트).
- 짧게 옮긴다. 게임 UI에 들어가는 텍스트이므로 원문보다 눈에 띄게 길어지면 안 된다.
- 설명이나 따옴표를 붙이지 않는다. 번역문만 낸다.
- 원문이 「」나 !! 같은 기호로 끝나면 그 느낌(느낌표, 말줄임표 등)을 살린다.

/no_think"""

TOKEN = re.compile(r"\{[^}]*\}")


def load() -> list[dict]:
    if not SOURCE.exists():
        raise FileNotFoundError(f"{SOURCE} 가 없다. scripts/match_kernel_text.py 먼저 실행.")
    return json.loads(SOURCE.read_text(encoding="utf-8"))


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
        payload = json.loads(reply.read())
    return payload["choices"][0]["message"]["content"]


def parse(answer: str, count: int) -> list[str]:
    out: dict[int, str] = {}
    loose: list[str] = []
    for line in answer.splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        match = re.match(r"^(\d+)[.)\]]\s*(.*)$", line)
        if match:
            out[int(match.group(1))] = match.group(2).strip()
        else:
            loose.append(line)
    if len(out) >= count:
        return [out.get(i + 1, "") for i in range(count)]
    if len(loose) == count:
        return loose
    return [out.get(i + 1, loose[i] if i < len(loose) else "")
            for i in range(count)]


def check(ja: str, ko: str) -> str | None:
    if not ko.strip():
        return "빈 답"
    if TM.control_codes(ja) != TM.control_codes(ko):
        return f"제어 코드가 다르다 {TM.control_codes(ja)} -> {TM.control_codes(ko)}"
    if re.search(r"[ぁ-んァ-ヶ]", TOKEN.sub("", ko)):
        return "가나가 남아 있다"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="qwen/qwen3.6-35b-a3b")
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0,
                        help="이번 세션에 번역할 건수 (0 = 전부)")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    rows = load()
    todo_idx = [i for i, r in enumerate(rows)
                if not r.get("ko", "").strip() or args.retry_failed]

    if args.status:
        done = len(rows) - len(todo_idx)
        print(f"kernel 숨겨진 항목 {len(rows):,}건 — 번역됨 {done:,} / 남음 {len(todo_idx):,}")
        return 0

    if not todo_idx:
        print("남은 것이 없다.")
        return 0

    plan = todo_idx[:args.limit] if args.limit else todo_idx
    batch = args.batch
    print(f"{len(plan)}건을 번역한다 ({batch}개씩)")

    good = bad = 0
    problems: list[tuple[str, str, str]] = []
    for start in range(0, len(plan), batch):
        chunk_idx = plan[start:start + batch]
        chunk = [rows[i]["ja"] for i in chunk_idx]
        prompt = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        try:
            answer = ask(prompt, args.model, args.timeout)
        except (urllib.error.URLError, OSError, KeyError) as error:
            print(f"\n로컬 모델에 붙지 못했다: {error}", file=sys.stderr)
            break
        for idx, ko in zip(chunk_idx, parse(answer, len(chunk))):
            ja = rows[idx]["ja"]
            why = check(ja, ko)
            if why:
                bad += 1
                problems.append((ja, ko, why))
            else:
                good += 1
                rows[idx]["ko"] = ko
        SOURCE.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"  {min(start + batch, len(plan)):>4}/{len(plan)}  "
              f"통과 {good} 실패 {bad}")

    print(f"\n통과 {good}건, 검사에 걸림 {bad}건")
    for ja, ko, why in problems[:15]:
        print(f"  {ja[:30]!r} -> {ko[:30]!r}")
        print(f"      {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
