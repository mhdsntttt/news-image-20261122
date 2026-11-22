#!/usr/bin/env python3
"""Stage the rendered news-image entry.

Copies the entry image next to this script into the output directory after
checking that it is a complete PNG of acceptable size. Standard library only,
no network access, deterministic output.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MAX_BYTES = 1_048_576
MAX_DIMENSION = 1024

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IHDR_TYPE = b"IHDR"
IEND_TYPE = b"IEND"
JPEG_MAGIC = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
JPEG_SOS = 0xDA
JPEG_SOF_MARKERS = frozenset(
    (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
)


class ValidationError(Exception):
    """Raised when the entry image fails a structural check."""


def parse_png_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) from the first IHDR chunk of *data*."""
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        raise ValidationError("not a PNG: bad signature")

    offset = 8
    found_ihdr = False
    saw_iend = False
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        body_start = offset + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise ValidationError("truncated chunk: file is not a whole PNG")
        if chunk_type == IHDR_TYPE and not found_ihdr:
            if length < 8:
                raise ValidationError("IHDR chunk is too short")
            width = int.from_bytes(data[body_start : body_start + 4], "big")
            height = int.from_bytes(data[body_start + 4 : body_start + 8], "big")
            found_ihdr = True
        if chunk_type == IEND_TYPE:
            saw_iend = True
            break
        offset = body_end + 4  # skip body + CRC

    if not found_ihdr:
        raise ValidationError("PNG has no IHDR chunk")
    if not saw_iend:
        raise ValidationError("PNG has no terminating IEND chunk")
    if offset + 8 + 4 != len(data):
        raise ValidationError("trailing bytes after IEND chunk")
    return width, height


def parse_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) from the first SOF frame header of *data*."""
    if len(data) < 4 or data[:3] != JPEG_MAGIC:
        raise ValidationError("not a JPEG: bad magic bytes")

    pos = 2
    saw_sof = False
    width = height = 0
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9 or marker == JPEG_SOS:
            break
        if pos + 4 > len(data):
            raise ValidationError("truncated JPEG segment")
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if length < 2:
            raise ValidationError("invalid JPEG segment length")
        segment_end = pos + 2 + length
        if segment_end > len(data):
            raise ValidationError("truncated JPEG segment: file is not whole")
        if marker in JPEG_SOF_MARKERS and not saw_sof:
            if length < 7:
                raise ValidationError("JPEG SOF segment is too short")
            height = int.from_bytes(data[pos + 5 : pos + 7], "big")
            width = int.from_bytes(data[pos + 7 : pos + 9], "big")
            saw_sof = True
        pos = segment_end

    if not saw_sof:
        raise ValidationError("JPEG has no SOF frame header")
    if data[-2:] != JPEG_EOI:
        raise ValidationError("JPEG has no EOI terminator")
    return width, height


def detect_format(data: bytes) -> str:
    """Return "png" or "jpeg" for a recognised image, else raise."""
    if data[:8] == PNG_SIGNATURE:
        return "png"
    if data[:3] == JPEG_MAGIC:
        return "jpeg"
    raise ValidationError("unrecognised image format: expected PNG or JPEG")


def validate_entry(data: bytes) -> None:
    """Reject entries that are oversized or larger than the allowed pixels."""
    if len(data) > MAX_BYTES:
        raise ValidationError(
            f"entry is {len(data)} bytes, over the {MAX_BYTES} byte limit"
        )
    fmt = detect_format(data)
    if fmt == "png":
        width, height = parse_png_dimensions(data)
    else:
        width, height = parse_jpeg_dimensions(data)
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValidationError(
            f"entry is {width}x{height}, over the {MAX_DIMENSION}x{MAX_DIMENSION} limit"
        )


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    candidates = [script_dir / "entry.png", script_dir / "entry.jpg"]
    source = next((path for path in candidates if path.is_file()), candidates[0])
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

    try:
        if not source.is_file():
            raise ValidationError(f"missing entry image at {source}")
        data = source.read_bytes()
        validate_entry(data)

        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / source.name
        with open(target, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        written = target.read_bytes()
        if written != data:
            raise ValidationError("output bytes do not match the source image")
    except (ValidationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"staged {len(data)} bytes to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
