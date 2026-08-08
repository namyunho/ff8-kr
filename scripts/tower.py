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
    Stage("kernel", "kernel 텍스트",
          ["scripts/insert_kernel_text.py"],
          "아이템·마법·어빌리티·전투커맨드·결과창과 **이름을 못 바꾸는 캐릭터 이름**. "
          "원래 크기에 들어가는 섹션만 제자리로 쓴다 — 넘치는 것은 목록으로 알려 준다",
          writes_disc=True),
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
    ("kernel — 아이템·마법·GF어빌리티·전투커맨드·결과창·못 바꾸는 캐릭터 이름",
     "insert_kernel_text.py", True,
     "681건 제자리 삽입 완료. **10개 섹션은 번역이 길어 못 넣었다**"),
    ("튜토리얼", None, False,
     "번역 꾸러미만 있다(work/tutorial-bundle). **삽입 도구가 없다**"),
]


# ── 링크 등록소 ────────────────────────────────────────────────────────────
#
# **관제탑은 자료를 갖지 않는다.** 자료는 각자의 파일에 있고, 여기에는 그
# 파일들이 **어떻게 이어져 있는지**만 둔다. 이어짐이 끊기면 화면이 조용히
# 깨지므로, 링크마다 「이어져 있는지 보는 법」을 함께 적는다.
#
#     from  ──(via)──▶  to        check() 가 이 화살표의 상태를 본다
#
# 링크를 늘릴 때는 자료를 여기 복사하지 말고 **경로와 확인법만** 적는다.

@dataclass
class Link:
    src: str
    dst: str
    via: str
    why: str
    check: "callable"


def _names_to_menu() -> tuple[bool, str]:
    lines = canon_freshness()
    ok = all("어긋남" not in x for x in lines)
    return ok, "; ".join(x for x in lines if "어긋남" in x) or "이름 21칸·글자판 36칸 일치"


def _layout_to_font() -> tuple[bool, str]:
    stamp = FONT_STAMP.read_text(encoding="utf-8").strip() if FONT_STAMP.exists() else None
    if stamp is None:
        return False, "각인이 없다"
    return stamp == canon_digest(), f"각인 {stamp[:12]}…"


def _text_into_layout(path: Path, label: str):
    """그 번역이 쓰는 음절이 배치에 다 있는가. **배치로 들어가는 화살표다.**"""
    def check() -> tuple[bool, str]:
        if not path.exists():
            return False, f"{path} 가 없다"
        chars = {c for c in json.loads(CANON.read_text(encoding="utf-8"))["chars"] if c.strip()}
        used: set[str] = set()
        for row in json.loads(path.read_text(encoding="utf-8")):
            used.update(c for c in (row.get("ko") or "") if "가" <= c <= "힣")
        missing = sorted(used - chars)
        return (not missing,
                f"{len(used)}자 중 {len(missing)}자 없음: {' '.join(missing[:12])}"
                if missing else f"음절 {len(used)}자 전부 있음")
    return check


def _layout_to_battlefont() -> tuple[bool, str]:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_layout.py")],
                          capture_output=True, text=True, cwd=ROOT)
    hit = [l for l in done.stdout.splitlines() if "전투 글꼴" in l]
    return ("실패" not in " ".join(hit)), (hit[-1].strip() if hit else "확인 못 함")


LINKS: list[Link] = [
    Link("data/nameable-entities.json", "work/text/menu-messages.json",
         "build_name_screen.py",
         "이름 정본이 화면 문구·글자판으로 간다. **정본이 낡으면 생성기가 새 작업을 지운다**",
         _names_to_menu),
    Link("work/translate/*.json", "data/glyph-layout.json",
         "build_layout_all.py",
         "필드 번역이 쓰는 음절이 배치에 있어야 한다",
         _text_into_layout(ROOT / "work" / "text" / "menu-messages.json", "메뉴")),
    Link("work/text/kernel-text-ko.json", "data/glyph-layout.json",
         "build_layout_all.py --also",
         "**kernel(아이템·마법·어빌리티·전투커맨드·결과창·못 바꾸는 캐릭터 이름)**"
         "이 쓰는 음절이 배치에 있어야 한다",
         _text_into_layout(ROOT / "work" / "text" / "kernel-text-ko.json", "kernel")),
    Link("data/glyph-layout.json", "work/font-all/font.bin",
         "build_font_4plane.py",
         "배치로 폰트를 굽는다. 각인이 어긋나면 화면 전체가 다른 글자가 된다",
         _layout_to_font),
    Link("data/glyph-layout.json", "battle-name-font-patched.tim",
         "patch_battle_font.py",
         "전투 이름 글꼴은 **칸 번호가 곧 글리프 인덱스**다",
         _layout_to_battlefont),
]


