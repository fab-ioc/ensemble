"""Folder links (#282): every walk the hub makes over a folder stops at one.

A symlink, a Windows junction or a mount point leads to another folder. A walk
that follows one measures, lists or deletes what is not under the folder at all,
and a link to a folder above it never ends. On Windows a junction is not
``is_symlink()``, and ``os.walk`` and ``DirEntry.is_dir(follow_symlinks=False)``
both go into it. So every walk here asks :func:`entry_is_link` first: a link
is a leaf, never followed, counted as 0 bytes; deleting it removes the link and
leaves what it points to.

A reparse point counts as a link when Windows names it a name surrogate (it
stands for another path: symlinks, junctions, mount points). A cloud
placeholder (OneDrive) is a reparse point too but holds its own files, so it
stays a folder.
"""
from __future__ import annotations

import os
import stat
from typing import Callable, Iterator

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_NAME_SURROGATE = 0x20000000


def _link_stat(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    if getattr(st, "st_file_attributes", 0) & _REPARSE:
        return bool(getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE)
    return False


def entry_is_link(e: os.DirEntry) -> bool:
    """A listing's entry is a link (symlink, junction, mount point). One that
    cannot be read counts as one: never walked into."""
    try:
        if e.is_symlink() or getattr(e, "is_junction", lambda: False)():
            return True
        return os.name == "nt" and _link_stat(e.stat(follow_symlinks=False))
    except OSError:
        return True


def is_link(path) -> bool:
    """``path`` itself is a link; False when it is not there."""
    try:
        return _link_stat(os.lstat(path))
    except OSError:
        return False


def is_dir(e: os.DirEntry) -> bool:
    """A real folder to walk into: a folder and not a link to one."""
    try:
        return e.is_dir(follow_symlinks=False) and not entry_is_link(e)
    except OSError:
        return False


def walk(top, onerror: Callable[[OSError], None] | None = None) -> Iterator[tuple[str, list[str], list[str]]]:
    """``os.walk(top)`` (top-down; the caller may prune ``dirnames``) that
    never goes into a link: a linked folder is in neither list."""
    stack = [os.fspath(top)]
    while stack:
        d = stack.pop()
        dirs, files = [], []
        try:
            with os.scandir(d) as it:
                for e in it:
                    if entry_is_link(e):
                        try:
                            if not e.is_dir():         # a link to a file is a file
                                files.append(e.name)
                        except OSError:
                            pass
                    elif is_dir(e):
                        dirs.append(e.name)
                    else:
                        files.append(e.name)
        except OSError as err:
            if onerror:
                onerror(err)
            continue
        yield d, dirs, files
        stack.extend(os.path.join(d, n) for n in reversed(dirs))


def _unlink(p: str, folder: bool) -> None:
    """Remove one file or one link (a link to a folder goes with rmdir:
    the link, never what it points to). One already gone is fine."""
    try:
        os.rmdir(p) if folder else os.unlink(p)
    except FileNotFoundError:
        pass
    except PermissionError:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if folder else 0))
        os.rmdir(p) if folder else os.unlink(p)


def remove_tree(path) -> None:
    """Delete a file, a link or a whole folder, read-only files too (git's
    objects). A link anywhere in it is removed as a link: the walk never goes
    into it, so what it points to stays. Errors are raised, as rmtree's."""
    p = os.fspath(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return
    if _link_stat(st) or not stat.S_ISDIR(st.st_mode):
        _unlink(p, _folder_stat(st))
        return
    # Depth first, each folder emptied before it goes; a link is a leaf.
    stack: list[tuple[str, bool]] = [(p, False)]
    while stack:
        d, listed = stack.pop()
        if listed:
            _unlink(d, True)
            continue
        stack.append((d, True))
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except FileNotFoundError:
            continue
        for e in entries:
            if entry_is_link(e):
                _unlink(e.path, _entry_folder(e))
            elif is_dir(e):
                stack.append((e.path, False))
            else:
                _unlink(e.path, False)


def _folder_stat(st: os.stat_result) -> bool:
    """Windows keeps a junction or a folder symlink as a folder: it goes with
    rmdir. Elsewhere every link is unlinked."""
    return os.name == "nt" and bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY)


def _entry_folder(e: os.DirEntry) -> bool:
    try:
        return _folder_stat(e.stat(follow_symlinks=False))
    except OSError:
        return False
