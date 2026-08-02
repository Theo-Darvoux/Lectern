"""Lossless JPEG metadata removal with complete multi-scan marker parsing."""

from __future__ import annotations

from app.core.security.file_security.errors import SanitizationError

_JPEG_SOI = b"\xff\xd8"
_STANDALONE_MARKERS = frozenset({0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)})
_PRIVATE_MARKERS = frozenset({0xE1, 0xE2, 0xED, 0xFE})  # EXIF/XMP, ICC, IPTC, comments


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Remove privacy-bearing JPEG segments without decoding image pixels.

    The parser follows markers across every scan in baseline and progressive
    JPEGs. Metadata inserted between scans is removed as well as metadata in the
    conventional header area. Malformed streams fail closed.
    """
    if not data.startswith(_JPEG_SOI):
        raise SanitizationError("Embedded DCT stream is not a valid JPEG")

    output = bytearray(_JPEG_SOI)
    offset = len(_JPEG_SOI)
    in_scan = False
    scan_start = offset

    while offset < len(data):
        if in_scan:
            marker_start = data.find(b"\xff", offset)
            if marker_start < 0:
                raise SanitizationError("JPEG scan is missing an end marker")

            marker_end = marker_start + 1
            while marker_end < len(data) and data[marker_end] == 0xFF:
                marker_end += 1
            if marker_end >= len(data):
                raise SanitizationError("JPEG ends inside a marker")

            marker = data[marker_end]
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                offset = marker_end + 1
                continue

            output.extend(data[scan_start:marker_start])
            offset = marker_start
            in_scan = False
            continue

        if data[offset] != 0xFF:
            raise SanitizationError("JPEG contains bytes outside a marker or scan")

        marker_start = offset
        marker_end = marker_start + 1
        while marker_end < len(data) and data[marker_end] == 0xFF:
            marker_end += 1
        if marker_end >= len(data):
            raise SanitizationError("JPEG ends inside a marker")

        marker = data[marker_end]
        offset = marker_end + 1
        if marker == 0x00:
            raise SanitizationError("JPEG contains a stuffed byte outside scan data")

        if marker in _STANDALONE_MARKERS:
            output.extend(data[marker_start:offset])
            if marker == 0xD9:
                return bytes(output)
            continue

        if offset + 2 > len(data):
            raise SanitizationError("JPEG segment is missing its length")
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2:
            raise SanitizationError("JPEG segment has an invalid length")
        segment_end = offset + segment_length
        if segment_end > len(data):
            raise SanitizationError("JPEG segment extends beyond the input")

        if marker not in _PRIVATE_MARKERS:
            output.extend(data[marker_start:segment_end])

        offset = segment_end
        if marker == 0xDA:  # Start of Scan
            in_scan = True
            scan_start = offset

    raise SanitizationError("JPEG is missing its end-of-image marker")
