#!/usr/bin/env python3
"""Build a correctly based IDA database from a PS-X EXE."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDAT = Path("/Applications/IDA Professional 9.4.app/Contents/MacOS/idat")
LOADER_SCRIPT = PROJECT_ROOT / "scripts" / "ida" / "load_psx_exe.py"


def psx_exe_metadata(path: Path) -> dict[str, int]:
    with path.open("rb") as source:
        header = source.read(0x800)
    if header[:8] != b"PS-X EXE" or len(header) != 0x800:
        raise ValueError(f"not a complete PS-X EXE header: {path}")
    entry, gp, load_address, text_size = struct.unpack_from("<4I", header, 0x10)
    if path.stat().st_size < 0x800 + text_size:
        raise ValueError(f"truncated PS-X EXE payload: {path}")
    return {
        "entry": entry,
        "gp": gp,
        "load_address": load_address,
        "text_size": text_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--idat", type=Path, default=DEFAULT_IDAT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    input_path = args.input.resolve()
    metadata = psx_exe_metadata(input_path)
    output = (
        args.output.resolve()
        if args.output
        else PROJECT_ROOT / "work" / "ida" / f"{input_path.name}.psx.i64"
    )
    if output.exists() and not args.force:
        parser.error(f"output already exists (use --force): {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.idat.is_file():
        parser.error(f"IDA headless executable not found: {args.idat}")

    environment = os.environ.copy()
    environment["PSX_IDB_OUTPUT"] = str(output)
    log_path = output.with_suffix(output.suffix + ".log")
    command = [
        str(args.idat),
        "-A",
        "-c",
        f"-L{log_path}",
        f"-o{output}",
        f"-S{LOADER_SCRIPT}",
        str(input_path),
    ]
    result = subprocess.run(command, env=environment, check=False)
    if result.returncode:
        if log_path.is_file():
            sys.stderr.write(log_path.read_text(encoding="utf-8", errors="replace"))
        raise SystemExit(result.returncode)
    if not output.is_file():
        parser.error(f"IDA exited without creating the database: {output}")
    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output),
                **metadata,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
