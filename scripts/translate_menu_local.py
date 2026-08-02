#!/usr/bin/env python3
"""메뉴 텍스트를 로컬 모델로 번역한다. 여러 세션에 나눠 돌린다.

메뉴 문구는 짧고 정형적이라 로컬 모델에 맞는다. 필드 대사와 달리 문맥이 거의
없고 `つかう` `そうび` `ジャンクション` 같은 낱말이 대부분이다.

## 필드 대사와 다른 점 셋

1. **폭이 빡빡하다.** 메뉴 항목은 창 안에 들어가야 한다. 원문의 픽셀 폭을
   예산으로 잡고 넘으면 잡아낸다. 대사는 302px 한 줄이지만 메뉴는 원문보다
   길어지면 곧바로 레이아웃이 깨진다.
2. **같은 원문이 많다.** 1,090건 중 고유 원문은 599종뿐이다. 고유 원문만
   번역하고 나머지에 전파한다.
3. **제어 코드가 적다.** 그래도 있는 것은 한 글자도 안 바꾼다.

## 여러 세션에 나누는 법

`--limit` 만큼만 번역하고 저장한다. 다시 돌리면 **아직 안 된 것부터** 이어서
한다. 중간에 끊겨도 그때까지 한 것은 남는다.

    python3 scripts/translate_menu_local.py --status
    python3 scripts/translate_menu_local.py --limit 100
    python3 scripts/translate_menu_local.py --limit 100      # 이어서

LM Studio 는 GUI 의 Developer 탭이나 `lms server start` 로 연다.
기본 `http://localhost:1234/v1` 이고 Ollama 도 같은 규격이다.
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
import text_metrics as TM                   # noqa: E402

SOURCE = Path("work/text/menu-messages.json")

# **번역하면 안 되는 자리.** 서브1 그룹1 의 id 54 부터 끝까지는 이름 입력
# 화면의 **글자판**이다. `あいうえお` `カキクケコ` 처럼 가나 배열 그 자체이므로
# 옮기면 이름 입력이 통째로 망가진다. 한국어판에서는 한글 자모 배열로 다시
# 짜야 하는 자리이지 번역 대상이 아니다.
KEYBOARD = (1, 1, 54)           # (서브, 그룹, 이 id 부터)

# 고유명사는 모델이 자기 식으로 음역한다. 고정한다.
NAMES = {
    "スコール": "스콜", "リノア": "리노아", "アンジェロ": "안젤로",
    "ケツァクウァトル": "케찰코아틀", "シヴァ": "시바", "イフリート": "이프리트",
    "セイレーン": "세이렌", "ブラザーズ": "브라더스", "ディアボロス": "디아볼로스",
    "カーバンクル": "카벙클", "リヴァイアサン": "리바이어던",
    "パンデモニウム": "판데모니움", "ケルベロス": "케르베로스",
    "アレクサンダー": "알렉산더", "グラシャラボラス": "글라샤라볼라스",
    "バハムート": "바하무트", "サボテンダー": "사보텐더", "トンベリ": "톤베리",
    "エデン": "에덴", "ボコ": "보코",
}
ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
TOKEN = re.compile(r"\{[^}]*\}")

SYSTEM = """당신은 게임 UI 번역가다. 파이널 판타지 8(PS1)의 일본어 메뉴 문구를
한국어로 옮긴다.

규칙
- 한 줄에 하나씩, 입력과 같은 순서로, 번호를 붙여 답한다.
- {02} {01} {03:30} 같은 중괄호 토큰은 **한 글자도 바꾸지 말고 같은 자리에** 둔다.
- 메뉴 항목이므로 **짧게** 옮긴다. 원문보다 길어지면 화면을 벗어난다.
- 게임에서 널리 쓰는 한국어 용어를 쓴다: つかう=사용, そうび=장비,
  ジャンクション=정션, まほう=마법, アイテム=아이템, GF=GF, セーブ=저장,
  ロード=불러오기, たたかう=싸운다, にげる=도망, ステータス=상태.
