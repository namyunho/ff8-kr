#!/usr/bin/env python3
"""kernel.bin 이름 뒤에 붙는 **설명 문장**을 로컬 모델로 초벌번역한다.

`translate_kernel_local.py` 가 이름(437종 이상)을 끝낸 뒤 남는 게 이거다 —
`work/text/kernel-text-ko.json` 에서 아직 `ko` 가 빈 항목들. 대부분 이름
바로 다음 오프셋에 붙는 효과 설명이라(`docs/font-analysis.md`), 이름을 알고
있다는 전제로 문맥을 프롬프트에 같이 준다.

    python3 scripts/translate_kernel_desc_local.py --status
    python3 scripts/translate_kernel_desc_local.py --limit 100
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

SOURCE = Path("work/text/kernel-text-ko.json")
ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"

SYSTEM = """당신은 게임 현지화 번역가다. 파이널 판타지 8(PS1)의 일본어
문장을 한국어로 옮긴다. 전부 아이템·마법·G.F.·적 기술의 **효과 설명**이거나
전투 UI 메시지다(예: 敵単体に炎属性のダメージ魔法 = 적 단일 대상에게 화염
속성 대미지를 주는 마법).

각 줄은 `이름 :: 원문` 형식으로 주어진다. **이름은 참고용 맥락이니 답에
포함하지 말고, 원문만 번역**한다.

규칙
- 한 줄에 하나씩, 입력과 같은 순서로, 번호를 붙여 답한다.
- {02} {0A:23} {03:40} 같은 중괄호 토큰은 **한 글자도 바꾸지 말고 같은 자리에** 둔다.
- 게임에서 널리 쓰는 한국어 용어를 쓴다: HP=HP, ダメージ=대미지, 属性=속성,
  状態異常=상태 이상, 敵単体=적 단일 대상, 敵全体=적 전체, 味方全体=아군 전체.
- 설명이나 따옴표를 붙이지 않는다. 번역문만 낸다.
- ダミー처럼 실제 게임에 안 쓰이는 자리표시자는 "더미"로만 옮긴다.

/no_think"""

TOKEN = re.compile(r"\{[^}]*\}")


def load() -> list[dict]:
    if not SOURCE.exists():
        raise FileNotFoundError(f"{SOURCE} 가 없다.")
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def context_name(rows: list[dict], i: int) -> str:
    """바로 앞 항목이 이름(ko 있음, 오프셋이 붙어 있음)이면 그걸 맥락으로 쓴다."""
    if i == 0:
        return "(맥락 없음)"
    prev = rows[i - 1]
    if prev.get("ko") and prev["offset"] < rows[i]["offset"] <= prev["offset"] + len(prev["ja"]) + 40:
        return f"{prev['ja']}({prev['ko']})"
    return "(맥락 없음)"


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
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    rows = load()
    todo_idx = [i for i, r in enumerate(rows)
                if not r.get("ko", "").strip() or args.retry_failed]

    if args.status:
        done = len(rows) - len(todo_idx)
        print(f"kernel 전체 {len(rows):,}건 — 확보 {done:,} / 남음 {len(todo_idx):,}")
        return 0

    if not todo_idx:
        print("남은 것이 없다.")
        return 0

    plan = todo_idx[:args.limit] if args.limit else todo_idx
    batch = args.batch
    print(f"{len(plan)}건을 번역한다 ({batch}개씩, 맥락 포함)")

    good = bad = 0
    problems: list[tuple[str, str, str]] = []
    for start in range(0, len(plan), batch):
        chunk_idx = plan[start:start + batch]
        lines = []
        for n, i in enumerate(chunk_idx):
            ctx = context_name(rows, i)
            lines.append(f"{n + 1}. {ctx} :: {rows[i]['ja']}")
        prompt = "\n".join(lines)
        try:
            answer = ask(prompt, args.model, args.timeout)
        except (urllib.error.URLError, OSError, KeyError) as error:
            print(f"\n로컬 모델에 붙지 못했다: {error}", file=sys.stderr)
            break
        chunk_ja = [rows[i]["ja"] for i in chunk_idx]
        for idx, ko in zip(chunk_idx, parse(answer, len(chunk_idx))):
            ja = rows[idx]["ja"]
            why = check(ja, ko)
            if why:
                bad += 1
                problems.append((ja, ko, why))
            else:
                good += 1
                rows[idx]["ko"] = ko
                rows[idx]["source"] = "qwen-desc"
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