# ── 글자 작업의 순서 ───────────────────────────────────────────────────────
#
# **관제 -> 자료 위치 -> 작업 -> 정본 갱신.** 이 순서를 어겨서 두 번 크게 데었다.
# 정본이 낡은 줄 모르고 생성기를 돌려 사용자 작업을 지웠고(불변식 26),
# kernel 을 배치 입력에서 빠뜨려 음절 47자가 없었다.
#
# 관제탑은 자료를 갖지 않는다. **어디에 있는지와 무엇을 거쳐 반영되는지**만 안다.
TOPICS = {
    "이름": ("data/nameable-entities.json",
             "캐릭터·GF 이름과 이름 입력 글자판. **여기가 정본이다**",
             ["tower.py --stage names", "tower.py --build"]),
    "배치": ("data/glyph-layout.json",
             "글리프 인덱스 <-> 글자. 폰트와 모든 텍스트가 동시에 따른다",
             ["build_layout_all.py (제안만)", "사람이 diff 검토 후 정본 갱신",
              "tower.py --build"]),
    "필드대사": ("work/translate/*.json",
                 "302개 필드의 MSD 번역",
                 ["build_apply_plan.py", "tower.py --stage field"]),
    "메뉴": ("work/text/menu-messages.json",
             "메뉴 문구. **이름 화면 문구는 이름 정본에서 생성된다** — 여기를 직접 고치지 않는다",
             ["tower.py --stage names", "tower.py --stage menu"]),
    "kernel": ("work/text/kernel-text-ko.json",
               "아이템·마법·GF어빌리티·전투커맨드·결과창, 그리고 **이름을 못 바꾸는 "
               "캐릭터 이름**(젤·어바인·키스티스·셀피·사이퍼·이데아·라그나·키로스·워드)",
               ["번역 수정", "build_layout_all.py 로 음절 반영", "정본 갱신",
                "tower.py --build  (※ 삽입 단계는 아직 없다)"]),
    "지명": ("data/? (patch_location_table.py 안)",
             "IMG TOC#132 지명 19개",
             ["tower.py --stage location"]),
    "전투이름글꼴": ("work/analysis/battle-font/",
                     "TOC#26 안의 TIM. **칸 번호가 곧 글리프 인덱스**",
                     ["inject_battle_font.py 로 그림->인덱스",
                      "tower.py --stage battlefont"]),
}


def where(topic: str | None) -> int:
    if topic and topic not in TOPICS:
        print(f"모르는 주제: {topic}\n아는 것: {', '.join(TOPICS)}", file=sys.stderr)
        return 2
    print("글자 작업의 순서 — 관제 → 자료 위치 → 작업 → **정본 갱신**\n")
    for key, (path, why, how) in TOPICS.items():
        if topic and key != topic:
            continue
        print(f"  {key}")
        print(f"    자료  {path}")
        print(f"    무엇  {why}")
        for step in how:
            print(f"    →     {step}")
        print()
    return 0


# **아직 화면에 안 들어간 것.** 관제탑이 이것을 알고 있어야 「다 됐다」고
# 착각하지 않는다. 번역이 끝났다고 들어간 것이 아니다.
PENDING = [
    ("kernel 나머지 10섹션", "work/text/kernel-text-ko.json",
     "681/1,320건은 들어갔다. 남은 것은 번역이 원래 자리보다 길다 — "
     "전투커맨드(#31 +33B) 마법(#32 +180B) 아이템설명(#39 +240B) 등. "
     "**번역을 줄이거나** 섹션을 옮겨 크기를 키운다(파일 여유 888B)"),
    ("튜토리얼 삽입", "work/tutorial-bundle/",
     "번역 꾸러미만 있다 · **삽입 도구가 없다**"),
    ("전투 이름 글꼴 출처", "work/analysis/battle-font/",
     "디스크의 TOC#26 에 넣고 있지만, 게임이 전투마다 **다시 싣는 원본 아카이브**는 "
     "아직 못 찾았다 (docs/roadmap.md 항목 6)"),
]


def pending_report() -> None:
    for title, where, why in PENDING:
        print(f"  [ 미반영 ] {title}")
        print(f"             {where}")
        print(f"             {why}")


def links_report() -> int:
    bad = 0
    for link in LINKS:
        ok, detail = link.check()
        mark = "이어짐" if ok else "**끊김**"
        print(f"  [{mark:^8}] {link.src}")
        print(f"             ──({link.via})──▶ {link.dst}")
        print(f"             {detail}")
        if not ok:
            bad += 1
    return bad


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

    print("\n링크 — 관제탑은 자료를 갖지 않고 이어짐만 본다")
    broken = links_report()
    if broken:
        print(f"  ** 끊긴 링크 {broken}개. 화살표 방향으로 다시 만든다 **")

    print("\n아직 안 들어간 것 — 관제가 알고 있어야 「다 됐다」고 착각하지 않는다")
    pending_report()

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
    group.add_argument("--where", nargs="?", const=None, metavar="주제",
                       help="그 글자 자료가 어디 있고 어떤 순서로 반영되는가")
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
    if args.where is not None or "--where" in sys.argv:
        return where(args.where)
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
