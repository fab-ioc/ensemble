"""Images pasted or dropped into a chat box, kept on the hub for the agent.

A chat's attachments live in ``attachments/`` inside the room's own folder (a
task's folder; a room without one gets ``<state dir>/attachments/<room id>``).
A pasted screenshot is named ``<yyyy-mm-dd HH.MM.SS> screenshot.png``, a
dropped file keeps its own name; nothing is ever overwritten (``-2``, ``-3``).

Only PNG, JPEG, GIF and WebP, told by their first bytes rather than by what the
browser claims, and at most MAX_BYTES each. A file is served back only by its
plain name inside that one folder, so no request reaches anything else.

The message that carries them ends with one ``[image] <absolute path>`` line
per image (message_refs.with_images), which is how the agent finds them.
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import time
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
DIR_NAME = "attachments"
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_EXT_ALIASES = {".jpeg": ".jpg", ".jpe": ".jpg"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
NAME_MAX = 120


class Refused(Exception):
    """Why an attachment was not stored or served: an HTTP status, a code and
    words for the person."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def payload(self) -> dict:
        return {"error": self.code, "message": self.message}


def sniff(head: bytes) -> str:
    """The extension an image's first bytes say it is (``.png``…), else ''."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return ""


def folder(room: dict, state_dir) -> Path:
    """Where a room's attachments go (not created here)."""
    base = str((room or {}).get("taskDir") or "").strip()
    if base and os.path.isdir(base):
        return Path(base) / DIR_NAME
    return Path(state_dir) / DIR_NAME / str((room or {}).get("id") or "room")


def file_name(given: str, ext: str, now: float | None = None) -> str:
    """The name to store under: the dropped file's own name made safe, with the
    extension its bytes call for; else ``<yyyy-mm-dd HH.MM.SS> screenshot``."""
    stem = ""
    if given:
        base = str(given).replace("\\", "/").rsplit("/", 1)[-1]
        stem, _dot, _old = base.rpartition(".")
        stem = _BAD.sub("_", stem if _dot else base).strip(" .")
        if stem.split(".")[0].lower() in _RESERVED:
            stem = "_" + stem
        stem = stem[:NAME_MAX]
    if not stem:
        stem = time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(now if now is not None else time.time())) + " screenshot"
    return stem + ext


def _claim(dest: Path, name: str) -> tuple[Path, object]:
    """Create ``name`` in ``dest`` exclusively, or ``stem-2``, ``stem-3``…:
    (path, open binary file)."""
    stem, ext = os.path.splitext(name)
    for n in range(1, 1000):
        path = dest / (name if n == 1 else f"{stem}-{n}{ext}")
        try:
            return path, open(path, "xb")
        except FileExistsError:
            continue
    raise Refused(409, "exists", "Too many files by that name already.")


def store(room: dict, state_dir, read, length: int, given_name: str = "", now: float | None = None) -> dict:
    """Store one image read from ``read(n)`` (``length`` bytes) for ``room``:
    ``{name, path, size, type}``. Refused before anything is written when it is
    too large or not an image; a body that ends early leaves nothing behind."""
    if length < 0:
        raise Refused(411, "length_required", "The upload did not say how large the image is.")
    if length == 0:
        raise Refused(400, "empty", "The image is empty.")
    if length > MAX_BYTES:
        raise Refused(413, "too_large", f"The image is {length / 1048576:.1f} MB; an image can be at most "
                                        f"{MAX_BYTES // 1048576} MB.")
    head = b""
    while len(head) < min(16, length):
        chunk = read(min(16, length) - len(head))
        if not chunk:
            break
        head += chunk
    ext = sniff(head)
    if not ext:
        raise Refused(415, "not_an_image", "Only a PNG, JPEG, GIF or WebP image can be attached.")
    dest = folder(room, state_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Refused(500, "not_stored", f"The image could not be stored: {e.strerror or e}.")
    path, fh = _claim(dest, file_name(given_name, ext, now))
    left, ok = length - len(head), False
    try:
        with fh:
            fh.write(head)
            while left > 0:
                chunk = read(min(left, 1024 * 1024))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        ok = left == 0 and len(head) == min(16, length)
    except OSError as e:
        raise Refused(500, "not_stored", f"The image could not be stored: {e.strerror or e}.")
    finally:
        if not ok:
            try:
                os.remove(path)
            except OSError:
                pass
    if not ok:
        raise Refused(400, "incomplete", "The image did not arrive in full, so it was not stored. Try again.")
    return {"name": path.name, "path": str(path), "size": length, "type": TYPES[ext]}


def find(room: dict, state_dir, name) -> Path:
    """The stored attachment ``name`` of ``room``, confined to its folder:
    a plain file name, an image extension, a regular file really inside."""
    name = str(name or "")
    if not name or name != name.strip(" .") or _BAD.search(name) or len(name) > 255:
        raise Refused(400, "bad_name", "That is not the name of an attachment.")
    ext = os.path.splitext(name)[1].lower()
    if _EXT_ALIASES.get(ext, ext) not in TYPES:
        raise Refused(400, "bad_name", "Only images are attachments.")
    dest = folder(room, state_dir)
    path = dest / name
    try:
        real, root = os.path.realpath(path), os.path.realpath(dest)
    except OSError:
        raise Refused(404, "not_found", "There is no such attachment.")
    if os.path.normcase(os.path.dirname(real)) != os.path.normcase(root) or not os.path.isfile(real):
        raise Refused(404, "not_found", "There is no such attachment.")
    return Path(real)


def content_type(path) -> str:
    ext = os.path.splitext(str(path))[1].lower()
    return TYPES[_EXT_ALIASES.get(ext, ext)]


def bring(room: dict, state_dir, src: Path) -> Path:
    """``src`` (another room's attachment) as a file of ``room``: itself when it
    is already there, else a copy under a name not yet taken."""
    dest = folder(room, state_dir)
    if os.path.normcase(os.path.realpath(src.parent)) == os.path.normcase(os.path.realpath(dest)):
        return src
    same = dest / src.name        # brought before (a retried send): that copy
    try:
        if same.is_file() and filecmp.cmp(src, same, shallow=False):
            return same
    except OSError:
        pass
    try:
        dest.mkdir(parents=True, exist_ok=True)
        path, fh = _claim(dest, src.name)
        with fh, open(src, "rb") as inp:
            shutil.copyfileobj(inp, fh)
    except OSError as e:
        raise Refused(500, "not_stored", f"The image could not be stored: {e.strerror or e}.")
    return path
