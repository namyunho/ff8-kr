#!/usr/bin/env python3
"""FF8 Disc 1 구조 조사 도구 — 원본을 절대 쓰지 않는 읽기 전용 분석기.

명령:
  verify     원본 이미지 크기와 CRC32/MD5/SHA-256을 기준값과 대조한다.
  iso        바깥 ISO9660 디렉터리를 나열한다.
  toc        FF8DISC1.IMG 첫 섹터의 (LBA, size) 인덱스를 분류해 나열한다.
  extract    TOC 엔트리를 work/extracted/ 로 추출한다(LZSS면 해제).
  report     위 전부를 JSON 하나로 묶어 --output 에 쓴다.

좌표 규칙(섞지 않는다):
  - raw sector 좌표: 2352바이트 단위, 이미지 파일 오프셋 = lba * 2352
  - ISO 파일 좌표  : 2048바이트 단위, Mode 2 Form 1 유저 데이터
  - PS-X EXE 오프셋: 파일 +0x800 이 header 의 load address 에 적재
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BIN = PROJECT_ROOT / "roms" / "Final Fantasy VIII (Japan, Asia) (Disc 1).bin"
CONFIG = PROJECT_ROOT / "config" / "original-media.json"

RAW_SECTOR = 2352
USER_OFFSET = 0x18          # Mode 2 Form 1 유저 데이터 시작
USER_SIZE = 2048
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
IMG_LBA = 826               # FF8DISC1.IMG 시작 LBA (iso 명령으로 재확인 가능)


class Disc:
    """raw 2352바이트 섹터 이미지를 읽기 전용으로 다룬다."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = path.open("rb")
        self.size = path.stat().st_size
        self.sectors = self.size // RAW_SECTOR

    def raw(self, lba: int) -> bytes:
        self.handle.seek(lba * RAW_SECTOR)
        return self.handle.read(RAW_SECTOR)

    def sector_form(self, lba: int) -> dict:
        sector = self.raw(lba)
        subheader = sector[0x10:0x18]
        return {
            "mode": sector[0x0F],
            "form": 2 if subheader[2] & 0x20 else 1,
            "sync_ok": sector[:12] == SYNC,
            "subheader_duplicated": subheader[:4] == subheader[4:],
        }

    def user(self, lba: int) -> bytes:
        return self.raw(lba)[USER_OFFSET:USER_OFFSET + USER_SIZE]

    def read(self, lba: int, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            out += self.user(lba)
            lba += 1
        return bytes(out[:length])


def lzss_decompress(data: bytes) -> bytes:
    """FF8 이 쓰는 Okumura 계열 LZSS. 4096바이트 링버퍼, 초기 위치 0xFEE."""
    out = bytearray()
    window = bytearray(4096)
    pos = 0xFEE
    index = 0
    total = len(data)
    while index < total:
        flags = data[index]
        index += 1
        for bit in range(8):
            if index >= total:
                break
            if flags & (1 << bit):
                byte = data[index]
                index += 1
                out.append(byte)
                window[pos] = byte
                pos = (pos + 1) & 0xFFF
            else:
                if index + 1 >= total:
                    break
                low, high = data[index], data[index + 1]
                index += 2
                offset = low | ((high & 0xF0) << 4)
                length = (high & 0x0F) + 3
                for step in range(length):
                    byte = window[(offset + step) & 0xFFF]
                    out.append(byte)
                    window[pos] = byte
                    pos = (pos + 1) & 0xFFF
    return bytes(out)


def is_lzss(disc: Disc, lba: int, size: int) -> bool:
    """선두 u32 가 (전체 크기 - 4) 이면 LZSS 컨테이너다."""
    if size < 8:
        return False
    return struct.unpack("<I", disc.read(lba, 4))[0] == size - 4


def parse_directory(data: bytes) -> list[dict]:
    records: list[dict] = []
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset = (offset // USER_SIZE + 1) * USER_SIZE
            if offset >= len(data):
                break
            continue
        record = data[offset:offset + length]
        records.append(
            {
                "lba": struct.unpack("<I", record[2:6])[0],
                "size": struct.unpack("<I", record[10:14])[0],
                "dir": bool(record[25] & 0x02),
                "name": record[33:33 + record[32]].decode("ascii", "replace"),
            }
        )
        offset += length
    return records


def walk_iso(disc: Disc, lba: int, size: int, prefix: str,
             out: list[dict], depth: int = 0) -> None:
    for record in parse_directory(disc.read(lba, size)):
        name = record["name"]
        if name in ("\x00", "\x01"):
            continue
        name = name.split(";")[0]
        path = f"{prefix}/{name}"
        if record["dir"]:
            out.append({"path": path + "/", "lba": record["lba"],
                        "size": record["size"], "dir": True})
            if depth < 8:
                walk_iso(disc, record["lba"], record["size"], path, out, depth + 1)
        else:
            out.append({"path": path, "lba": record["lba"],
                        "size": record["size"], "dir": False})


def iso_entries(disc: Disc) -> list[dict]:
    pvd = disc.user(16)
    if pvd[1:6] != b"CD001":
        raise ValueError("PVD 를 찾지 못했다. Mode 2 Form 1 이미지가 맞는지 확인한다.")
    root = pvd[156:190]
    entries: list[dict] = []
    walk_iso(disc, struct.unpack("<I", root[2:6])[0],
             struct.unpack("<I", root[10:14])[0], "", entries)
    return entries


def classify(disc: Disc, lba: int, size: int) -> str:
    """엔트리 선두 바이트로 저장 종류를 판정한다. 확정이 아닌 것은 unknown 으로 둔다."""
    head = disc.read(lba, min(size, 512))
    if head[:8] == b"PS-X EXE":
        return "psx-exe"
    if head[:4] == b"AKAO":
        return "akao-sound"
    if is_lzss(disc, lba, size):
        return "lzss"
    if len(head) >= 8:
        first, second = struct.unpack("<II", head[:8])
        # sub_8002C358 이 받는 폰트 파일: [u32 표 오프셋][u32 TIM 오프셋]
        if 0 < first < 4096 and 8 <= second < len(head) - 8:
            if struct.unpack("<I", head[second:second + 4])[0] == 0x10:
                return "font"
        if first == 2 and second == 0x14:
            return "field-archive"
    if struct.unpack("<I", head[:4])[0] == 0x10:
        return "tim"
    if head[2:4] == b"\xbd\x27":
        return "mips-overlay"
    return "unknown"


def read_toc(disc: Disc, img_lba: int = IMG_LBA) -> list[dict]:
    table = disc.user(img_lba)
    entries: list[dict] = []
    for index in range(0, USER_SIZE, 8):
        lba, size = struct.unpack("<II", table[index:index + 8])
        if not lba or not size:
            continue
        entries.append(
            {
                "index": index // 8,
                "lba": lba,
                "size": size,
                "sectors": (size + USER_SIZE - 1) // USER_SIZE,
                "kind": classify(disc, lba, size),
            }
        )
    return entries


def image_identity(path: Path) -> dict:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    crc = 0
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 24)
            if not chunk:
                break
            md5.update(chunk)
            sha256.update(chunk)
            crc = zlib.crc32(chunk, crc)
            total += len(chunk)
    return {
        "path": str(path),
        "size": total,
        "sectors": total // RAW_SECTOR,
        "crc32": format(crc & 0xFFFFFFFF, "08X"),
        "md5": md5.hexdigest(),
        "sha256": sha256.hexdigest(),
    }


def load_expected() -> dict | None:
    if not CONFIG.is_file():
        return None
    return json.loads(CONFIG.read_text(encoding="utf-8")).get("disc1")


def cmd_verify(args) -> int:
    identity = image_identity(args.image)
    expected = load_expected()
    print(json.dumps(identity, indent=2))
    if not expected:
        print("기준값 파일이 없다. config/original-media.json 을 만든 뒤 다시 확인한다.",
              file=sys.stderr)
        return 0
    mismatched = [key for key in ("size", "crc32", "md5", "sha256")
                  if key in expected and expected[key] != identity[key]]
    if mismatched:
        print(f"기준값 불일치: {', '.join(mismatched)}", file=sys.stderr)
        return 1
    print("원본 기준값과 일치한다.")
    return 0


def cmd_iso(args) -> int:
    disc = Disc(args.image)
    print(f"# raw sector {disc.sectors:,} / sector0 {disc.sector_form(0)}")
    for entry in sorted(iso_entries(disc), key=lambda item: item["lba"]):
        print(f"{entry['lba']:>8} {entry['size']:>13,}  {entry['path']}")
    return 0


def cmd_toc(args) -> int:
    disc = Disc(args.image)
    entries = read_toc(disc, args.img_lba)
    print(f"# 유효 엔트리 {len(entries)}개")
    print(f"{'#':>4} {'LBA':>7} {'SIZE':>11} {'SECT':>5}  KIND")
    for entry in entries:
        print(f"{entry['index']:>4} {entry['lba']:>7} {entry['size']:>11,} "
              f"{entry['sectors']:>5}  {entry['kind']}")
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    print("\n# 종류별 개수")
    for kind, count in sorted(counts.items(), key=lambda item: -item[1]):
        print(f"  {kind:<16} {count}")
    return 0


def cmd_extract(args) -> int:
    disc = Disc(args.image)
    destination = args.destination
    destination.mkdir(parents=True, exist_ok=True)
    wanted = set(args.index) if args.index else None
    for entry in read_toc(disc, args.img_lba):
        if wanted is not None and entry["index"] not in wanted:
            continue
        payload = disc.read(entry["lba"], entry["size"])
        suffix = "bin"
        if entry["kind"] == "lzss":
            payload = lzss_decompress(payload[4:])
            suffix = "dec.bin"
        name = f"img_{entry['index']:03d}_lba{entry['lba']}.{suffix}"
        (destination / name).write_bytes(payload)
        print(f"{name}  {len(payload):,}B  ({entry['kind']})")
    return 0


def cmd_report(args) -> int:
    disc = Disc(args.image)
    report = {
        "image": image_identity(args.image),
        "sector_form_samples": {
            str(lba): disc.sector_form(lba) for lba in (0, 16, IMG_LBA)
        },
        "iso": iso_entries(disc),
        "toc": read_toc(disc, args.img_lba),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{args.output} 에 기록했다. "
          f"ISO {len(report['iso'])}개 / TOC {len(report['toc'])}개")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, default=DEFAULT_BIN)
    parser.add_argument("--img-lba", type=int, default=IMG_LBA)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify").set_defaults(func=cmd_verify)
    sub.add_parser("iso").set_defaults(func=cmd_iso)
    sub.add_parser("toc").set_defaults(func=cmd_toc)

    extract = sub.add_parser("extract")
    extract.add_argument("--index", type=int, nargs="*")
    extract.add_argument("--destination", type=Path,
                         default=PROJECT_ROOT / "work" / "extracted")
    extract.set_defaults(func=cmd_extract)

    report = sub.add_parser("report")
    report.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "work" / "analysis" / "disc1-structure.json")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"원본 이미지를 찾을 수 없다: {args.image}")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
