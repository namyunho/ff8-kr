#!/usr/bin/env python3
"""번역 워크시트를 삽입 계획으로 모은다. 디스크에 무엇을 쓸지의 기록이다.

`patch_disc.py --apply` 가 받는 `{"필드": {"메시지 id": "번역문"}}` 를 만든다.
워크시트를 곧바로 읽어 쓰지 않고 한 파일로 굳히는 이유는 **무엇을 썼는지가
남아야 하기 때문**이다. 디스크에 들어간 내용과 워크시트가 나중에 갈라져도
그때 무엇을 넣었는지 되짚을 수 있다.

디버그 필드(`test`·`gover`·`start` 계열)는 기본으로 뺀다. 플레이어가 볼 수
없는 화면이고, 번역 품질을 확인할 대상도 아니다.

    python3 scripts/build_apply_plan.py work/translate work/apply-plan.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HOLE = re.compile(r"\{b1:\d+\}")


def collect(root: Path, keep_debug: bool) -> tuple[dict, dict]:
    plan: dict[str, dict[str, str]] = {}
    stats = {"필드": 0, "메시지": 0, "건너뛴 디버그 필드": 0,
             "건너뛴 디버그 메시지": 0, "번역 없는 필드": 0}
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" not in document:
            continue
        messages = {str(entry["id"]): entry["ko"]
                    for entry in document["entries"]
                    if entry.get("ko", "").strip()}
        if not messages:
            stats["번역 없는 필드"] += 1
            continue
        if document.get("debug") and not keep_debug:
            stats["건너뛴 디버그 필드"] += 1
            stats["건너뛴 디버그 메시지"] += len(messages)
            continue
        plan[str(document["field"])] = messages
        stats["필드"] += 1
        stats["메시지"] += len(messages)
    return plan, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="번역 워크시트 디렉터리")
    parser.add_argument("output", type=Path, help="계획을 쓸 곳")
    parser.add_argument("--keep-debug", action="store_true",
                        help="디버그 필드도 넣는다")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"없는 디렉터리: {args.root}", file=sys.stderr)
        return 2

    plan, stats = collect(args.root, args.keep_debug)
    if not plan:
        print("넣을 것이 없다", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for label, value in stats.items():
        print(f"  {label:<20} {value:>6,}")
    print(f"→ {args.output}  ({args.output.stat().st_size:,}바이트)")

    # **`{b1:N}` 이 남아 있으면 조용히 넘어가면 안 된다.** 뱅크0 만 쓸 때는 그
    # 자리에 원문 글리프가 그대로 남아 읽히기라도 했지만, 뱅크1 을 한글로
    # 가져간 뒤로는 뜻 없는 음절이 나온다. 넣기 전에 알아야 하는 값이다.
    holes = collections.Counter()
    for field, messages in plan.items():
        holes[field] = sum(1 for text in messages.values() if HOLE.search(text))
    left = {field: n for field, n in holes.items() if n}
    if left:
        total = sum(left.values())
        top = ", ".join(f"{f}({n})" for f, n in
                        sorted(left.items(), key=lambda r: -r[1])[:8])
        print(f"\n  **{{b1:N}} 이 남은 메시지 {total}건** — 필드 {len(left)}개")
        print(f"    {top}")
        print("    뱅크1 을 한글로 가져갔으므로 그 자리는 뜻 없는 음절이 된다.")
        print("    번역을 마저 해야 사라진다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
