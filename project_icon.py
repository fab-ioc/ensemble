"""A project's own icon (#329): an image the person uploads on the project's
settings, shown in place of its coloured square.

It is kept beside the project's ``project.json`` as ``project-icon.<ext>``;
the meta records ``icon`` = {file, name, size, v}. Accepted: PNG, JPEG, WebP
(by their magic bytes, whatever the name says) and SVG, which is checked
element by element and refused when it holds anything that runs or reaches
outside the file (scripts, event attributes, foreign content, outside links
and style urls). The hub serves it with a CSP that allows nothing anyway."""
from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET

MAX_BYTES = 1024 * 1024
# The JSON body of an upload: the file in base64 (4/3) and a little more.
MAX_BODY = MAX_BYTES * 4 // 3 + 64 * 1024
FILE_RE = re.compile(r"^project-icon\.(png|jpg|webp|svg)$")
TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}

REFUSALS = {
    "no_image": "Pick an image file.",
    "too_big": "That image is larger than 1 MB.",
    "bad_type": "Only a PNG, JPEG, SVG or WebP image can be an icon.",
    "unsafe_svg": "That SVG holds a script or a link to outside the file, so it cannot be an icon.",
    "bad_svg": "That SVG could not be read.",
}


class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def decode(data_url: str) -> bytes:
    """The bytes of a ``data:...;base64,...`` URL (or bare base64)."""
    s = (data_url or "").strip()
    if not s:
        raise Refused("no_image")
    if s.startswith("data:"):
        head, _, s = s.partition(",")
        if ";base64" not in head:
            raise Refused("bad_type")
    if len(s) > MAX_BYTES * 4 // 3 + 8:
        raise Refused("too_big")
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("bad_type")
    if not raw:
        raise Refused("no_image")
    if len(raw) > MAX_BYTES:
        raise Refused("too_big")
    return raw


def sniff(raw: bytes) -> str:
    """The extension the bytes really are: png, jpg, webp or svg."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<?xml", b"<svg", b"<!--", b"<!doctype")):
        if b"<svg" in raw[:65536].lower():
            return "svg"
    raise Refused("bad_type")


_BAD_TAGS = {"script", "foreignobject", "iframe", "embed", "object", "audio", "video",
             "handler", "listener", "set", "animate", "animatemotion", "animatetransform"}
_URL_RE = re.compile(r"url\s*\(\s*['\"]?\s*([^)'\"\s]*)", re.I)


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def _ok_ref(v: str) -> bool:
    v = (v or "").strip()
    return v == "" or v.startswith("#") or bool(re.match(r"data:image/(png|jpeg|webp|gif);base64,", v, re.I))


# CSS that could name something outside the file in a form the url() check
# would not see: an escape (u\72l(, @\69mport), any at-rule, and the
# functions that take a URL as a plain string.
_CSS_BAD_RE = re.compile(r"\\|@|\b(?:-webkit-)?image-set\s*\(|\bimage\s*\(|\bcross-fade\s*\(|\belement\s*\(", re.I)


def _ok_style(text: str) -> bool:
    text = text or ""
    if _CSS_BAD_RE.search(text):
        return False
    return all(_ok_ref(u) for u in _URL_RE.findall(text))


def check_svg(raw: bytes) -> None:
    """Refuse an SVG that could run anything or load anything from outside."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused("bad_svg")
    low = text.lower()
    # No DTD: no entities (a billion laughs), no outside entity either.
    if "<!doctype" in low or "<!entity" in low or "<?xml-stylesheet" in low:
        raise Refused("unsafe_svg")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise Refused("bad_svg")
    if _local(root.tag) != "svg":
        raise Refused("bad_svg")
    for el in root.iter():
        tag = _local(el.tag) if isinstance(el.tag, str) else ""
        if tag in _BAD_TAGS:
            raise Refused("unsafe_svg")
        if tag == "style" and not _ok_style("".join(el.itertext())):
            raise Refused("unsafe_svg")
        for k, v in el.attrib.items():
            name = _local(k)
            # xml:base turns a "#x" below it into an outside address; a
            # backslash is a CSS escape in a presentation attribute.
            if name == "base" or "\\" in (v or ""):
                raise Refused("unsafe_svg")
            if name.startswith("on"):
                raise Refused("unsafe_svg")
            if name in ("href", "src", "action", "formaction") and not _ok_ref(v):
                raise Refused("unsafe_svg")
            if name == "style" and not _ok_style(v):
                raise Refused("unsafe_svg")
            if "url(" in (v or "").lower() and not _ok_style(v):
                raise Refused("unsafe_svg")
            if re.match(r"\s*javascript:", v or "", re.I):
                raise Refused("unsafe_svg")


def accept(data_url: str) -> tuple[bytes, str]:
    """(bytes, extension) of an upload the hub may keep, or Refused."""
    raw = decode(data_url)
    ext = sniff(raw)
    if ext == "svg":
        check_svg(raw)
    return raw, ext