- 설명이나 따옴표를 붙이지 않는다. 번역문만 낸다.
- 원문이 기호나 숫자뿐이면 그대로 낸다."""


def load() -> list[dict]:
    if not SOURCE.exists():
        raise FileNotFoundError(f"{SOURCE} 가 없다. 메뉴 텍스트를 먼저 뽑는다.")
    return json.loads(SOURCE.read_text(encoding="utf-8"))


LAYOUT = Path("work/hangul-layout-4plane.json")
FONT = Path("work/font-4plane/font.bin")
_METRICS: tuple[list[int], dict[str, int]] | None = None


def metrics() -> tuple[list[int], dict[str, int]]:
    """**진짜 폭 테이블**을 쓴다. 글자마다 진행폭이 다르다.

    모든 글자를 12px 로 잡으면 라틴·숫자가 섞인 문구를 과대평가한다. 실제
    폰트에서 `F` 는 8px, `I` 는 5px 다. `FINAL FANTASY VIII` 를 216px 로
    계산해 원문보다 넓다고 거절한 적이 있는데 실제로는 그보다 좁았다.
    """
    global _METRICS
    if _METRICS is None:
        chars = json.loads(LAYOUT.read_text(encoding="utf-8"))["chars"]
        index = {char: i for i, char in enumerate(chars)}
        data = FONT.read_bytes()
        off = int.from_bytes(data[:4], "little")
        widths = []
        for i in range(len(chars)):
            byte = data[off + (i >> 1)]
            widths.append((byte >> 4) if (i & 1) else (byte & 0xF))
        _METRICS = (widths, index)
    return _METRICS


def budget(text: str) -> int:
    """줄 폭의 최댓값. 메뉴는 원문의 이 값을 넘으면 안 된다."""
    widths, index = metrics()
    return max(TM.line_pixels(text, widths, index.get) or [0])


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
    """`1. 번역문` 꼴을 순서대로 거둔다. 번호가 없으면 줄 순서로 본다."""
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


def check(ja: str, ko: str, covered: set[str]) -> str | None:
    """넣을 수 있는 번역인지 본다. 문제가 있으면 이유를 돌려준다."""
    if not ko.strip():
        return "빈 답"
    if TM.control_codes(ja) != TM.control_codes(ko):
        return f"제어 코드가 다르다 {TM.control_codes(ja)} -> {TM.control_codes(ko)}"
    missing = sorted({c for c in TOKEN.sub("", ko)
                      if "가" <= c <= "힣" and c not in covered})
    if missing:
        return f"폰트에 없는 음절 {''.join(missing)}"
    over = budget(ko) - budget(ja)
    if over > 24:
        return f"원문보다 {over}px 넓다"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--batch", type=int, default=20,
                        help="한 번에 보낼 원문 수 (기본 20)")
    parser.add_argument("--limit", type=int, default=0,
                        help="이번 세션에 번역할 고유 원문 수 (0 = 전부)")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-failed", action="store_true",
                        help="검사에 걸린 것을 다시 시도한다")
    args = parser.parse_args()

    rows = load()
    sub, group, start = KEYBOARD
    skipped = 0
    for row in rows:
        if row["sub"] == sub and row["group"] == group and row["id"] >= start:
            row["ko"] = ""          # 글자판은 번역하지 않는다
            skipped += 1
        elif row["ja"] in NAMES:
            row["ko"] = NAMES[row["ja"]]

    done = {r["ja"] for r in rows if r.get("ko", "").strip()}
    unique: dict[str, str] = {}
    for row in rows:
        if row["sub"] == sub and row["group"] == group and row["id"] >= start:
            continue
        unique.setdefault(row["ja"], "")
    todo = [ja for ja in unique if ja not in done or args.retry_failed]

    if args.status:
        print(f"메뉴 텍스트 {len(rows):,}건")
        print(f"  글자판(번역 대상 아님)  {skipped:>5}건")
        print(f"  고유 원문             {len(unique):>5}종")
        print(f"  번역됨   {len(unique) - len(todo):>5}종")
        print(f"  남음     {len(todo):>5}종")
        return 0

    if not todo:
        print("남은 것이 없다.")
        return 0

    covered = BF.covered()
    batch = args.batch
    plan = todo[:args.limit] if args.limit else todo
    print(f"고유 원문 {len(unique):,}종 중 {len(plan)}종을 번역한다 "
          f"({batch}개씩)")

    good = bad = 0
    problems: list[tuple[str, str, str]] = []
    for start in range(0, len(plan), batch):
        chunk = plan[start:start + batch]
        prompt = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        try:
            answer = ask(prompt, args.model, args.timeout)
        except (urllib.error.URLError, OSError, KeyError) as error:
            print(f"\n로컬 모델에 붙지 못했다: {error}", file=sys.stderr)
            print(f"  {ENDPOINT}\n"
                  "  LM Studio 의 Developer 탭이나 `lms server start` 로 연다.",
                  file=sys.stderr)
            break
        for ja, ko in zip(chunk, parse(answer, len(chunk))):
            why = check(ja, ko, covered)
            if why:
                bad += 1
                problems.append((ja, ko, why))
            else:
                good += 1
                for row in rows:
                    if row["ja"] == ja:
                        row["ko"] = ko
        SOURCE.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"  {min(start + batch, len(plan)):>4}/{len(plan)}  "
              f"통과 {good} 실패 {bad}")

    print(f"\n통과 {good}종, 검사에 걸림 {bad}종")
    for ja, ko, why in problems[:12]:
        print(f"  {ja[:26]!r} -> {ko[:26]!r}")
        print(f"      {why}")
    left = sum(1 for ja in unique
               if not any(r["ja"] == ja and r.get("ko", "").strip() for r in rows))
    print(f"\n아직 남은 고유 원문 {left}종 — 다시 돌리면 이어서 한다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
