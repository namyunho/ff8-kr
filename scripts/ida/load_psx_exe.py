"""IDAPython loader fixup for a PS-X EXE opened as a raw binary.

Run through ``scripts/build_ida_db.py``. The wrapper supplies
``PSX_IDB_OUTPUT`` and starts IDA in autonomous mode.
"""

from __future__ import annotations

import os
from pathlib import Path
import struct
import traceback

import ida_auto
import ida_bytes
import ida_entry
import ida_funcs
import ida_idp
import ida_loader
import ida_name
import ida_nalt
import ida_pro
import ida_segment
from ida_idaapi import BADADDR


def segment_starts() -> list[int]:
    starts = []
    start = ida_segment.get_first_segment_ea()
    while start != BADADDR:
        starts.append(start)
        start = ida_segment.get_next_segment_ea(start)
    return starts


def add_segment(start: int, end: int, name: str, segment_class: str, perm: int) -> None:
    segment = ida_segment.segment_info_t()
    segment.start_ea = start
    segment.end_ea = end
    segment.set_name(name)
    segment.set_sclass(segment_class)
    segment.set_bitness(1)  # 32-bit
    segment.set_align(ida_segment.saRelPara)
    segment.set_comb(ida_segment.scPub)
    segment.set_perm(perm)
    if not ida_segment.add_segment_ex(segment, ida_segment.ADDSEG_NOSREG):
        raise RuntimeError(f"failed to add {name} segment {start:#x}..{end:#x}")


def load_psx_exe() -> None:
    input_name = ida_nalt.get_input_file_path()
    output_name = os.environ.get("PSX_IDB_OUTPUT")
    if not input_name or not output_name:
        raise RuntimeError("input path or PSX_IDB_OUTPUT is missing")

    input_path = Path(input_name)
    data = input_path.read_bytes()
    if data[:8] != b"PS-X EXE" or len(data) < 0x800:
        raise ValueError(f"not a PS-X EXE: {input_path}")

    (
        entry,
        gp,
        load_address,
        text_size,
        data_address,
        data_size,
        bss_address,
        bss_size,
        stack_address,
        stack_size,
    ) = struct.unpack_from("<10I", data, 0x10)
    payload = data[0x800 : 0x800 + text_size]
    if len(payload) != text_size:
        raise ValueError(
            f"truncated PS-X EXE payload: expected {text_size:#x}, got {len(payload):#x}"
        )

    text_end = load_address + text_size
    mapped_prefix = ida_bytes.get_bytes(load_address, min(32, text_size))
    native_mapping = mapped_prefix == payload[: len(mapped_prefix or b"")]
    if not native_mapping:
        for start in segment_starts():
            if not ida_segment.del_segm(start, ida_segment.SEGMOD_KILL):
                raise RuntimeError(f"failed to remove raw segment at {start:#x}")

        if not ida_idp.set_processor_type("mipsl", ida_idp.SETPROC_LOADER):
            raise RuntimeError("IDA rejected the mipsl processor")

        add_segment(
            load_address,
            text_end,
            ".text",
            "CODE",
            ida_segment.SEGPERM_READ | ida_segment.SEGPERM_EXEC,
        )
        mapped = ida_bytes.put_bytes(load_address, payload)
        if mapped is False:
            raise RuntimeError("failed to map PS-X EXE payload")
        ida_nalt.set_imagebase(load_address)

    if bss_size:
        bss_end = bss_address + bss_size
        if not (bss_end <= load_address or bss_address >= text_end):
            raise ValueError("PS-X EXE BSS overlaps the text payload")
        add_segment(
            bss_address,
            bss_end,
            ".bss",
            "BSS",
            ida_segment.SEGPERM_READ | ida_segment.SEGPERM_WRITE,
        )

    ida_entry.add_entry(entry, entry, "_start", True)
    ida_name.set_name(entry, "_start", ida_name.SN_NOCHECK)
    ida_bytes.set_cmt(
        entry,
        (
            f"PS-X EXE entry; gp={gp:#x}, data={data_address:#x}+{data_size:#x}, "
            f"bss={bss_address:#x}+{bss_size:#x}, "
            f"stack={stack_address:#x}+{stack_size:#x}"
        ),
        True,
    )
    ida_auto.plan_and_wait(load_address, text_end)
    if ida_funcs.get_func(entry) is None and not ida_funcs.add_func(entry):
        raise RuntimeError(f"failed to define entry function at {entry:#x}")
    ida_auto.auto_wait()
    saved = ida_loader.save_database(output_name, 0)
    if saved is False:
        raise RuntimeError(f"failed to save IDB: {output_name}")


try:
    load_psx_exe()
except Exception:
    traceback.print_exc()
    ida_pro.qexit(1)
else:
    ida_pro.qexit(0)
