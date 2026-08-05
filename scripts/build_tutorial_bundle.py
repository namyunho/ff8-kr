#!/usr/bin/env python3
"""튜토리얼 텍스트(`work/text/tutorial-text.json`)를 외부 번역 꾸러미로 자른다.

`work/translate-bundle/`(필드 대사용)과 같은 형식이다 — 다른 AI 채팅창에
파일 하나를 그대로 붙여 넣으면 되게 만든다. 제어 코드 규칙과 쓸 수 있는
글자 제한은 필드 대사와 동일하다(같은 폰트·같은 인코딩이기 때문).

    python3 scripts/build_tutorial_bundle.py
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = Path("work/text/tutorial-text.json")
OUT_DIR = Path("work/tutorial-bundle")
BUNDLE_SIZE = 300

INSTRUCTIONS = """
**돌려줄 것은 번역뿐이다.** 아래와 똑같은 모양으로 답한다.

```
id<탭>번역문
```

- **원문을 다시 적지 않는다.** id 와 번역문만.
- 번역문 안에 실제 개행이나 탭 문자를 넣지 않는다. 줄바꿈은 `{02}` 로 적는다.
- 번역하지 않는 항목은 그 줄을 쓰지 않는다.
- 설명·요약·머리말을 붙이지 않는다.

---

# 번역 지침

이 텍스트는 `mngrp.bin`(메뉴 아카이브) 안의 **튜토리얼·잡지·도움말 텍스트
풀**이다 — 전투 조작 해설, 카드게임 해설, 무기 개조 잡지(월간무기),
리미트 브레이크 기술 설명(격투왕·펫통신), 세계관 잡지(오컬트팬),
아빌리티 세팅 설명 등이 섞여 있다. 등장 순서는 대략 그대로지만 정확한
극 진행 순서는 아니다.

## 제어 코드는 하나도 건드리지 않는다

`{...}` 는 게임 제어 코드다. **개수·종류·순서가 원문과 완전히 같아야 한다.**
검사기가 기계로 대조하며 하나라도 어긋나면 그 항목은 못 쓴다.

필드 대사(`docs/lessons.md`, `sub_8002E4A0`/`sub_8002F73C` 확정)와 같은
인코딩이라 같은 표를 따른다.

| 표기 | 뜻 | 다루는 법 |
|---|---|---|
| `{02}` | 줄바꿈 | 개수를 바꾸지 않는다. 한국어에서 줄을 나눌 자리로 옮기는 것은 된다 |
| `{06:XX}` | 여닫는 강조 쌍 | **쌍째로 옮긴다.** 원문이 감싼 말에 해당하는 한국어를 다시 감싼다 |
| `{03:XX}` | 이름·수치 삽입 | 한 낱말처럼 문장 안에 그대로 둔다 |
| `{01}` | 행 카운터 리셋 | 자리를 그대로 둔다 |
| `{0E:XX}`/`{0F:XX}` | 표 참조 문자열 삽입(지명 등) | 한 낱말처럼 그대로 둔다 |
| `{09:XX}` | 파라미터 코드 | 자리를 그대로 둔다 |
| `{05:XX}` | 변수 삽입(버튼 아이콘 등) | 한 낱말처럼 그대로 둔다 |
| `{04:XX}` | 이름/숫자 표 조회 | 한 낱말처럼 그대로 둔다 |
| `{b1:N}` | 제어 코드 아님, 필드 전용 글자(미판독) | 원문 그대로 둔다. 번역문에서 새로 만들지 않는다 |

`{03:30}` 은 주인공 이름 자리다. `「あぁ、{03:30}」` → `「아, {03:30}」`처럼
코드를 그대로 둔 채 옮긴다.

## 쓸 수 있는 글자

**서로 다른 글자 하나가 폰트 칸 하나를 먹는다.** 부호를 여러 벌 쓰면 그만큼
칸이 밀려난다. 아래에서만 고르고 한 벌로 통일한다.

- 숫자: `0123456789`
- 라틴 대문자: `ABCDEFGHIJKLMNOPQRSTUVWXYZ`
- 라틴 소문자: `e`
- 문장 부호: `.,?!…:-/～・`
- 따옴 기호: `「」『』()`
- 그 밖: `+=%&※×、。`
- 공백 한 칸

- 따옴표 `"` `'` 는 폰트에 없다. 대사 인용은 `「」`를 쓴다.
- `【】`는 없다 — `『』`로 바꾼다.
- 일본어 `、``。` 대신 `,``.`를 쓴다.

## 고유명사

`work/text/glossary-additions.csv`, `work/text/notion-glossary.json` 을
따른다. 지명·GF·캐릭터 이름이 자주 나온다(발람, 돌, 트라비아, 갈바디아,
스퀄, 젤, 키스티스 등). 같은 이름이 항목마다 다르게 번역되면 안 된다.

**번역이 원문보다 눈에 띄게 길어지지 않게 한다** — 튜토리얼 창도 폭 제한이
있다.

---

# 원문
"""


def main() -> int:
    entries = json.loads(SOURCE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total = len(entries)
    n_bundles = (total + BUNDLE_SIZE - 1) // BUNDLE_SIZE
    for b in range(n_bundles):
        chunk = entries[b * BUNDLE_SIZE:(b + 1) * BUNDLE_SIZE]
        lines = [f"# FF8 튜토리얼 텍스트 초벌번역 — 꾸러미 {b + 1} / {n_bundles}",
                 "",
                 f"이 파일 하나가 한 번 넘길 분량이다. 항목 {len(chunk)}건.",
                 INSTRUCTIONS.strip()]
        for e in chunk:
            lines.append(f"{e['offset']}\t{e['ja']}")
        path = OUT_DIR / f"bundle-{b + 1:02d}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{path}  {len(chunk)}건")

    print(f"\n총 {total:,}건 -> {n_bundles}개 꾸러미 ({OUT_DIR})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
