#!/usr/bin/env python3
"""FF8 한국어화 — 글자 편집기.

우리가 고칠 수 있는 글자를 한 창에서 다룬다. 지금 붙어 있는 자료원은
**kernel**(아이템·마법·어빌리티·전투커맨드·전투결과·못 바꾸는 캐릭터 이름)
이고, 필드 대사는 기존 `dialogue_editor.py` 를 그대로 띄운다.

## 한 건에 다섯 가지를 나란히 놓는다

    일본어 원문        제어 토큰을 인라인으로 담은 원문
    원문 글리프 코드    실제 바이트
    한국어 초벌        기계·노션 번역. 여기는 안 고친다
    한국어 축약문      **사람이 자리에 맞춰 줄인 것.** 편집 대상은 이 칸뿐이다
    안전 슬롯          이 자리에 들어갈 수 있는 최대 바이트

축약이 비면 초벌이 나가고, 축약이 있으면 축약이 나간다. 지우는 것이 곧
되돌리기다.

## 색이 알려 주는 것

바이트 판정은 세지 않고 삽입기가 쓰는 `glyph_text.encode` 를 그대로 부른다
(불변식 14). 그 위에 **글자마다 값이 다르다는 것**을 색으로 보여 준다.

    (색 없음)  1바이트 음절 — 배치 앞 224칸
    노랑       2바이트 음절 — **줄일 여지가 여기 있다**
    빨강       뱅크1 음절 — 인코딩은 되지만 화면에서 깨진다
    자주       배치에 없는 글자 — 인코딩 자체가 안 된다
    파랑       제어 토큰 — 지우면 삽입이 어긋난다

kernel 초과 462건 중 337건은 2바이트 음절 개수가 초과분보다 많다. 문장을
줄이기 전에 **노란 글자를 싼 동의어로 바꾸는 쪽이 먼저다.**

    ./대사편집기.command
    python3 scripts/text_editor.py --source kernel
    python3 scripts/text_editor.py --source kernel --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import text_measure as TM                    # noqa: E402
import text_rows as TR                       # noqa: E402

# 조각 종류별 색. 배경색으로 칠해야 12픽셀 글자에서도 눈에 든다.
COLOURS = {
    TM.TWO:     ("#3a2f00", "#ffd479"),      # 2바이트 — 줄일 여지
    TM.BANK1:   ("#4a0d0d", "#ff8a8a"),      # 뱅크1 — 화면에서 깨진다
    TM.MISSING: ("#3d0a3d", "#ff9cff"),      # 배치에 없다
    TM.TOKEN:   ("#0d2f4a", "#8ad0ff"),      # 제어 토큰 — 보존해야 한다
}

FILTERS = ("초과만", "미해결 (초과·뱅크1)", "축약함", "뱅크1 사용", "전체")
SORTS = ("자리 순", "초과 큰 순")


def hex_groups(text: str | None, per: int = 2) -> str:
    """`19cc1a3b` -> `19cc 1a3b`. 두 바이트씩 끊어야 선두 바이트가 눈에 든다."""
    if not text:
        return "(인코딩 불가)"
    step = per * 2
    return " ".join(text[i:i + step] for i in range(0, len(text), step))


# ---------------------------------------------------------------------------
# GUI 없이 보는 길 — 상태 확인과 자동 검증에 쓴다
# ---------------------------------------------------------------------------

def report(rows: list[dict], maps: TM.Maps, limit: int) -> int:
    counts = TR.stats(rows, maps)
    for key, value in counts.items():
        print(f"{key:>14}  {value:,}")

    ranked = sorted(
        ((TR.measure(row, maps), row) for row in rows),
        key=lambda pair: -pair[0].overflow)
    over = [(m, r) for m, r in ranked if m.overflow]
    print(f"\n초과 {len(over)}건 — 큰 것부터 {min(limit, len(over))}건\n")
    for result, row in over[:limit]:
        print(f"  +{result.overflow:>2}B  자리 {result.slot_bytes:>2}  "
              f"#{row['offset']} 섹션 {row.get('section')}")
        print(f"        {TM.highlight(TR.effective(row), maps)}")
        if result.savable:
            print(f"        2바이트 음절 {result.savable}개 — 교체 여지")
    return 1 if over else 0


# ---------------------------------------------------------------------------
# 편집 창
# ---------------------------------------------------------------------------

def launch(rows: list[dict], path: Path, maps: TM.Maps) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    saved = [row.get("ko_short") for row in rows]
    view: list[int] = []

    root = tk.Tk()
    root.title(f"FF8 글자 편집기 — {path.name}")
    root.geometry("1180x820")

    # --- 머리글 -------------------------------------------------------------
    head = ttk.Frame(root, padding=(8, 6))
    head.pack(fill="x")
    summary = ttk.Label(head, text="", font=("TkDefaultFont", 12, "bold"))
    summary.pack(side="left")

    sort_box = ttk.Combobox(head, state="readonly", width=10, values=SORTS)
    sort_box.set(SORTS[1])
    sort_box.pack(side="right", padx=(6, 0))
    filter_box = ttk.Combobox(head, state="readonly", width=18, values=FILTERS)
    filter_box.set(FILTERS[0])
    filter_box.pack(side="right", padx=(6, 0))
    section_box = ttk.Combobox(head, state="readonly", width=10)
    section_box.pack(side="right", padx=(6, 0))
    search = ttk.Entry(head, width=18)
    search.pack(side="right", padx=(6, 0))
    ttk.Label(head, text="검색").pack(side="right")

    sections = sorted({row.get("section") for row in rows
                       if row.get("section") is not None})
    section_box["values"] = ["전체"] + [f"#{n}" for n in sections]
    section_box.set("전체")

    # --- 본문: 왼쪽 목록 / 오른쪽 편집 --------------------------------------
    body = ttk.Panedwindow(root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=8, pady=4)

    left = ttk.Frame(body)
    tree = ttk.Treeview(left, columns=("over", "slot", "ja", "ko"),
                        show="headings", selectmode="browse")
    for key, title, width in (("over", "초과", 52), ("slot", "자리", 46),
                              ("ja", "일본어", 210), ("ko", "한국어", 250)):
        tree.heading(key, text=title)
        tree.column(key, width=width, anchor="w")
    bar = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    tree.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    body.add(left, weight=3)

    right = ttk.Frame(body, padding=(10, 0))
    body.add(right, weight=4)

    title = ttk.Label(right, text="", font=("TkDefaultFont", 13, "bold"))
    title.pack(anchor="w", pady=(4, 0))
    gauge = ttk.Label(right, text="", font=("TkDefaultFont", 12))
    gauge.pack(anchor="w", pady=(2, 6))

    ttk.Label(right, text="일본어 원문", foreground="#888").pack(anchor="w")
    ja_view = tk.Text(right, height=2, wrap="word", font=("TkDefaultFont", 13),
                      relief="flat", background="#1b1b22", foreground="#e8e8ef")
    ja_view.pack(fill="x")
    ja_hex = ttk.Label(right, text="", font=("TkFixedFont", 10), foreground="#7d8590")
    ja_hex.pack(anchor="w", pady=(1, 8))

    ttk.Label(right, text="한국어 초벌 (고치지 않는다)",
              foreground="#888").pack(anchor="w")
    draft_view = tk.Text(right, height=2, wrap="word", font=("TkDefaultFont", 13),
                         relief="flat", background="#1b1b22", foreground="#c9c9d4")
    draft_view.pack(fill="x", pady=(0, 8))

    ttk.Label(right, text="한국어 축약문 — 여기만 편집한다",
              foreground="#dd88aa").pack(anchor="w")
    editor = tk.Text(right, height=4, wrap="word", font=("TkDefaultFont", 15),
                     undo=True, background="#101018", foreground="#f2f2f7",
                     insertbackground="#f2f2f7")
    editor.pack(fill="x")
    ko_hex = ttk.Label(right, text="", font=("TkFixedFont", 10), foreground="#7d8590")
    ko_hex.pack(anchor="w", pady=(1, 6))

    hint = ttk.Label(right, text="", wraplength=520, justify="left")
    hint.pack(anchor="w", fill="x")

    ttk.Label(right, text="메모", foreground="#888").pack(anchor="w", pady=(8, 0))
    note = ttk.Entry(right)
    note.pack(fill="x")

    for widget in (ja_view, draft_view, editor):
        for kind, (back, fore) in COLOURS.items():
            widget.tag_configure(kind, background=back, foreground=fore)

    foot = ttk.Frame(root, padding=8)
    foot.pack(fill="x")

    state: dict = {"row": 0, "pending": None}

    # --- 판정과 그리기 ------------------------------------------------------

    def paint(widget, text: str) -> None:
        """조각마다 색을 입힌다. 인코더와 같은 순서로 자른다."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        for kind in COLOURS:
            widget.tag_remove(kind, "1.0", "end")
        for span in TM.spans(text, maps):
            if span.kind in COLOURS:
                widget.tag_add(span.kind, f"1.0+{span.start}c", f"1.0+{span.end}c")

    def current() -> dict:
        return rows[state["row"]]

    def pull() -> None:
        """편집 상자의 글자를 행에 반영한다. 새 행을 만들어 갈아 끼운다."""
        index = state["row"]
        rows[index] = TR.with_short(rows[index], editor.get("1.0", "end-1c"), maps)
        rows[index] = {**rows[index], "note": note.get()}

    def describe(result: TM.Measurement) -> tuple[str, str]:
        if result.used_bytes is None:
            return "인코딩 불가 — 배치에 없는 글자가 있다", "#ff9cff"
        if result.overflow:
            return (f"자리 {result.slot_bytes}B · 사용 {result.used_bytes}B · "
                    f"**{result.overflow}B 넘친다**", "#ff8a8a")
        return (f"자리 {result.slot_bytes}B · 사용 {result.used_bytes}B · "
                f"여유 {result.headroom}B", "#7ee787")

    def advise(result: TM.Measurement) -> str:
        notes = []
        if result.overflow and result.savable:
            notes.append(f"2바이트 음절이 {result.savable}개 있다 — "
                         f"싼 음절로 바꾸면 최대 {result.savable}B 준다")
        if result.chars(TM.BANK1):
            notes.append("뱅크1 음절 " + " ".join(dict.fromkeys(result.chars(TM.BANK1)))
                         + " — 인코딩은 되지만 화면에서 깨진다")
        if result.chars(TM.MISSING):
            notes.append("배치에 없는 글자 "
                         + " ".join(dict.fromkeys(result.chars(TM.MISSING))))
        if result.lost_tokens:
            notes.append("원문에 있던 제어 토큰이 사라졌다: "
                         + " ".join(result.lost_tokens))
        if result.added_tokens:
            notes.append("원문에 없던 토큰이 생겼다: " + " ".join(result.added_tokens))
        return "\n".join(notes)

    def refresh(event=None) -> None:
        pull()
        row = current()
        result = TR.measure(row, maps)
        text, colour = describe(result)
        gauge.configure(text=text, foreground=colour)
        ko_hex.configure(text=hex_groups(row.get("ko_hex")))
        hint.configure(text=advise(result),
                       foreground="#ffd479" if result.overflow else "#7d8590")

        for kind in COLOURS:
            editor.tag_remove(kind, "1.0", "end")
        for span in TM.spans(editor.get("1.0", "end-1c"), maps):
            if span.kind in COLOURS:
                editor.tag_add(span.kind, f"1.0+{span.start}c", f"1.0+{span.end}c")

        item = str(state["row"])
        if tree.exists(item):
            tree.item(item, values=row_values(row))
        stamp()

    def row_values(row: dict) -> tuple:
        result = TR.measure(row, maps)
        mark = f"+{result.overflow}" if result.overflow else ""
        if result.used_bytes is None:
            mark = "??"
        return (mark, result.slot_bytes, row.get("ja", ""), TR.effective(row))

    def stamp_now() -> None:
        state["pending"] = None
        counts = TR.stats(rows, maps)
        dirty = sum(1 for i, row in enumerate(rows) if row.get("ko_short") != saved[i])
        summary.configure(
            text=f"초과 {counts['초과']:,}건 / {counts['초과 바이트']:,}B  ·  "
                 f"축약함 {counts['축약함']:,}  ·  뱅크1 {counts['뱅크1 사용']:,}"
                 + (f"  ·  저장 안 함 {dirty}건" if dirty else ""))

    def stamp() -> None:
        """머리글 집계는 **타자 경로에서 뺀다.**

        1,320행을 다 재면 60ms 가 든다 — 글자마다 그걸 하면 손에 걸린다.
        잠깐 멈췄을 때 한 번만 다시 센다. 지금 보고 있는 한 건의 판정은
        `refresh` 가 즉시 갱신하므로 손끝에서 느려지는 것은 없다.
        """
        if state.get("pending") is not None:
            root.after_cancel(state["pending"])
        state["pending"] = root.after(250, stamp_now)

    # --- 목록 ---------------------------------------------------------------

    def keep(row: dict) -> bool:
        result = TR.measure(row, maps)
        mode = filter_box.get()
        if mode == "초과만" and not result.overflow:
            return False
        if mode == "미해결 (초과·뱅크1)" and not (result.overflow
                                                 or result.chars(TM.BANK1)):
            return False
        if mode == "축약함" and not TR.is_shortened(row):
            return False
        if mode == "뱅크1 사용" and not result.chars(TM.BANK1):
            return False
        if section_box.get() != "전체" \
                and f"#{row.get('section')}" != section_box.get():
            return False
        needle = search.get().strip()
        if needle and needle not in (row.get("ja") or "") \
                and needle not in TR.effective(row):
            return False
        return True

    def rebuild(event=None) -> None:
        tree.delete(*tree.get_children())
        picked = [i for i, row in enumerate(rows) if keep(row)]
        if sort_box.get() == "초과 큰 순":
            picked.sort(key=lambda i: -TR.measure(rows[i], maps).overflow)
        view.clear()
        view.extend(picked)
        for index in picked:
            tree.insert("", "end", iid=str(index), values=row_values(rows[index]))
        stamp()
        if picked:
            show(picked[0])

    def show(index: int) -> None:
        state["row"] = index
        row = rows[index]
        title.configure(text=f"#{row['offset']}   섹션 {row.get('section')}   "
                             f"초벌 출처 {row.get('source')}")
        paint(ja_view, row.get("ja") or "")
        ja_view.configure(state="disabled")
        paint(draft_view, row.get("ko_draft") or "")
        draft_view.configure(state="disabled")
        ja_hex.configure(text=hex_groups(row.get("raw_hex")))

        editor.delete("1.0", "end")
        editor.insert("1.0", row.get("ko_short") or row.get("ko_draft") or "")
        note.delete(0, "end")
        note.insert(0, row.get("note") or "")

        if tree.exists(str(index)):
            tree.selection_set(str(index))
            tree.see(str(index))
        refresh()

    def step(delta: int) -> None:
        pull()
        if not view:
            return
        here = view.index(state["row"]) if state["row"] in view else -1
        show(view[max(0, min(here + delta, len(view) - 1))])

    def on_pick(event=None) -> None:
        picked = tree.selection()
        if picked and int(picked[0]) != state["row"]:
            pull()
            show(int(picked[0]))

    # --- 저장·되돌리기 ------------------------------------------------------

    def do_save() -> None:
        pull()
        try:
            TR.save(path, rows)
        except Exception as error:
            messagebox.showerror("저장 실패", str(error))
            return
        for index, row in enumerate(rows):
            saved[index] = row.get("ko_short")
        messagebox.showinfo("저장", f"{path.name} 에 저장했다\n"
                                    f"(.bak 과 날짜 사본을 남겼다)")
        stamp()

    def do_revert() -> None:
        editor.delete("1.0", "end")
        editor.insert("1.0", current().get("ko_draft") or "")
        refresh()

    ttk.Button(foot, text="◀ 이전", command=lambda: step(-1)).pack(side="left")
    ttk.Button(foot, text="다음 ▶", command=lambda: step(1)).pack(side="left")
    ttk.Button(foot, text="초벌로 되돌리기", command=do_revert).pack(side="left", padx=12)
    ttk.Label(foot, text="⌘S 저장 · ⌘↩ 다음", foreground="#888").pack(side="left", padx=8)
    ttk.Button(foot, text="저장", command=do_save).pack(side="right")

    editor.bind("<KeyRelease>", refresh)
    note.bind("<KeyRelease>", lambda e: pull())
    tree.bind("<<TreeviewSelect>>", on_pick)
    for box in (filter_box, section_box, sort_box):
        box.bind("<<ComboboxSelected>>", rebuild)
    search.bind("<Return>", rebuild)
    root.bind("<Command-s>", lambda e: do_save())
    root.bind("<Control-s>", lambda e: do_save())
    root.bind("<Command-Return>", lambda e: step(1))
    root.bind("<Control-Return>", lambda e: step(1))

    rebuild()
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="kernel", choices=("kernel",),
                        help="편집할 자료원. 필드 대사는 dialogue_editor.py 를 쓴다")
    parser.add_argument("--path", type=Path, default=TR.KERNEL_JSON)
    parser.add_argument("--layout", type=Path, default=TM.CANON)
    parser.add_argument("--check", action="store_true",
                        help="GUI 없이 초과 목록만 본다")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    maps = TM.load_maps(args.layout)
    rows = TR.load(args.path)
    if "ko_draft" not in (rows[0] if rows else {}):
        print("아직 이관되지 않은 자료다. 먼저 옮긴다:\n"
              "  python3 scripts/migrate_kernel_rows.py", file=sys.stderr)
        return 2

    if args.check:
        return report(rows, maps, args.limit)
    return launch(rows, args.path, maps)


if __name__ == "__main__":
    raise SystemExit(main())
