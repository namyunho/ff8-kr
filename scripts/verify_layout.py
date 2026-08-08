#!/usr/bin/env python3
"""글리프 배치가 계약을 지키는지 검사한다. **디스크에 쓰기 전 관문이다.**

## 왜 필요한가

배치(인덱스 -> 글자)는 폰트와 텍스트가 **동시에** 따라야 하는 계약이다. 둘이
어긋나면 화면 전체가 다른 글자로 나오는데, 크래시가 아니라서 조용히 지나간다.
실제로 이 저장소에는 한때 배치 파일이 여섯 개 있었고 서로 882칸 중 880칸이
달랐다. 폰트는 새 배치로 굽고 `patch_disc.py` 는 옛 배치를 기본값으로 읽고
있었다 — 매번 `--layout` 을 직접 넘겨서 안 터졌을 뿐이다.

디스크 2~4 를 아직 번역하지 않았으므로 이 계약은 **앞으로 더 오래** 지켜야 한다.
번역문이 늘면 빈도표가 바뀌고, 배치를 다시 생성하면 이미 넣은 것이 전부
어긋난다. 그래서 배치는 산출물이 아니라 **입력**이어야 한다.

## 무엇을 보는가

    1. 정본이 자기 sha256 과 맞는가
    2. 못 박은 자리(영문자 페이지)가 원본 글리프 그대로인가
    3. 크래시 자리(벡터 폰트 구간)에 글자를 놓지 않았는가
    4. 구워 둔 폰트가 정본으로 구운 것인가
    5. 번역문이 쓰는 음절이 모두 배치에 있는가
    6. 전투 이름 글꼴의 칸 번호가 정본 인덱스와 맞는가
    7. 메뉴가 쓰는 글자가 뱅크0 안에 있는가 (뱅크1 은 평면이 어긋난다)
    8. 메뉴의 고정폭 슬롯이 아직 들어가는가 (삽입기를 그대로 돌린다)
    9. 어떤 스크립트도 정본 아닌 배치를 읽지 않는가

    python3 scripts/verify_layout.py
    python3 scripts/verify_layout.py --strict      # 경고도 실패로 본다
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "data" / "glyph-layout.json"
SINGLE = 224                      # 여기부터 두 바이트
BANK0 = 882                       # 여기부터 뱅크1 — 메뉴는 못 쓴다
BATTLE_CELLS = 231                # 전투 이름 글꼴이 담는 칸 수 (21 x 11)

# `AGENTS.md` 불변식 21 — EXE 의 벡터 폰트 루틴 `0x8002c540` 은 경계 검사를
# 하지 않는다. 이 자리에 글자를 놓으면 그리는 순간 쓰레기를 읽는다.
#
# **베껴 적지 않는다.** 한 번 베껴 적었다가 안전한 네 칸(243·245·247·249)을
# 위험으로 잘못 신고했다. 위험 목록도 정본이 하나여야 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_layout_all import VECTOR_BOMB          # noqa: E402

# 폐기된 도구. 배치를 읽지 않고 자기 스냅샷을 쓸 뿐이라 검사에서 뺀다.
DEPRECATED = {"inject_hangul_font.py"}


class Report:
    def __init__(self) -> None:
        self.fail: list[str] = []
        self.warn: list[str] = []
        self.ok: list[str] = []

    def check(self, good: bool, title: str, detail: str = "", soft: bool = False) -> bool:
        if good:
            self.ok.append(title)
        elif soft:
            self.warn.append(f"{title} — {detail}" if detail else title)
        else:
            self.fail.append(f"{title} — {detail}" if detail else title)
        return good


def load_canon() -> dict:
    if not CANON.exists():
        print(f"정본이 없다: {CANON}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(CANON.read_text(encoding="utf-8"))


def digest_of(chars: list[str]) -> str:
    return hashlib.sha256(json.dumps(chars, ensure_ascii=False).encode("utf-8")).hexdigest()


def korean_syllables(root: Path) -> set[str]:
    found: set[str] = set()
    if not root.exists():
        return found
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                found.update(c for c in item if "가" <= c <= "힣")
            elif isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="경고도 실패로 본다")
    parser.add_argument("--text", type=Path, default=ROOT / "work" / "translate")
    args = parser.parse_args()

    canon = load_canon()
    chars = canon["chars"]
    rep = Report()

    # 1. 자기 해시
    actual = digest_of(chars)
    rep.check(actual == canon.get("sha256"), "정본 sha256 일치",
              f"기록 {canon.get('sha256','?')[:16]}… vs 실제 {actual[:16]}…")

    # 2. 못 박은 자리 — 영문자 페이지는 원본 글리프를 그대로 둔다
    pins = ROOT / "work" / "keyboard-pins.json"
    if pins.exists():
        pinned = json.loads(pins.read_text(encoding="utf-8"))
        bad = [(int(i), c, chars[int(i)]) for i, c in pinned.items()
               if int(i) < len(chars) and chars[int(i)] != c]
        rep.check(not bad, f"못 박은 자리 {len(pinned)}칸 보존",
                  "어긋남 " + ", ".join(f"{i}:{want}->{got}" for i, want, got in bad[:6]))
    else:
        rep.check(False, "못 박은 자리 검사", f"{pins} 가 없다", soft=True)

    # 3. 크래시 자리
    # 금지 색인은 **게임 인덱스** 공간이다. 슬롯으로 보면 엉뚱한 자리를 본다.
    import build_hangul_font as _BF
    occupied = sorted((i, chars[i]) for i in range(len(chars))
                      if chars[i].strip() and _BF.game_index(i) in VECTOR_BOMB)
    rep.check(not occupied, "벡터 폰트 크래시 자리 비어 있음",
              "글자가 놓임: " + " ".join(f"{c}(슬롯{i})" for i, c in occupied[:10]))

    # 3-1. 텍스처 칸을 넘지 않는가
    #
    # **리스트 위치는 게임 인덱스가 아니라 슬롯이다.**
    # `게임인덱스 = (슬롯 // 756) * 882 + (슬롯 % 756)`. 이것을 게임 인덱스로
    # 잘못 보고 「756~881 은 가리킬 셀이 없다」는 없는 결함을 고치려다 리스트를
    # 1,638슬롯으로 늘려 빌드를 깨뜨리고 글자까지 잃었다. 슬롯 쪽 제약은
    # 「텍스처 1,512칸을 넘지 않는다」 하나뿐이다.
    import build_hangul_font as BF

    rep.check(len(chars) <= BF.PER_TEXTURE,
              f"슬롯 {len(chars):,} / 텍스처 {BF.PER_TEXTURE:,}칸",
              f"{len(chars) - BF.PER_TEXTURE:,}칸 넘는다")

    # 4. 구워 둔 폰트가 정본에서 나왔는가
    font = ROOT / "work" / "font-all" / "font.bin"
    stamp = ROOT / "work" / "font-all" / "layout.sha256"
    if font.exists():
        recorded = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else None
        rep.check(recorded == actual, "구워 둔 폰트가 정본에서 나옴",
                  "각인이 없다 — build_font_4plane.py 를 다시 돌려라"
                  if recorded is None else f"각인 {recorded[:16]}… != 정본 {actual[:16]}…",
                  soft=recorded is None)
    else:
        rep.check(False, "폰트 산출물 존재", f"{font} 가 없다", soft=True)

    # 5. 번역문이 쓰는 음절이 모두 배치에 있는가
    have = {c for c in chars}
    used = korean_syllables(args.text)
    missing = sorted(used - have)
    rep.check(not missing, f"번역문 음절 {len(used)}자 모두 배치에 있음",
              f"{len(missing)}자 없음: {' '.join(missing[:20])}")

    # 6. 전투 이름 글꼴 — 이름 음절이 231칸 안에 있는가
    names_path = ROOT / "data" / "nameable-entities.json"
    if names_path.exists():
        names = json.loads(names_path.read_text(encoding="utf-8"))
        index = {c: _BF.game_index(i) for i, c in enumerate(chars)}
        need = {c for key in names["order"] for c in names["korean"][key]}
        absent = sorted(c for c in need if c not in index)
        over = sorted(c for c in need if c in index and index[c] >= BATTLE_CELLS)
        rep.check(not absent, f"이름 음절 {len(need)}자 모두 배치에 있음",
                  "없음: " + " ".join(absent))
        rep.check(not over, f"이름 음절이 전투 글꼴 {BATTLE_CELLS}칸 안에 있음",
                  "범위 밖: " + " ".join(f"{c}({index[c]})" for c in over))

    # 7. 고정폭 슬롯이 아직 들어가는가
    #
    # **이 검사가 없어서 한 번 깨뜨렸다.** 이름 입력 화면의 글자판은 한 줄이
    # 5바이트 고정이다(게임이 줄을 바이트로 센다). 배치를 바꾸며 어떤 음절을
    # 2바이트 구간으로 밀어냈는데, 하필 그 음절이 글자판에 있어서 줄이 6바이트가
    # 되고 이름이 통째로 깨졌다. 인코딩 자체는 성공하므로 「실패 0건」으로 보인다.
    # 7-1. 메뉴가 쓰는 글자는 뱅크0 에 있어야 한다
    #
    # 메뉴를 그리는 오버레이는 **뱅크 비트를 안 더한다**(`build_layout_all.py`).
    # 뱅크1(인덱스 >= 882) 글자를 쓰면 그 글자만 엉뚱한 평면으로 나온다.
    # **인코딩은 성공하므로 `--dry-run` 관문에 안 걸린다** — 렌더링 제약이라
    # 따로 봐야 한다. 실제로 이걸 안 봐서 화면에서 '결' 이 깨졌다.
    menu_json = ROOT / "work" / "text" / "menu-messages.json"
    if menu_json.exists():
        rows = json.loads(menu_json.read_text(encoding="utf-8"))
        used = {c for row in rows for c in (row.get("ko") or "") if "가" <= c <= "힣"}
        where = {c: _BF.game_index(i) for i, c in enumerate(chars)}
        stray = sorted((where[c], c) for c in used if where.get(c, 0) >= BANK0)
        rep.check(not stray, f"메뉴 글자가 모두 뱅크0(<{BANK0}) 안에 있음",
                  " ".join(f"{c}({i})" for i, c in stray[:8]))

        # kernel 도 같은 제약을 받는다. **이 검사가 없어서 '닉'(피닉스의 꼬리)이
        # 뱅크1 로 가 깨졌다.** 메뉴만 보고 kernel 을 안 봤다.
        kernel = ROOT / "work" / "text" / "kernel-text-ko.json"
        if kernel.exists():
            kused = {c for row in json.loads(kernel.read_text(encoding="utf-8"))
                     for c in (row.get("ko") or "") if "가" <= c <= "힣"}
            kstray = sorted((where[c], c) for c in kused if where.get(c, 0) >= BANK0)
            rep.check(not kstray, f"kernel 글자가 모두 뱅크0(<{BANK0}) 안에 있음",
                      f"{len(kstray)}자: "
                      + " ".join(f"{c}({i})" for i, c in kstray[:10]))

    # 7-2. 이름 글자판은 한 줄이 5바이트 고정이고, 쓰는 음절은 전부 1바이트여야 한다
    #
    # **이 검사를 「더 일반적」이라며 --dry-run 으로 바꿨다가 다시 깨뜨렸다.**
    # insert_menu_text 는 블록 전체 크기만 본다. 글자판은 재배치 그룹이라 줄이
    # 6바이트가 되어도 통과한다 — 게임은 줄을 5바이트로 세므로 그때부터 어긋난다.
    # 둘 다 필요하다.
    if menu_json.exists():
        import build_name_screen as NS
        import glyph_text as GT
        import patch_disc as PD
        glyphs, ours, _ = PD.korean_map(CANON)
        keyboard = [row for row in rows
                    if row.get("sub") == 1 and row.get("group") == 1 and row.get("ko")
                    and any(ids.start <= row["id"] < ids.stop
                            for ids in NS.PAGE_IDS.values())]
        over, wide = [], []
        for row in keyboard:
            try:
                size = len(GT.encode(row["ko"], glyphs, ours))
            except Exception as error:                      # noqa: BLE001
                over.append(f"id{row['id']} 인코딩 실패 {error}")
                continue
            if size != NS.CELL:
                over.append(f"id{row['id']}:{row['ko']!r} {size}B")
            wide += [c for c in row["ko"]
                     if "가" <= c <= "힣" and where.get(c, 0) >= SINGLE]
        rep.check(not over, f"이름 글자판 {len(keyboard)}줄이 {NS.CELL}바이트 고정",
                  "; ".join(over[:6]))
        rep.check(not wide, "글자판 음절이 모두 1바이트 구간",
                  " ".join(f"{c}({where[c]})" for c in dict.fromkeys(wide[:8])))

    # 손으로 슬롯 목록을 적지 않는다. **삽입기 자신이 정본이다** — 처음에는
    # 이름 글자판(그룹1)만 확인했다가, 같은 사고를 메뉴 그룹7 '재배열' 에서 또
    # 냈다. 고정폭 슬롯은 메뉴 전반에 흩어져 있고 목록은 도구 안에 있다.
    menu = ROOT / "work" / "text" / "menu-messages.json"
    if menu.exists():
        import subprocess
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "insert_menu_text.py"), "--dry-run"],
            capture_output=True, text=True)
        tail = (done.stderr or done.stdout).strip().splitlines()
        rep.check(done.returncode == 0, "메뉴 고정폭 슬롯 전부 들어감",
                  tail[-1] if tail else f"종료코드 {done.returncode}")
    else:
        rep.check(False, "메뉴 슬롯 검사", f"{menu} 가 없다", soft=True)

    # 8. 정본 아닌 배치를 읽는 스크립트가 있는가
    stray = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name in DEPRECATED or path.name == "verify_layout.py":
            continue
        text = path.read_text(encoding="utf-8")
        for hit in re.findall(r"[\w/.-]*hangul-layout[\w.-]*\.json", text):
            stray.append(f"{path.name}: {hit}")
    rep.check(not stray, "모든 스크립트가 정본만 읽음", "; ".join(stray[:6]))

    for line in rep.ok:
        print(f"  통과  {line}")
    for line in rep.warn:
        print(f"  경고  {line}")
    for line in rep.fail:
        print(f"  실패  {line}")
    print(f"\n통과 {len(rep.ok)} / 경고 {len(rep.warn)} / 실패 {len(rep.fail)}")
    if rep.fail or (args.strict and rep.warn):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
