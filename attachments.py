"""Files pasted, dropped or picked into a chat box, kept on the hub for the agent.

A chat's attachments live in ``attachments/`` inside the room's own folder (a
task's folder; a room without one gets ``<state dir>/attachments/<room id>``).
A pasted screenshot is named ``<yyyy-mm-dd HH.MM.SS> screenshot.png``, any
other file keeps its own name; nothing is ever overwritten (``-2``, ``-3``).

Any file, at most MAX_BYTES each. An image (PNG, JPEG, GIF or WebP) is told by
its first bytes rather than by what the browser claims, and stored with the
extension they call for; any other file keeps its own name and extension, made
safe (no folders, no characters a file system refuses). A file named like an
image that is not one is refused. A file is served back only by its plain name
inside that one folder, so no request reaches anything else; only an image or
a PDF is shown inline, everything else is a download (:func:`disposition`).

The message that carries them ends with one ``[image] <absolute path>`` line
per image and one ``[file] <absolute path>`` line per other file
(message_refs.with_images), which is how the agent finds them.
"""
from __future__ import annotations

import filecmp
import glob
import os
import re
import shutil
import time
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
DIR_NAME = "attachments"
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_EXT_ALIASES = {".jpeg": ".jpg", ".jpe": ".jpg"}
# What a stored file that is not an image is served as. Text kinds go out as
# plain text (never as markup); anything unknown as bytes.
_TEXT = "text/plain; charset=utf-8"
OTHER_TYPES = {".pdf": "application/pdf", ".txt": _TEXT, ".md": _TEXT, ".log": _TEXT,
               ".csv": "text/csv; charset=utf-8", ".tsv": "text/tab-separated-values; charset=utf-8",
               ".json": "application/json", ".zip": "application/zip",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
# Shown by the browser itself (a thumbnail, its PDF viewer); every other kind,
# HTML, SVG and scripts among them, only ever downloads.
INLINE = {*TYPES, ".pdf"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NOT_EXT = re.compile(r"[^\w-]")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
NAME_MAX = 120
EXT_MAX = 16


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


def _ext(name) -> str:
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    # A name that is only a dot and an extension (".png") is that type too.
    ext = (base if base.startswith(".") and base.count(".") == 1 else os.path.splitext(base)[1]).lower()
    return _EXT_ALIASES.get(ext, ext)


def is_image_name(name) -> bool:
    """Whether a stored file's name is an image's: an ``[image]`` line and a
    thumbnail, rather than a ``[file]`` line and a chip."""
    return _ext(name) in TYPES


def folder(room: dict, state_dir) -> Path:
    """Where a room's attachments go (not created here)."""
    base = str((room or {}).get("taskDir") or "").strip()
    if base and os.path.isdir(base):
        return Path(base) / DIR_NAME
    return Path(state_dir) / DIR_NAME / str((room or {}).get("id") or "room")


def _safe_stem(stem: str) -> str:
    stem = _BAD.sub("_", stem).strip(" ").rstrip(" .")   # a leading dot stays (".env")
    if stem.split(".")[0].lower() in _RESERVED:
        stem = "_" + stem
    return stem[:NAME_MAX].rstrip(" .")


def _stamp(now: float | None) -> str:
    return time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(now if now is not None else time.time()))


def file_name(given: str, ext: str, now: float | None = None) -> str:
    """The name to store an image under: the dropped file's own name made safe,
    with the extension its bytes call for; else ``<yyyy-mm-dd HH.MM.SS>
    screenshot``."""
    stem = ""
    if given:
        base = str(given).replace("\\", "/").rsplit("/", 1)[-1]
        stem, _dot, _old = base.rpartition(".")
        stem = _safe_stem(stem if _dot else base)
    if not stem:
        stem = _stamp(now) + " screenshot"
    return stem + ext


def other_name(given: str, now: float | None = None) -> str:
    """The name a file that is not an image is stored under: its own name made
    safe (no folders, no characters a file system refuses, an extension only of
    letters, digits, ``_`` and ``-``), else ``<yyyy-mm-dd HH.MM.SS> file``."""
    base = str(given or "").replace("\\", "/").rsplit("/", 1)[-1].strip(" ").rstrip(" .")
    stem, ext = os.path.splitext(base)
    ext = ext[1:]
    if ext and (len(ext) > EXT_MAX or _NOT_EXT.search(ext)):
        stem, ext = base, ""          # not an extension: part of the name
    stem = _safe_stem(stem)
    if not stem:
        stem = _stamp(now) + " file"
    return stem + ("." + ext if ext else "")


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


def too_large(length: int) -> str:
    """The words for a file over the limit."""
    return f"The file is {length / 1048576:.1f} MB; a file can be at most {MAX_BYTES // 1048576} MB."


def store(room: dict, state_dir, read, length: int, given_name: str = "", now: float | None = None) -> dict:
    """Store one file read from ``read(n)`` (``length`` bytes) for ``room``:
    ``{name, path, size, type, image}``. Refused before anything is written
    when it is too large, empty, or named like an image without being one; a
    body that ends early leaves nothing behind."""
    if length < 0:
        raise Refused(411, "length_required", "The upload did not say how large the file is.")
    if length == 0:
        raise Refused(400, "empty", "The file is empty.")
    if length > MAX_BYTES:
        raise Refused(413, "too_large", too_large(length))
    head = b""
    while len(head) < min(16, length):
        chunk = read(min(16, length) - len(head))
        if not chunk:
            break
        head += chunk
    ext = sniff(head)
    if ext:
        name = file_name(given_name, ext, now)
    else:
        name = other_name(given_name, now)
        if is_image_name(name):
            raise Refused(415, "not_an_image", f"{name} is named like an image but is not one, so it was not "
                                               "attached. Attach the real image, or rename the file.")
    dest = folder(room, state_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Refused(500, "not_stored", f"The file could not be stored: {e.strerror or e}.")
    path, fh = _claim(dest, name)
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
        raise Refused(500, "not_stored", f"The file could not be stored: {e.strerror or e}.")
    finally:
        if not ok:
            try:
                os.remove(path)
            except OSError:
                pass
    if not ok:
        raise Refused(400, "incomplete", "The file did not arrive in full, so it was not stored. Try again.")
    return {"name": path.name, "path": str(path), "size": length, "type": content_type(path), "image": bool(ext)}


def find(room: dict, state_dir, name) -> Path:
    """The stored attachment ``name`` of ``room``, confined to its folder:
    a plain file name, a regular file really inside."""
    name = str(name or "")
    if not name or name != name.strip(" ").rstrip(" .") or _BAD.search(name) or len(name) > 255:
        raise Refused(400, "bad_name", "That is not the name of an attachment.")
    dest = folder(room, state_dir)
    path = dest / name
    try:
        real, root = os.path.realpath(path), os.path.realpath(dest)
    except OSError:
        raise Refused(404, "not_found", "There is no such attachment.")
    if os.path.normcase(os.path.dirname(real)) != os.path.normcase(root) or not os.path.isfile(real):
        raise Refused(404, "not_found", "There is no such attachment.")
    return Path(real)


def is_attachment_path(path, state_dir) -> bool:
    """Whether ``path`` is where some chat keeps an attachment: directly in a
    task folder's ``attachments/``, or in ``<state dir>/attachments/<room id>/``
    (a chat with no task folder, such as a project's PO)."""
    p = Path(str(path))
    if p.parent.name == DIR_NAME:
        return True
    try:
        return os.path.normcase(os.path.abspath(p.parent.parent)) == os.path.normcase(os.path.abspath(Path(state_dir) / DIR_NAME))
    except (OSError, ValueError):
        return False


def content_type(path) -> str:
    """What a stored file is served as: an image's type, a few known kinds',
    else bytes; never a page's, a drawing's or a script's."""
    ext = _ext(path)
    return TYPES.get(ext) or OTHER_TYPES.get(ext) or "application/octet-stream"


def disposition(path, download: bool = False) -> str:
    """``inline`` for an image or a PDF (unless a download is asked for), else
    ``attachment``: an uploaded page, drawing or script is never shown by the
    hub's own origin."""
    return "inline" if not download and _ext(path) in INLINE else "attachment"


def bring(room: dict, state_dir, src: Path) -> Path:
    """``src`` (another room's attachment) as a file of ``room``: itself when it
    is already there, else a copy under a name not yet taken."""
    dest = folder(room, state_dir)
    if os.path.normcase(os.path.realpath(src.parent)) == os.path.normcase(os.path.realpath(dest)):
        return src
    # Brought before (a retried send), under its name or a numbered one: that copy.
    stem, ext = os.path.splitext(src.name)
    try:
        for same in [dest / src.name, *sorted(dest.glob(f"{glob.escape(stem)}-*{glob.escape(ext)}"))]:
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
        raise Refused(500, "not_stored", f"The file could not be stored: {e.strerror or e}.")
    return path
