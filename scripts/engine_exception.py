# SPDX-License-Identifier: Apache-2.0
"""Fixed exception categories and source coordinates; never serialize content."""

import hashlib
import sys
import types

EXCEPTION_TYPES = (None, ModuleNotFoundError, ImportError, ValueError, RuntimeError,
                   AssertionError, TypeError, AttributeError, KeyError, OSError,
                   PermissionError, TimeoutError, SystemExit)
EXCEPTION_NAMES = ("none", "module-not-found", "import", "value", "runtime", "assertion",
                   "type", "attribute", "key", "os", "permission", "timeout", "system-exit", "other")
EXCEPTION_FRAMES = 8
EXCEPTION_PACKET_BYTES = 3 + EXCEPTION_FRAMES * 36


def validation_causes(error):
    """Inspect only the already-loaded sealed Pydantic class, never duck types."""
    module = sys.modules.get("pydantic_core")
    if type(module) is not types.ModuleType:
        return ()
    cls = vars(module).get("ValidationError")
    if cls is None or type(error) is not cls:
        return ()
    count = cls.error_count(error)
    require(type(count) is int and 0 <= count <= 8)
    rows = cls.errors(error, include_url=False, include_context=True, include_input=False)
    require(type(rows) is list and len(rows) == count)
    causes = []
    for row in rows:
        require(type(row) is dict)
        context = row.get("ctx")
        if type(context) is dict:
            cause = context.get("error")
            # Pydantic wraps these validators and retains their traceback.
            # Do not persist any error dict, input, message, context or location.
            if type(cause) in (ValueError, AssertionError):
                causes.append(cause)
    return tuple(causes)


def exception_packet(error, roots):
    """Fixed classes and source digests only; never format an exception or frame."""
    if error is None:
        return bytes(EXCEPTION_PACKET_BYTES)
    code = next((i for i, cls in enumerate(EXCEPTION_TYPES[1:], 1) if type(error) is cls),
                len(EXCEPTION_TYPES))
    frames, flags = [], 0
    try:
        require(type(roots) is tuple and len(roots) <= 5
                and all(type(root) is str and root.startswith("/") and root.endswith("/") for root in roots))
        scanned = 0
        causes = (error,)
        try:
            causes += validation_causes(error)
        except BaseException:
            flags |= 4
        for cause in causes:
            tb = BaseException.__traceback__.__get__(cause)
            while tb is not None and scanned < 64:
                require(type(tb) is types.TracebackType)
                scanned += 1
                path, line = tb.tb_frame.f_code.co_filename, tb.tb_lineno
                if type(path) is str and any(path.startswith(root) for root in roots) and 0 < line <= 1000000:
                    try:
                        source = read_source(path, 2 << 20)
                        frames.append(line.to_bytes(4, "big") + hashlib.sha256(source).digest())
                        if len(frames) > EXCEPTION_FRAMES:
                            del frames[0]
                            flags |= 1
                    except Exception:
                        flags |= 2
                else:
                    flags |= 2
                tb = tb.tb_next
            if tb is not None:
                flags |= 1
    except BaseException:
        flags |= 4
    return bytes([code, flags, len(frames)]) + b"".join(frames) + bytes(36 * (EXCEPTION_FRAMES - len(frames)))


def decode_exception(raw):
    require(type(raw) is bytes and len(raw) == EXCEPTION_PACKET_BYTES)
    code, flags, count = raw[:3]
    require(code < len(EXCEPTION_NAMES) and flags < 8 and count <= EXCEPTION_FRAMES)
    require(not any(raw[3 + count * 36:]))
    if code == 0:
        require(flags == 0 and count == 0)
        return None
    frames = []
    for i in range(count):
        offset = 3 + i * 36
        line = int.from_bytes(raw[offset:offset + 4], "big")
        digest = raw[offset + 4:offset + 36]
        require(0 < line <= 1000000 and any(digest))
        frames.append({"source_sha256": digest.hex(), "line": line})
    return {"type": EXCEPTION_NAMES[code], "frames": frames, "truncated": bool(flags & 1),
            "source_unavailable": bool(flags & 2), "observer_failed": bool(flags & 4)}


def validate_exception(row):
    if row is None:
        return
    require(type(row) is dict and set(row) == {"type", "frames", "truncated", "source_unavailable", "observer_failed"}
            and type(row["type"]) is str and row["type"] in EXCEPTION_NAMES[1:]
            and type(row["frames"]) is list and len(row["frames"]) <= EXCEPTION_FRAMES
            and all(type(row[key]) is bool for key in ("truncated", "source_unavailable", "observer_failed")))
    for frame in row["frames"]:
        require(type(frame) is dict and set(frame) == {"source_sha256", "line"}
                and type(frame["line"]) is int and 0 < frame["line"] <= 1000000
                and type(frame["source_sha256"]) is str and len(frame["source_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in frame["source_sha256"])
                and frame["source_sha256"] != "0" * 64)
