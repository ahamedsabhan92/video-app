"""Set the MP4 'encoder' tag (©too). ffmpeg ≥ 9 always writes its own "Lavf…" there, even when
-metadata encoder=… is given, so we patch the file afterwards.

Walks moov/udta/meta/ilst/©too/data, swaps the text, fixes every parent box size and, because
+faststart puts moov before mdat, shifts every chunk offset (stco/co64) by the size change.
"""
from __future__ import annotations

import struct
from pathlib import Path

CONTAINERS = {b"moov", b"udta", b"trak", b"mdia", b"minf", b"stbl", b"ilst"}


def _boxes(buf: bytes | bytearray, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", buf[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            raise ValueError(f"bad MP4 box {kind!r} at {i}")
        yield i, size, hdr, kind
        i += size


def _find(buf, start, end, path):
    """Offsets (box start) of the chain of boxes along path, e.g. [b'udta', b'meta', b'ilst']."""
    chain = []
    for name in path:
        for off, size, hdr, kind in _boxes(buf, start, end):
            if kind == name:
                chain.append((off, size, hdr))
                start, end = off + hdr + (4 if kind == b"meta" else 0), off + size  # meta is a full box
                break
        else:
            return None
    return chain


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, delta: int) -> None:
    for off, size, hdr, kind in _boxes(buf, start, end):
        if kind in CONTAINERS:
            _shift_chunk_offsets(buf, off + hdr, off + size, delta)
        elif kind in (b"stco", b"co64"):
            n = struct.unpack(">I", buf[off + hdr + 4:off + hdr + 8])[0]
            p = off + hdr + 8
            fmt, w = (">I", 4) if kind == b"stco" else (">Q", 8)
            for k in range(n):
                q = p + k * w
                buf[q:q + w] = struct.pack(fmt, struct.unpack(fmt, buf[q:q + w])[0] + delta)


def set_encoder_tag(path: Path, value: str) -> None:
    data = bytearray(path.read_bytes())
    top = {kind: (off, size, hdr) for off, size, hdr, kind in _boxes(data, 0, len(data))}
    if b"moov" not in top:
        raise ValueError("no moov box")
    moov_off, moov_size, moov_hdr = top[b"moov"]
    chain = _find(data, moov_off + moov_hdr, moov_off + moov_size, [b"udta", b"meta", b"ilst", b"\xa9too", b"data"])
    if chain is None:
        raise ValueError("no encoder tag to replace (was the file written with -fflags +bitexact?)")
    d_off, d_size, d_hdr = chain[-1]
    payload = d_off + d_hdr + 8                    # data box: type (4) + locale (4) + text
    new = value.encode("utf-8")
    delta = len(new) - (d_off + d_size - payload)
    data[payload:d_off + d_size] = new
    for off, size, hdr in [(moov_off, moov_size, moov_hdr), *chain]:
        if hdr != 8:
            raise ValueError("64-bit box sizes in moov aren't supported")
        data[off:off + 4] = struct.pack(">I", size + delta)
    mdat = top.get(b"mdat")
    if delta and mdat and mdat[0] > moov_off:      # faststart: media data moved by delta
        _shift_chunk_offsets(data, moov_off + moov_hdr, moov_off + moov_size + delta, delta)
    tmp = path.with_suffix(path.suffix + ".tag")
    tmp.write_bytes(data)
    tmp.replace(path)
