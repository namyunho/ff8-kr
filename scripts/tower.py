#!/usr/bin/env python3
"""빌드 관제탑. **패치는 여러 도구가 순서대로 쌓는 누적형**이라 순서를 틀리면
조용히 어긋난다. 그 순서를 사람 머리가 아니라 여기에 둔다.

## 왜 필요한가

이 저장소에는 디스크에 쓰는 도구가 아홉 개 있고, 그중 넷은 이미 대체된 옛
도구다. 어느 것을 어느 순서로 돌려야 하는지는 `docs/lessons.md` 12번의 산문
안에만 있었다. 그래서 실제로 이런 일이 났다.

- 폰트를 새 배치로 굽고 **옛 배치로 디스크에 쓰는** 상태로 굴렀다 (lessons 16)
- 배치를 바꾸고 **이름 화면 생성기를 다시 안 돌려** 정본이 갈라졌다 (lessons 17-2)
- 「인코딩 실패 0건」 초록불을 믿고 넘어갔다가 세 번 깨뜨렸다

관제탑은 세 가지를 한다.

    1. **순서를 안다.**   단계와 의존을 표로 갖는다
    2. **관문을 건다.**   디스크에 쓰기 전 verify_layout 을 반드시 통과시킨다
    3. **낡은 것을 본다.** 무엇이 정본보다 오래됐는지 알려 준다

    python3 scripts/tower.py --status      지금 무엇이 낡았는가
    python3 scripts/tower.py --build       처음부터 끝까지 다시 만든다
    python3 scripts/tower.py --stage menu  한 단계만
    python3 scripts/tower.py --list        단계와 폐기된 도구 목록
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "data" / "glyph-layout.json"
PATCHED = ROOT / "work" / "patched" / "Final Fantasy VIII (Japan, Asia) (Disc 1).bin"
FONT_STAMP = ROOT / "work" / "font-all" / "layout.sha256"


@dataclass
class Stage:
    key: str
    title: str
    argv: list[str]
    why: str
    writes_disc: bool = False
    produces: list[Path] = field(default_factory=list)


# **순서가 곧 계약이다.** 이름 화면 -> 폰트 -> EXE -> 관문 -> 디스크 쓰기.
# 관문을 폰트 뒤에 두는 이유: 관문이 폰트의 배치 각인을 대조한다.
STAGES: list[Stage] = [
    Stage("names", "이름 화면 생성",
          ["scripts/build_name_screen.py"],
          "정본 이름(data/nameable-entities.json)을 메뉴 문구와 글자판에 깐다. "
          "이걸 안 돌리면 배치가 보호하는 음절과 글자판이 갈라진다",
          produces=[ROOT / "work" / "text" / "menu-messages.json"]),
    Stage("font", "한글 폰트 굽기",
          ["scripts/build_font_4plane.py"],
          "정본 배치로 4중 인터리브 텍스처를 만든다. 배치 각인을 함께 남긴다",
          produces=[ROOT / "work" / "font-all" / "font.bin", FONT_STAMP]),
    Stage("exe", "EXE 4중 인터리브 패치",
          ["scripts/patch_font_4plane.py"],
          "글자 칸을 882 -> 1,764 로 올리는 EXE 패치. 배치와 무관하지만 폰트와 짝이다",
          produces=[ROOT / "work" / "patch-4plane" / "SLPS_018.80"]),
    Stage("gate", "관문 (verify_layout)",
          ["scripts/verify_layout.py"],
          "**디스크에 쓰기 전 반드시 통과해야 한다.** 배치 계약 13가지를 본다"),
    Stage("install", "폰트·EXE 설치",
          ["scripts/install_font_4plane.py"],
          "구운 폰트와 패치한 EXE 를 사본에 넣는다", writes_disc=True),
    Stage("clut", "오버레이 CLUT",
          ["scripts/patch_overlay_clut.py"],
          "오버레이가 제 코드로 그리므로 팔레트 상수를 따로 옮긴다. "
          "**한 번만 적용된다** — 이미 되어 있으면 건너뛴다", writes_disc=True),
    Stage("menu", "메뉴 텍스트",
          ["scripts/insert_menu_text.py"],
          "원본에서 읽어 사본에 쓴다(멱등)", writes_disc=True),
    Stage("location", "지명 표",
          ["scripts/patch_location_table.py"],
          "IMG TOC#132 지명 19개", writes_disc=True),
    Stage("battlefont", "전투 이름 글꼴",
          ["scripts/patch_battle_font.py"],
          "TOC#26 안의 TIM 을 갈아 끼우고 다시 압축한다", writes_disc=True),
    Stage("field", "필드 대사",
          ["scripts/patch_disc.py", "--apply", "work/apply-plan.json"],
          "원본에서 읽어 사본에 쓴다(멱등). 가장 오래 걸린다", writes_disc=True),
]

# 대체됐다. 돌리면 디스크가 어긋난다.
RETIRED = {
    "inject_hangul_font.py": "장면 단위 시험용 초기 도구. 자기 배치 스냅샷을 쓴다",
    "install_font_bank0.py": "뱅크0 단독 설치. 4중 인터리브가 대체했다",
    "install_font_bank1.py": "뱅크1 설치. 뱅크1 안을 쓰지 않기로 했다",
    "patch_font_bank1.py": "뱅크1 EXE 패치. 위와 같다",
}


# 폰트를 쓰는 곳. **정본 배치를 따라야 하는 것 전부**를 적는다 — 아직 넣지
# 못한 것까지 적어야 조용히 잊히지 않는다.
CONSUMERS = [
    ("메뉴 텍스트", "insert_menu_text.py", True,
     "메뉴 문구·이름 화면 문구"),
    ("필드 대사", "patch_disc.py --apply", True,
     "302개 필드의 MSD"),
    ("지명 표", "patch_location_table.py", True,
     "IMG TOC#132, 지명 19개"),
    ("이름 화면", "build_name_screen.py", True,
     "기본 이름 21칸 + 글자판 36칸 (메뉴에 실려 들어간다)"),
    ("전투 이름 글꼴", "patch_battle_font.py", True,
     "TOC#26 안의 TIM. **칸 번호가 곧 글리프 인덱스**라 배치와 직접 묶인다"),
    ("kernel — 아이템·마법·GF어빌리티·전투커맨드·결과창", None, False,
     "번역 1,320건 완료(work/text/kernel-text-ko.json). **삽입 도구가 없다**"),
    ("튜토리얼", None, False,
     "번역 꾸러미만 있다(work/tutorial-bundle). **삽입 도구가 없다**"),
]


def canon_freshness() -> list[str]:
    """정본이 파생물보다 낡지 않았는지 본다.

    **한 번 반대로 했다.** 사용자가 정한 이름이 `work/text/menu-messages.json`
    에만 있었는데, 낡은 `data/nameable-entities.json` 을 정본으로 알고
    생성기를 돌려 **사용자 작업을 덮어썼다.** 정본이 가장 최신이어야 한다.
    """
    out: list[str] = []
    names_path = ROOT / "data" / "nameable-entities.json"
    menu_path = ROOT / "work" / "text" / "menu-messages.json"
    if not (names_path.exists() and menu_path.exists()):
        return ["이름 정본 또는 메뉴 문구가 없다"]
    names = json.loads(names_path.read_text(encoding="utf-8"))
    rows = json.loads(menu_path.read_text(encoding="utf-8"))
    index = {r["id"]: (r.get("ko") or "").strip()
             for r in rows if r.get("sub") == 1 and r.get("group") == 1}

    want_slots = {int(k): names["japanese"][v] for k, v in names["menu_slots"].items()}
    off = [f"id{k}={index.get(k)!r}≠{v!r}" for k, v in sorted(want_slots.items())
           if index.get(k) != v]
    out.append(f"이름 21칸   {'일치' if not off else '**어긋남** ' + ' '.join(off[:3])}")

    import build_name_screen as NS
    board = []
    for key in ("character", "gf"):
        page = names["pages"][key]
        lines = NS.by_column(page["cells"])
        for line, slot in zip(lines, list(NS.PAGE_IDS[page["tab"]])):
            board.append((slot, line.strip()))
    bad = [f"id{s}={index.get(s)!r}≠{t!r}" for s, t in board if index.get(s) != t]
    out.append(f"글자판 36칸 {'일치' if not bad else '**어긋남** ' + ' '.join(bad[:3])}")
    if off or bad:
        out.append("→ 화면 쪽이 앞서 있으면 **정본을 먼저 고친다.** "
                   "생성기를 돌리면 화면 쪽이 지워진다")
    return out


def sha_of(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def canon_digest() -> str:
    doc = json.loads(CANON.read_text(encoding="utf-8"))
    return hashlib.sha256(
        json.dumps(doc["chars"], ensure_ascii=False).encode("utf-8")).hexdigest()


def status() -> int:
    doc = json.loads(CANON.read_text(encoding="utf-8"))
    digest = canon_digest()
    print(f"정본  v{doc['version']}  {len(doc['chars']):,}슬롯  {digest[:16]}…")
    print(f"      기록된 sha256 {'일치' if digest == doc.get('sha256') else '**어긋남**'}")

    stamp = FONT_STAMP.read_text(encoding="utf-8").strip() if FONT_STAMP.exists() else None
    print(f"\n폰트  배치각인 {'없다' if stamp is None else stamp[:16] + '…'}"
          f"  {'최신' if stamp == digest else '**낡음 — font 단계부터 다시**'}")

    if PATCHED.exists():
        import datetime
        when = datetime.datetime.fromtimestamp(PATCHED.stat().st_mtime)
        print(f"사본  {PATCHED.name}  {when:%Y-%m-%d %H:%M}")
    else:
        print("사본  **없다** — patch_disc.py --init 이 먼저다")

    print(f"\n{'정본 최신성 — 파생물이 정본보다 앞서 있지는 않은가':^0}")
    for line in canon_freshness():
        print("  " + line)

    print("\n관문:")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_layout.py")],
                          capture_output=True, text=True, cwd=ROOT)
    for line in done.stdout.strip().splitlines()[-3:]:
        print("  " + line)
    return 0 if done.returncode == 0 else 1


def clut_already_applied() -> bool:
    """CLUT 단계는 멱등이 아니다. 이미 적용됐는지 직접 본다."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import patch_disc as PD
        import patch_font_4plane as PF
        import patch_overlay_clut as OC
        from mips_dis import Exe
        exe = Exe(str(OC.EXE_PATCHED))
        for _name, archive, hook, _loop, third in OC.SHADOW_HOOKS:
            where = OC.find_shadow(exe, third)
            lba, size = next((l, s) for i, l, s in OC.entries() if i == archive)
            data = PD.read_user(PD.PATCH_BIN, lba, size)
            if int.from_bytes(data[hook:hook + 4], "little") != PF.jump(where):
                return False
        return True
    except Exception:                                   # noqa: BLE001
        return False


