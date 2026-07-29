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
import sys
from pathlib import Path


def read_tsv(path: Path) -> tuple[dict[int, str], list[str]]:
    """`id<TAB>ko` 를 읽는다. 깨진 줄은 버리지 않고 이유와 함께 돌려준다."""
    rows: dict[int, str] = {}
    complaints: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            complaints.append(f"{path.name}:{number} 탭이 없다: {line[:60]}")
            continue
        head, _, text = line.partition("\t")
        try:
            identifier = int(head.strip())
        except ValueError:
            complaints.append(f"{path.name}:{number} id 가 숫자가 아니다: {head[:20]}")
            continue
        if identifier in rows:
            complaints.append(f"{path.name}:{number} id {identifier} 가 중복이다")
        rows[identifier] = text.strip()
    return rows, complaints


def merge_field(tsv: Path, target: Path, dry_run: bool) -> tuple[int, list[str]]:
    """한 필드를 합친다. 워크시트에 없는 id 는 넣지 않고 알린다."""
    rows, complaints = read_tsv(tsv)
    document = json.loads(target.read_text(encoding="utf-8"))
    known = {entry["id"] for entry in document["entries"]}
    for identifier in sorted(set(rows) - known):
        complaints.append(f"{tsv.name} id {identifier} 가 워크시트에 없다")

    filled = 0
    for entry in document["entries"]:
        text = rows.get(entry["id"])
        if text is None or not text.strip():
            continue
        entry["ko"] = text
        filled += 1
    if not dry_run:
        target.write_text(json.dumps(document, indent=2, ensure_ascii=False)
                          + "\n", encoding="utf-8")
    return filled, complaints


def sheets(target: Path) -> list[tuple[Path, dict]]:
    """워크시트를 파일 순서대로 읽는다."""
    out = []
    for path in sorted(target.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if "entries" in document:
            out.append((path, document))
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
    files = sorted(source.glob("*.tsv")) if source.is_dir() else [source]
    if not files:
        print(f"TSV 가 없다: {source}", file=sys.stderr)
        return 2

    total, merged, complaints = 0, 0, []
    missing = []
    for tsv in files:
        sheet = target / f"{tsv.stem}.json"
        if not sheet.exists():
            missing.append(tsv.name)
            continue
        filled, said = merge_field(tsv, sheet, dry_run)
        total += filled
        merged += 1
        complaints.extend(said)

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
    args = parser.parse_args()
    if not args.target.is_dir():
        print(f"디렉터리가 아니다: {args.target}", file=sys.stderr)
        return 2
    if args.status:
        return status(args.target, args.batch_size)
    if args.propagate:
        return propagate(args.target, args.dry_run)
    if args.refresh_prompts:
        return refresh_prompts(args.target)
    return run(args.source, args.target, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
