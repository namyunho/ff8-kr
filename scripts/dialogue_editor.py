#!/usr/bin/env python3
"""FF8 필드 대사를 한 건씩 검토하며 한국어 번역을 넣는 로컬 GUI.

`psx-gpx-cyberformula-kr` 의 17x3 대사 편집기를 FF8 전제에 맞게 고쳤다.
사이버 포뮬러는 고정 셀 17열x3행이었지만 **FF8 은 폭 테이블 기반 가변폭**
이라 열 수가 아니라 픽셀 폭이 제약이다. 안전 슬롯 대신 이미 검증된
바이트 예산을 경계로 쓴다.

원본을 수정하지 않는다. 워크시트 CSV 의 `ko` 열만 바꾸며, 나머지 열은
읽은 값을 그대로 복사한다. 덮어쓰기 전 `.bak` 사본을 만들고 임시 파일을
거쳐 원자적으로 교체한다.

세 가지 한계를 동시에 보여준다.

    바이트 예산   원본 길이. 넘으면 in-place 교체가 깨진다.
    줄 픽셀 폭    원본 실측 최대 302px. 넘으면 창 밖으로 흘러넘친다.
    줄 수         원본 줄 수. 넘으면 창 높이를 벗어난다.

    python3 scripts/dialogue_editor.py work/text/f293-worksheet.csv --field 293
    python3 scripts/dialogue_editor.py work/text/f293-worksheet.csv --check
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_field_text as FT             # noqa: E402
import preview_hangul_scene as PH           # noqa: E402
import text_metrics as TM                   # noqa: E402

# 상한은 `text_metrics` 가 정본이다. 전수 26,883줄 실측치다.
DEFAULT_LINE_PIXELS = TM.LINE_PIXELS
FALLBACK_WIDTH = TM.FALLBACK_WIDTH  # 폭 테이블에 없는 글리프의 진행폭
PREVIEW_SCALE = 2
PREVIEW_ROW_HEIGHT = 16             # 원본 행 진행 높이
COLUMNS = ("id", "byte_budget", "glyph_count", "lines", "control_codes", "ko")


class EditorError(ValueError):
    """편집기가 다룰 수 없는 입력."""


@dataclass
class Row:
    """워크시트 한 줄. `ko` 만 편집 대상이고 나머지는 보호한다."""

    index: int
    identifier: str
    byte_budget: int
    glyph_count: int
    lines: int
    control_codes: str
    ko: str
    saved_ko: str

    @property
    def dirty(self) -> bool:
        return self.ko != self.saved_ko


def glyph_widths(blob: bytes) -> list[int]:
    """니블 팩 폭 테이블을 편다. `sub_8002E3EC` 가 정본이다."""
    table_offset, tim_offset = struct.unpack_from("<II", blob, 0)
    widths = []
    for index in range((tim_offset - table_offset) * 2):
        packed = blob[table_offset + (index >> 1)]
        widths.append((packed >> 4) if (index & 1) else (packed & 0xF))
    return widths


def load_rows(path: Path) -> list[Row]:
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise EditorError(f"워크시트에 없는 열: {', '.join(missing)}")
        rows = []
        for index, raw in enumerate(reader):
            ko = raw.get("ko") or ""
            rows.append(Row(
                index=index,
                identifier=raw["id"],
                byte_budget=int(raw["byte_budget"]),
                glyph_count=int(raw["glyph_count"]),
                lines=int(raw["lines"]),
                control_codes=raw.get("control_codes") or "",
                ko=ko,
                saved_ko=ko,
            ))
    if not rows:
        raise EditorError("워크시트가 비어 있다")
    return rows


def save_rows(path: Path, rows: list[Row]) -> None:
    """`.bak` 을 남기고 원자적으로 교체한다."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    handle = tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False)
    try:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([row.identifier, row.byte_budget, row.glyph_count,
                             row.lines, row.control_codes, row.ko])
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    for row in rows:
        row.saved_ko = row.ko


def control_byte_cost(field: str) -> int:
    return PH.control_byte_cost(field)


