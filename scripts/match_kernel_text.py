#!/usr/bin/env python3
"""kernel.bin 문자열(`work/text/kernel-text.json`)을 Notion 공략집 용어집
(`work/text/notion-glossary.json`)과 일본어 키로 매칭한다.

kernel.bin 은 **이름 다음에 바로 설명이 온다**(`docs/font-analysis.md`).
그래서 이름이 매칭되면 바로 다음 항목은 그 이름의 설명으로 보고 "숨겨진
항목" 후보에서 뺀다 — 설명 문장 자체가 용어집(단어 단위)과 매칭될 일은
없기 때문이다.

남는 미상 항목은 다시 둘로 가른다.

- **설명형**(です/します/…에 끝나거나 15자 이상인 문장) — 매칭된 이름의
  설명일 가능성이 높지만 짝을 확정 못 한 것. 번역 없이 그대로 둔다.
- **이름형**(그 외 — 짧고 문장이 아님) — 진짜 "리스트에 없는 항목" 후보.
  이게 사용자에게 보고하고 초벌번역을 돌릴 대상이다.

    python3 scripts/match_kernel_text.py
"""

from __future__ import annotations

import json
from pathlib import Path

KERNEL = Path("work/text/kernel-text.json")
GLOSSARY = Path("work/text/notion-glossary.json")
OUT_MATCHED = Path("work/text/kernel-matched.json")
OUT_HIDDEN = Path("work/text/kernel-hidden-names.json")

SENTENCE_END = ("です", "ます", "する", "した", "しん", "せん")

# Notion 원문(한국 블로그 발췌)이 일본어를 옮기며 흔히 섞는 문자 차이.
# 게임 데이터는 항상 오른쪽(정자/신자체)을 쓴다. 매칭 전에 양쪽을 여기로 민다.
NORMALIZE = str.maketrans({
    "－": "ー",  # 전각 하이픈(U+FF0D) -> 장음부호(U+30FC)
    "鐵": "鉄", "醫": "医", "彈": "弾", "黑": "黒", "壞": "壊",
    "觸": "触", "鬪": "闘", "劍": "剣", "龍": "竜", "絲": "糸",
    "體": "体", "發": "発", "號": "号", "藥": "薬",
})


def norm(text: str) -> str:
    return text.translate(NORMALIZE)


def flat_glossary(doc: dict) -> dict[str, str]:
    flat = {}
    for key, table in doc.items():
        if key.startswith("_"):
            continue
        flat.update(table)
    return {norm(k): v for k, v in flat.items()}


def looks_like_sentence(ja: str) -> bool:
    if len(ja) >= 15:
        return True
    return ja.endswith(SENTENCE_END)


def main() -> int:
    entries = json.loads(KERNEL.read_text(encoding="utf-8"))
    glossary = flat_glossary(json.loads(GLOSSARY.read_text(encoding="utf-8")))

    matched: list[dict] = []
    hidden_names: list[dict] = []
    described: list[dict] = []

    prev_matched = False
    for e in entries:
        ja = e["ja"]
        key = norm(ja)
        if key in glossary:
            matched.append({**e, "ko": glossary[key]})
            prev_matched = True
            continue
        if prev_matched:
            described.append(e)
            prev_matched = False
            continue
        prev_matched = False
        if looks_like_sentence(ja):
            described.append(e)
        else:
            hidden_names.append(e)

    OUT_MATCHED.write_text(json.dumps(matched, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    OUT_HIDDEN.write_text(json.dumps(hidden_names, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    print(f"전체 {len(entries):,}건")
    print(f"  이름 매칭됨      {len(matched):,}건 -> {OUT_MATCHED}")
    print(f"  설명문(짝/추정)  {len(described):,}건 -- 번역 보류")
    print(f"  이름형 미상      {len(hidden_names):,}건 -> {OUT_HIDDEN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
