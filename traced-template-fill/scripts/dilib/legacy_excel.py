"""Read-only preflight for Excel COM. Only admit structurally identified BIFF5/8 data sheets."""
from __future__ import annotations

import os
from pathlib import Path
import struct


_MAX_BYTES = 64 * 1024 * 1024
_MAX_RECORDS = 2_000_000
_OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")


def ordinary_biff(data):
    """Check the globals, sheet directory, record boundaries and every substream BOF.

    Defined names and procedure/module records need formula-level inspection, so leave
    those workbooks to the passive reader or LibreOffice. Nothing here evaluates BIFF.
    """
    if not 4 <= len(data) <= _MAX_BYTES:
        return False
    offset = records = 0
    depth = 0
    declared, observed = {}, {}
    while offset + 4 <= len(data):
        record, length = struct.unpack_from("<HH", data, offset)
        if not depth and record == 0:
            # Excel sometimes pads the Workbook stream to the OLE mini-stream cutoff.
            if any(memoryview(data)[offset:]):
                return False
            offset = len(data)
            break
        start = offset + 4
        end = start + length
        records += 1
        if records > _MAX_RECORDS or length > 8224 or end > len(data):
            return False
        if record == 0x0809:  # BOF
            if length < 8:
                return False
            version, kind = struct.unpack_from("<HH", data, start)
            if version not in (0x0500, 0x0600):
                return False
            if not offset:
                if kind != 0x0005:  # workbook globals
                    return False
            elif not depth:
                if kind not in (0x0010, 0x0020):  # worksheet/dialog or chart
                    return False
                observed[offset] = kind
            elif kind != 0x0020:  # an embedded chart has its own substream
                return False
            depth += 1
        elif not depth:
            return False
        elif record == 0x000A:  # EOF
            if length:
                return False
            depth -= 1
        elif record == 0x0085:  # BoundSheet/BoundSheet8
            if depth != 1 or observed or length < 7:
                return False
            location, visibility, sheet_type = struct.unpack_from("<IBB", data, start)
            if visibility > 2 or sheet_type not in (0, 2) or location in declared:
                return False
            declared[location] = 0x0010 if sheet_type == 0 else 0x0020
        elif record in (0x002F, 0x0018, 0x0218, 0x00D3, 0x0009, 0x0209, 0x0409):
            # FILEPASS, defined names, VBA project or ambiguous earlier BOF.
            return False
        offset = end
    return offset == len(data) and depth == 0 and bool(declared) and declared == observed


def _workbook_stream(path):
    """Use Windows' read-only Structured Storage API for FAT, mini-FAT and directory resolution."""
    import ctypes as ct
    from contextlib import ExitStack
    from ctypes import wintypes as wt

    pointer = ct.c_void_p
    out_pointer = ct.POINTER(pointer)
    def method(obj, slot, *argtypes):
        vtable = ct.cast(obj, ct.POINTER(out_pointer)).contents
        return ct.WINFUNCTYPE(ct.c_long, pointer, *argtypes)(vtable[slot])

    ole = ct.WinDLL("ole32")
    open_storage = ole.StgOpenStorage
    open_storage.argtypes = (wt.LPCWSTR, pointer, wt.DWORD, pointer, wt.DWORD, out_pointer)
    open_storage.restype = ct.c_long
    storage = pointer()
    # STGM_READ | STGM_SHARE_DENY_WRITE, from the filesystem only, with no object activation.
    if open_storage(str(path), None, 0x20, None, 0, ct.byref(storage)) < 0:
        return None
    with ExitStack() as handles:
        handles.callback(method(storage, 2), storage)  # IUnknown::Release
        open_stream = method(storage, 4, wt.LPCWSTR, pointer, wt.DWORD, wt.DWORD, out_pointer)
        workbook = None
        for name in ("Workbook", "Book"):
            stream = pointer()
            result = open_stream(storage, name, None, 0x10, 0, ct.byref(stream))
            if result & 0xFFFFFFFF == 0x80030002:  # STG_E_FILENOTFOUND
                continue
            if result < 0:
                return None
            handles.callback(method(stream, 2), stream)
            if workbook is not None:
                return None  # ambiguous root streams
            workbook = stream
        if workbook is None:
            return None
        length = ct.c_ulonglong()
        seek = method(workbook, 5, ct.c_longlong, wt.DWORD, ct.POINTER(ct.c_ulonglong))
        if seek(workbook, 0, 2, ct.byref(length)) < 0 or not 0 < length.value <= _MAX_BYTES:
            return None
        if seek(workbook, 0, 0, None) < 0:
            return None
        buffer = ct.create_string_buffer(length.value)
        received = wt.DWORD()
        read = method(workbook, 3, pointer, wt.DWORD, ct.POINTER(wt.DWORD))
        if read(workbook, buffer, length.value, ct.byref(received)) < 0 or received.value != length.value:
            return None
        return buffer.raw


def ordinary_xls(src):
    if os.name != "nt":
        return False
    path = Path(src).absolute()
    try:
        if path.stat().st_size > _MAX_BYTES:
            return False
        with path.open("rb") as file:
            if file.read(8) != _OLE_HEADER:
                return False
        data = _workbook_stream(path)
    except (OSError, ValueError):
        return False
    return data is not None and ordinary_biff(data)