@dataclass
class Measurement:
    """한 엔트리의 세 한계를 한 번에 잰다."""

    used_bytes: int
    budget: int
    line_pixels: list[int]
    line_limit: int
    line_count: int
    line_count_limit: int
    unmapped: list[str]

    @property
    def byte_overflow(self) -> int:
        return max(0, self.used_bytes - self.budget)

    @property
    def wide_rows(self) -> list[int]:
        return [i for i, px in enumerate(self.line_pixels) if px > self.line_limit]

    @property
    def fits(self) -> bool:
        return (not self.byte_overflow and not self.wide_rows
                and self.line_count <= self.line_count_limit)


def measure(row: Row, assignment: dict[str, int], widths: list[int],
            line_limit: int) -> Measurement:
    encoded, unmapped = PH.encode(row.ko, assignment)
    used = len(encoded) + control_byte_cost(row.control_codes)
    pixels = []
    for piece in PH.split_lines(row.ko):
        total = 0
        for char in piece:
            index = assignment.get(char)
            if index is None:
                total += FALLBACK_WIDTH
            else:
                total += widths[index] if index < len(widths) else FALLBACK_WIDTH
        pixels.append(total)
    return Measurement(used, row.byte_budget, pixels, line_limit,
                       len(pixels), max(row.lines, 1), unmapped)


def wrap_conservative(text: str, assignment: dict[str, int], widths: list[int],
                      line_limit: int) -> str:
    """띄어쓰기 경계에서만 다시 접는다. 단어를 쪼개지 않는다."""
    words = " ".join(PH.split_lines(text)).split()
    if not words:
        return text

    def advance(word: str) -> int:
        total = 0
        for char in word:
            index = assignment.get(char)
            total += (widths[index] if index is not None and index < len(widths)
                      else FALLBACK_WIDTH)
        return total

    space = advance(" ")
    lines: list[str] = []
    current: list[str] = []
    used = 0
    for word in words:
        need = advance(word) + (space if current else 0)
        if current and used + need > line_limit:
            lines.append(" ".join(current))
            current, used = [word], advance(word)
        else:
            current.append(word)
            used += need
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def render_original(field: int, message_id: int, bank0: FT.Font,
                    bank1: FT.Font | None):
    """원본 일본어 한 건을 PIL 이미지로 그린다."""
    from PIL import Image

    dat = FT.load_entry(field)
    msd = FT.msd_section(dat)
    offsets = FT.message_offsets(msd)
    if message_id >= len(offsets):
        return None
    rows: list[list[int]] = [[]]
    for token in FT.tokenize(msd, offsets[message_id]):
        if token[0] == "g":
            rows[-1].append(token[1])
        elif token[1] in (1, 2, 7):
            rows.append([])
    return _draw(rows, {"bank0": bank0, "bank1": bank1})


def render_korean(text: str, assignment: dict[str, int], font: FT.Font):
    rows = [[assignment[c] for c in piece if c in assignment]
            for piece in PH.split_lines(text)]
    return _draw(rows, {"bank0": font, "bank1": None})


def _draw(rows: list[list[int]], fonts: dict):
    from PIL import Image

    width = 26 + FT.CELL * 26
    height = 8 + PREVIEW_ROW_HEIGHT * max(len(rows), 1)
    image = Image.new("RGB", (width * PREVIEW_SCALE, height * PREVIEW_SCALE),
                      FT.BACKGROUND)
    pixels = image.load()
    for row_index, indices in enumerate(rows):
        top = 4 + row_index * PREVIEW_ROW_HEIGHT
        pen = 8
        for index in indices:
            font = fonts["bank1"] if index & 0x400 else fonts["bank0"]
            if font is None:
                pen += FT.CELL
                continue
            cell = font.glyph(index & 0x3FF if index & 0x400 else index)
            for y in range(FT.CELL):
                for x in range(FT.CELL):
                    colour = FT.SHADE[cell[y][x]]
                    if colour is None:
                        continue
                    for sy in range(PREVIEW_SCALE):
                        for sx in range(PREVIEW_SCALE):
                            px = (pen + x) * PREVIEW_SCALE + sx
                            py = (top + y) * PREVIEW_SCALE + sy
                            if 0 <= px < width * PREVIEW_SCALE \
                                    and 0 <= py < height * PREVIEW_SCALE:
                                pixels[px, py] = colour
            pen += FT.CELL
            if pen > width - FT.CELL:
                break
    return image


def build_assignment(rows: list[Row]) -> dict[str, int]:
    fake = [{"ko": row.ko} for row in rows]
    return PH.assign_indices(PH.collect_syllables(fake))


