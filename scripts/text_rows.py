#!/usr/bin/env python3
"""편집 대상 텍스트 한 행의 **스키마 정본**.

일본어 원문, 한국어 초벌, 사람이 줄인 축약문, 글리프 코드, 안전 슬롯을
한 행에 나란히 둔다. 셋 다 따로 남겨야 「사람이 무엇을 고쳤는가」를
diff 로 추측하지 않고 자료가 직접 말한다.

    offset      제자리 삽입 좌표
    section     섹션 번호 (집계·필터)
    ja          ① 일본어 원문 — 제어 토큰을 인라인으로 담는다
    raw_hex     ④ 원문 글리프 코드
    source      초벌 출처 (notion / qwen / qwen-desc / manual)
    ko_draft    ② 한국어 초벌 — **동결**. 기계 번역기는 여기만 쓴다
    ko_short    ③ 한국어 축약문 — 사람이 고친 것. `null` 이면 아직 안 봤다
    ko             **화면에 나가는 글자**
    ko_hex      ④ `ko` 의 글리프 코드
    slot_bytes  ⑤ 안전 슬롯 — 이 자리에 들어갈 수 있는 최대 바이트
    used_bytes  지금 쓰는 바이트 (`null` 이면 인코딩 불가)
    note        사람 메모

## 우선순위는 하나뿐이다

    ko = ko_short 가 있으면 ko_short, 없으면 ko_draft

`ko` 라는 이름을 그대로 둔 것이 이 설계의 핵심이다. 이 칸을 읽는 소비자가
다섯인데(불변식 25: 소비자를 전부 센다), 이름을 바꾸면 다섯 곳을 다 고쳐야
하고 **그중 셋은 고쳐도 조용히 틀린다** — 배치 검사가 사람이 안 쓰게 된
음절을 계속 보호하고, 새로 쓴 음절은 안 보이는 채로 초록불이 뜬다.

    insert_kernel_text.py       디스크에 쓸 글자
    build_layout_all.py --also  화면에 나갈 음절 전부
    verify_layout.py            같은 음절 집합 (뱅크0 관문)
    tower.py                    링크·미반영 집계
    translate_kernel_desc_local.py  빈 초벌을 채운다 — **여기만 가드가 필요하다**

## 파생값은 저장하되 믿지 않는다

`ko`·`ko_hex`·`used_bytes` 는 다시 계산할 수 있는 값이다. 사람이 눈으로
보라고 파일에 적어 두지만, 낡을 수 있으므로 `--check` 가 「적힌 값 == 지금
인코딩한 값」을 전수로 대조한다. 어긋나면 그 사실부터 말한다.

    python3 scripts/text_rows.py --check
    python3 scripts/text_rows.py --stats
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import glyph_text as GT                      # noqa: E402
import text_measure as TM                    # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KERNEL_JSON = PROJECT_ROOT / "work" / "text" / "kernel-text-ko.json"

# 사람이 읽는 파일이라 칸 순서를 고정한다. 여기 없는 칸은 뒤에 그대로 남긴다.
KEY_ORDER = ("offset", "section", "ja", "raw_hex", "source",
             "ko_draft", "ko_short", "ko", "ko_hex",
             "slot_bytes", "used_bytes", "note")

DERIVED = ("ko", "ko_hex", "used_bytes")


# ---------------------------------------------------------------------------
# 우선순위 — 이 파일에서만 정의한다
# ---------------------------------------------------------------------------

def effective(row: dict) -> str:
    """화면에 나가는 글자. **축약이 있으면 축약, 없으면 초벌.**

    `None` 도 `""` 도 「축약문 없음」이다. 편집기가 빈 칸을 남기면 초벌로
    돌아간다 — 지우는 것이 곧 되돌리기다.
    """
    short = row.get("ko_short")
    if short:
        return short
    return row.get("ko_draft") or ""


def is_shortened(row: dict) -> bool:
    """사람이 손댄 행인가."""
    return bool(row.get("ko_short"))


def with_short(row: dict, text: str | None, maps: TM.Maps) -> dict:
    """축약문을 바꾼 **새 행**을 돌려준다. 원본은 그대로 둔다.

    초벌과 같아지면 축약문을 지운다 — 「고치지 않았다」와 「초벌로 되돌렸다」를
    같은 상태로 본다.
    """
    cleaned = (text or "").strip("\n")
    if not cleaned or cleaned == (row.get("ko_draft") or ""):
        cleaned = None
    return refresh({**row, "ko_short": cleaned}, maps)


def refresh(row: dict, maps: TM.Maps) -> dict:
    """파생값 셋을 다시 재서 넣은 **새 행**."""
    text = effective(row)
    used = TM.encoded_length(text, maps)
    return order({
        **row,
        "ko": text,
        "ko_hex": (GT.encode(text, maps.glyphs, maps.bank1).hex()
                   if used is not None else None),
        "used_bytes": used,
    })


def order(row: dict) -> dict:
    """칸 순서를 고정한다. 모르는 칸도 버리지 않는다."""
    known = {key: row[key] for key in KEY_ORDER if key in row}
    rest = {key: value for key, value in row.items() if key not in KEY_ORDER}
    return {**known, **rest}


def measure(row: dict, maps: TM.Maps) -> TM.Measurement:
    """이 행의 판정. 토큰 대조 기준은 **일본어 원문**이다."""
    return TM.measure(effective(row), int(row.get("slot_bytes") or 0),
                      row.get("ja") or "", maps)


# ---------------------------------------------------------------------------
# 읽고 쓰기 — `work/` 는 git 밖이라 사본을 남긴다
# ---------------------------------------------------------------------------

def load(path: Path = KERNEL_JSON) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"목록이 아니다: {path}")
    return rows


def save(path: Path, rows: list[dict]) -> None:
    """원자적으로 바꾸고 사본을 둘 남긴다.

    이 파일은 `work/` 에 있어 git 이 추적하지 않는다. 되돌릴 방법이 사본밖에
    없으므로 **직전 판(`.bak`)과 그날 첫 판(날짜 사본)** 을 함께 남긴다.
    사람이 몇 시간 손본 축약문이 여기 있다(불변식 26).
    """
    if path.exists():
        snapshot = path.parent / "backups" / f"{path.stem}.{date.today():%Y%m%d}{path.suffix}"
        if not snapshot.exists():
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, snapshot)
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp")
    try:
        json.dump([order(row) for row in rows], handle,
                  ensure_ascii=False, indent=1)
        handle.write("\n")
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# 집계 — 편집기 머리글과 CLI 가 같은 것을 본다
# ---------------------------------------------------------------------------

def stats(rows: list[dict], maps: TM.Maps) -> dict:
    """**행마다 한 번만 잰다.** 항목마다 다시 재면 편집기가 타자마다 멈춘다."""
    results = [measure(row, maps) for row in rows]
    return {
        "전체": len(rows),
        "축약함": sum(1 for row in rows if is_shortened(row)),
        "초과": sum(1 for r in results if r.overflow),
        "초과 바이트": sum(r.overflow for r in results),
        "뱅크1 사용": sum(1 for r in results if r.chars(TM.BANK1)),
        "인코딩 불가": sum(1 for r in results if r.used_bytes is None),
        "이름 :: 설명": sum(1 for row in rows if "::" in (row.get("ko") or "")),
    }


# ---------------------------------------------------------------------------
# 자기검증
# ---------------------------------------------------------------------------

def check(path: Path, maps: TM.Maps) -> int:
    rows = load(path)
    print(f"{path}  {len(rows):,}행\n")

    missing_cols = [key for key in KEY_ORDER
                    if not any(key in row for row in rows)]
    if missing_cols:
        print(f"  **아직 이관되지 않았다** — 없는 칸: {', '.join(missing_cols)}")
        print("  python3 scripts/migrate_kernel_rows.py 를 먼저 돌린다")
        return 1

    bad_priority = bad_derived = bad_slot = 0
    for row in rows:
        if row.get("ko") != effective(row):
            bad_priority += 1
        fresh = refresh(row, maps)
        if any(row.get(key) != fresh.get(key) for key in DERIVED):
            bad_derived += 1
        if "raw_hex" in row and row.get("slot_bytes") != len(row["raw_hex"]) // 2:
            bad_slot += 1

    ok = True
    for label, count, hint in (
            ("우선순위 (ko == 축약 ?? 초벌)", bad_priority, "편집기가 refresh 를 안 거쳤다"),
            ("파생값 (ko_hex·used_bytes)", bad_derived, "파일이 낡았다 — 다시 저장하면 맞는다"),
            ("안전 슬롯 (raw_hex 길이)", bad_slot, "원문 자리가 바뀌었다면 이관을 다시 한다")):
        mark = "통과" if not count else "**실패**"
        print(f"  {mark}  {label}" + (f"  — {count}행 어긋남 ({hint})" if count else ""))
        ok = ok and not count

    print()
    for key, value in stats(rows, maps).items():
        print(f"  {key:>14}  {value:,}")
    return 0 if ok else 1


def selftest(maps: TM.Maps) -> int:
    """편집 한 번의 왕복을 처음부터 끝까지 돌려 본다.

    「자리에 맞게 줄였다 -> 저장했다 -> 다시 열었다 -> 초벌로 되돌렸다」가
    실제로 그 결과를 내는지 본다. 임시 파일에만 쓰고 진짜 자료는 안 건드린다.
    """
    seed = {
        "offset": 1, "section": 31,
        "ja": "{03:40}装備している武器で攻撃します",
        "raw_hex": "0340" + "19cc1a3b77858dab1a41194b45196e195d779d79",
        "source": "test",
        "ko_draft": "무장한 무기로 공격합니다", "ko_short": None,
        "slot_bytes": 21, "note": "",
    }
    fails: list[str] = []

    def want(good: bool, label: str) -> None:
        print(f"  {'통과' if good else '**실패**'}  {label}")
        if not good:
            fails.append(label)

    row = refresh(seed, maps)
    want(row["ko"] == row["ko_draft"], "축약이 없으면 초벌이 나간다")
    want(row["used_bytes"] == 15 and row["ko_hex"].startswith("5da6"),
         "파생값을 잰다 (used_bytes·ko_hex)")

    row = with_short(row, "무기로 공격", maps)
    want(row["ko_short"] == "무기로 공격" and row["ko"] == "무기로 공격",
         "축약이 있으면 축약이 나간다")
    want(row["used_bytes"] == len(GT.encode("무기로 공격", maps.glyphs, maps.bank1)),
         "축약문으로 파생값을 다시 잰다")

    row = with_short(row, row["ko_draft"], maps)
    want(row["ko_short"] is None, "초벌과 같아지면 축약을 지운다")
    row = with_short(row, "", maps)
    want(row["ko_short"] is None and row["ko"] == row["ko_draft"],
         "빈 칸은 초벌로 되돌리기다")

    # 제어 토큰을 잃으면 판정이 잡아야 한다
    row = with_short(row, "무기로 공격", maps)
    lost = measure({**row, "ja": seed["ja"]}, maps).lost_tokens
    want(lost == ("{03:40}",), "사라진 제어 토큰을 잡는다")

    # 자리 판정
    tight = with_short(row, "적 전체에게 아주 긴 문장을 넣어 자리를 넘긴다", maps)
    want(measure(tight, maps).overflow > 0, "자리를 넘기면 초과로 센다")

    # 저장 -> 다시 읽기
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "round-trip.json"
        save(path, [row])
        back = load(path)[0]
        want(back == order(row), "저장하고 다시 읽으면 같다")
        save(path, [row])
        want((path.parent / "backups").exists() and
             path.with_suffix(path.suffix + ".bak").exists(),
             "사본을 남긴다 (.bak · 날짜 사본)")

    print(f"\n{len(fails)}건 실패" if fails else "\n전부 통과")
    return 1 if fails else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, nargs="?", default=KERNEL_JSON)
    parser.add_argument("--layout", type=Path, default=TM.CANON)
    parser.add_argument("--check", action="store_true", help="스키마 불변식을 검사한다")
    parser.add_argument("--stats", action="store_true", help="집계만 본다")
    parser.add_argument("--selftest", action="store_true",
                        help="편집 왕복을 임시 파일로 돌려 본다")
    args = parser.parse_args()

    maps = TM.load_maps(args.layout)
    if args.selftest:
        return selftest(maps)
    if args.stats:
        for key, value in stats(load(args.path), maps).items():
            print(f"{key:>14}  {value:,}")
        return 0
    return check(args.path, maps)


if __name__ == "__main__":
    raise SystemExit(main())