def run(stage: Stage) -> bool:
    print(f"\n── {stage.key}  {stage.title}")
    if stage.key == "clut" and clut_already_applied():
        print("   이미 적용돼 있다 (훅이 지금 EXE 의 그림자 루틴을 가리킨다) — 건너뛴다")
        return True
    done = subprocess.run([sys.executable, *stage.argv], cwd=ROOT,
                          capture_output=True, text=True)
    tail = (done.stdout + done.stderr).strip().splitlines()
    for line in tail[-4:]:
        print("   " + line)
    if done.returncode != 0:
        print(f"   **실패 (종료코드 {done.returncode})**", file=sys.stderr)
        return False
    return True


def build(only: str | None) -> int:
    if not PATCHED.exists():
        print("디스크 사본이 없다. 먼저 만든다 — patch_disc.py --init")
        if subprocess.run([sys.executable, "scripts/patch_disc.py", "--init"],
                          cwd=ROOT).returncode != 0:
            return 1
    for stage in STAGES:
        if only and stage.key != only:
            # 관문은 디스크에 쓰는 단계 앞에서 언제나 돈다
            if not (stage.key == "gate" and only
                    and next(s for s in STAGES if s.key == only).writes_disc):
                continue
        if not run(stage):
            print("\n멈춘다. 앞 단계가 실패하면 뒤는 무의미하다.", file=sys.stderr)
            return 1
    print("\n전 단계 완료.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="무엇이 낡았는지 본다")
    group.add_argument("--build", action="store_true", help="처음부터 끝까지")
    group.add_argument("--stage", metavar="키", help="한 단계만 (관문은 함께 돈다)")
    group.add_argument("--list", action="store_true", help="단계·폐기 도구 목록")
    args = parser.parse_args()

    if args.list:
        print("단계 (이 순서가 계약이다):\n")
        for i, s in enumerate(STAGES, 1):
            mark = "디스크" if s.writes_disc else "     "
            print(f"  {i:>2}. [{mark}] {s.key:<11} {s.title}")
            print(f"          {s.why}")
        print("\n폰트를 쓰는 곳 — 전부 같은 배치를 따라야 한다:\n")
        for title, tool, wired, why in CONSUMERS:
            mark = "연결됨" if wired else "**미연결**"
            print(f"  [{mark:^8}] {title}")
            print(f"          {tool or '(도구 없음)'} — {why}")
        print("\n폐기된 도구 — 돌리지 않는다:\n")
        for name, why in RETIRED.items():
            print(f"  {name:<26} {why}")
        return 0
    if args.status:
        return status()
    if args.stage:
        if args.stage not in {s.key for s in STAGES}:
            print(f"모르는 단계: {args.stage}", file=sys.stderr)
            return 2
        return build(args.stage)
    return build(None)


if __name__ == "__main__":
    raise SystemExit(main())