def report(rows: list[Row], widths: list[int], line_limit: int) -> int:
    assignment = build_assignment(rows)
    translated = [r for r in rows if r.ko.strip()]
    print(f"엔트리 {len(rows)}개, 번역됨 {len(translated)}개")
    print(f"필요한 음절 {len(assignment)}종 (뱅크 0 수용량 {PH.BANK0_CAPACITY})")
    problems = 0
    for row in translated:
        result = measure(row, assignment, widths, line_limit)
        if result.fits and not result.unmapped:
            continue
        problems += 1
        reasons = []
        if result.byte_overflow:
            reasons.append(f"예산 {result.budget} < 사용 {result.used_bytes}")
        if result.wide_rows:
            widest = max(result.line_pixels)
            reasons.append(f"줄 폭 {widest}px > {line_limit}px")
        if result.line_count > result.line_count_limit:
            reasons.append(f"줄 수 {result.line_count} > {result.line_count_limit}")
        if result.unmapped:
            reasons.append("미배정 " + "".join(sorted(set(result.unmapped))))
        print(f"  #{row.identifier}: " + ", ".join(reasons))
    print(f"문제 {problems}건")
    return 0 if problems == 0 else 1


def launch(rows: list[Row], path: Path, field: int | None, widths: list[int],
           line_limit: int) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import ImageTk

    bank0 = FT.system_font()
    bank1 = None
    if field is not None:
        try:
            bank1 = FT.field_font(FT.load_entry(field - 1))
        except Exception:
            bank1 = None

    state = {"index": 0, "filter": "전체", "photo_ja": None, "photo_ko": None}

    root = tk.Tk()
    root.title(f"FF8 대사 편집기 — {path.name}")
    root.geometry("980x760")

    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    header = ttk.Label(top, text="", font=("TkDefaultFont", 13, "bold"))
    header.pack(side="left")
    filter_box = ttk.Combobox(top, state="readonly", width=16,
                              values=("전체", "미번역만", "한계 초과만"))
    filter_box.set("전체")
    filter_box.pack(side="right")

    ja_label = ttk.Label(root, text="원본", padding=(8, 4))
    ja_label.pack(anchor="w")
    ja_canvas = tk.Label(root, background="#12121a")
    ja_canvas.pack(fill="x", padx=8)

    ko_label = ttk.Label(root, text="한글 미리보기", padding=(8, 4))
    ko_label.pack(anchor="w")
    ko_canvas = tk.Label(root, background="#12121a")
    ko_canvas.pack(fill="x", padx=8)

    editor = tk.Text(root, height=6, font=("TkFixedFont", 14), undo=True)
    editor.pack(fill="both", expand=True, padx=8, pady=6)

    status = ttk.Label(root, text="", padding=8, justify="left")
    status.pack(fill="x")

    controls = ttk.Label(root, text="", padding=(8, 0), foreground="#666")
    controls.pack(fill="x")

    buttons = ttk.Frame(root, padding=8)
    buttons.pack(fill="x")

    def visible() -> list[int]:
        assignment = build_assignment(rows)
        if state["filter"] == "미번역만":
            return [r.index for r in rows if not r.ko.strip()]
        if state["filter"] == "한계 초과만":
            out = []
            for row in rows:
                if not row.ko.strip():
                    continue
                result = measure(row, assignment, widths, line_limit)
                if not result.fits or result.unmapped:
                    out.append(row.index)
            return out
        return [r.index for r in rows]

    def commit_edit() -> None:
        rows[state["index"]].ko = editor.get("1.0", "end-1c")

    def show(index: int) -> None:
        state["index"] = max(0, min(index, len(rows) - 1))
        row = rows[state["index"]]
        editor.delete("1.0", "end")
        editor.insert("1.0", row.ko)
        header.config(text=f"#{row.identifier}   ({state['index'] + 1}/{len(rows)})"
                           + ("  *수정됨" if row.dirty else ""))

        if field is not None:
            try:
                image = render_original(field, int(row.identifier), bank0, bank1)
                state["photo_ja"] = ImageTk.PhotoImage(image)
                ja_canvas.config(image=state["photo_ja"])
            except Exception:
                ja_canvas.config(image="", text="원본 렌더 실패")

        refresh()

    def refresh(event=None) -> None:
        commit_edit()
        row = rows[state["index"]]
        assignment = build_assignment(rows)
        result = measure(row, assignment, widths, line_limit)

        try:
            cells = PH.HF.rasterize(PH.HF.DEFAULT_TTF, 12, list(assignment))
            font = PH.overlay_font(cells, assignment, 3)
            image = render_korean(row.ko, assignment, font)
            state["photo_ko"] = ImageTk.PhotoImage(image)
            ko_canvas.config(image=state["photo_ko"], text="")
        except Exception as error:
            ko_canvas.config(image="", text=f"미리보기 실패: {error}")

        marks = []
        marks.append(f"바이트 {result.used_bytes}/{result.budget}"
                     + (f"  초과 {result.byte_overflow}" if result.byte_overflow else ""))
        widest = max(result.line_pixels) if result.line_pixels else 0
        marks.append(f"줄 폭 {widest}/{line_limit}px"
                     + (f"  초과 행 {result.wide_rows}" if result.wide_rows else ""))
        marks.append(f"줄 수 {result.line_count}/{result.line_count_limit}")
        if result.unmapped:
            marks.append("미배정 " + "".join(sorted(set(result.unmapped))))
        status.config(text="   |   ".join(marks),
                      foreground="#b00" if not result.fits else "#060")
        controls.config(text="보존할 제어 코드: "
                             + (row.control_codes or "없음"))
        header.config(text=f"#{row.identifier}   ({state['index'] + 1}/{len(rows)})"
                           + ("  *수정됨" if row.dirty else ""))

    def step(delta: int) -> None:
        commit_edit()
        order = visible()
        if not order:
            messagebox.showinfo("이동", "조건에 맞는 엔트리가 없다")
            return
        current = state["index"]
        later = [i for i in order if (i > current if delta > 0 else i < current)]
        target = (later[0] if delta > 0 else later[-1]) if later else order[0]
        show(target)

    def do_wrap() -> None:
        commit_edit()
        row = rows[state["index"]]
        assignment = build_assignment(rows)
        wrapped = wrap_conservative(row.ko, assignment, widths, line_limit)
        if wrapped == row.ko:
            messagebox.showinfo("자동 줄바꿈", "바꿀 것이 없다")
            return
        row.ko = wrapped
        editor.delete("1.0", "end")
        editor.insert("1.0", wrapped)
        refresh()

    def do_save() -> None:
        commit_edit()
        try:
            save_rows(path, rows)
        except Exception as error:
            messagebox.showerror("저장 실패", str(error))
            return
        messagebox.showinfo("저장", f"{path.name} 에 저장했다 (.bak 보관)")
        refresh()

    def do_revert() -> None:
        row = rows[state["index"]]
        row.ko = row.saved_ko
        editor.delete("1.0", "end")
        editor.insert("1.0", row.ko)
        refresh()

    ttk.Button(buttons, text="◀ 이전", command=lambda: step(-1)).pack(side="left")
    ttk.Button(buttons, text="다음 ▶", command=lambda: step(1)).pack(side="left")
    ttk.Button(buttons, text="자동 줄바꿈", command=do_wrap).pack(side="left", padx=12)
    ttk.Button(buttons, text="되돌리기", command=do_revert).pack(side="left")
    ttk.Button(buttons, text="저장", command=do_save).pack(side="right")

    filter_box.bind("<<ComboboxSelected>>",
                    lambda e: (state.update(filter=filter_box.get()), step(1)))
    editor.bind("<KeyRelease>", refresh)

    show(0)
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worksheet", type=Path)
    parser.add_argument("--field", type=int,
                        help="원본 렌더에 쓸 필드 번호. 생략하면 원본을 안 보여준다")
    parser.add_argument("--line-pixels", type=int, default=DEFAULT_LINE_PIXELS,
                        help=f"한 줄 최대 픽셀 폭 (기본 {DEFAULT_LINE_PIXELS}, 원본 실측)")
    parser.add_argument("--check", action="store_true",
                        help="GUI 없이 한계 초과만 보고한다")
    args = parser.parse_args()

    widths = glyph_widths(FT.SYSFNT.read_bytes())
    try:
        rows = load_rows(args.worksheet)
    except EditorError as error:
        parser.error(str(error))

    if args.check:
        return report(rows, widths, args.line_pixels)
    return launch(rows, args.worksheet, args.field, widths, args.line_pixels)


if __name__ == "__main__":
    raise SystemExit(main())
