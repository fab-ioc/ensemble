"""Work items named in text: Jira tickets and pull requests (round 2 step 2).

One small module with no hub state, so every source can call it (the Slack
router here, the Jira reader of round 2 step 3):

    find(text, repos=("example-repo",))  ->  [("pr", "example-repo#12"), ("ticket", "ABC-34")]

An item is ``(kind, key)`` in the form the routing table keeps
(``outside_route.parse_item``): a ticket key in upper case (``ABC-123``), a
pull request as ``repo#123`` in lower case, without its organisation.

What counts:

- **A link**: a GitHub pull request link (``…/org/repo/pull/123``, also
  inside Slack's ``<url|label>``) is a pull request; a Jira link
  (``…/browse/ABC-123``, or an Atlassian link with ``selectedIssue=ABC-123``)
  is a ticket.
- **``repo#123``** or ``org/repo#123``: a pull request, for any repo.
- **``repo 123``** (the form ``outside_route.short_links`` writes a link in,
  also ``repo PR 123``, ``repo #123`` and ``repo 123/124``): a pull request
  only for a repo in ``repos``, because "page 2" or "version 3" read the
  same way.
- **A ticket key** ``ABC-123`` written in capitals, also ``ABC-123/124``
  for two tickets of one project. A few capitals that are not ticket
  projects (``UTF-8``, ``SHA-256``, ``ISO-8601`` …) are left out. With
  ``prefixes`` given, only tickets of those projects count.

``find_in_name`` reads a branch or folder name (``abc_123_fix_the_thing``),
where a ticket is written in lower case with ``_`` or ``-``: only the
projects in ``prefixes`` count there.
"""
from __future__ import annotations

import re

TICKET = "ticket"
PR = "pr"

# Capitals followed by a dash and a number that are not ticket keys.
NOT_TICKETS = frozenset({
    "AES", "COVID", "CVE", "GPT", "HTTP", "IPV", "ISO", "MD", "RFC", "RSA", "SHA", "SSL",
    "TLS", "UTF", "WIN", "X", "Y", "Z",
})

_SLACK_LINK = re.compile(r"<(https?://[^>|\s]+)(?:\|[^>]*)?>")
_URL = re.compile(r"https?://[^\s<>()\"'|]+")
_GITHUB_PR = re.compile(r"^https?://(?:www\.)?github\.com/[^/\s]+/([^/\s]+)/pulls?/(\d+)", re.I)
_BROWSE = re.compile(r"/browse/([A-Za-z][A-Za-z0-9]+-\d+)", re.I)
_SELECTED = re.compile(r"[?&](?:selectedIssue|issueKey)=([A-Za-z][A-Za-z0-9]+-\d+)", re.I)
_HASH_PR = re.compile(r"(?<![\w/.#-])(?:[A-Za-z0-9_.-]+/)?([A-Za-z][A-Za-z0-9_.-]*)#(\d{1,7})(?![\w-])")
_TICKET = re.compile(r"(?<![A-Za-z0-9_/.-])([A-Z][A-Z0-9]{0,9})-(\d{1,7})((?:/\d{1,7})*)(?![A-Za-z0-9-])")
_NAME_TICKET = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{0,9})[-_](\d{2,7})(?![0-9])")


def _add(out: list, seen: set, kind: str, key: str) -> None:
    if key not in seen:
        seen.add(key)
        out.append((kind, key))


def _link_item(url: str):
    url = url.rstrip(".,;:!?")
    m = _GITHUB_PR.match(url)
    if m:
        return PR, f"{m.group(1).lower()}#{int(m.group(2))}"
    m = _BROWSE.search(url) or (_SELECTED.search(url) if "atlassian" in url.lower() else None)
    if m:
        return TICKET, m.group(1).upper()
    return None


def _repo_numbers(repos) -> re.Pattern | None:
    names = sorted({str(r).strip().lower() for r in repos or () if str(r).strip()}, key=len, reverse=True)
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?<![\w./-])({alt})\s+(?:(?:pr|pull request)\s*)?#?(\d{{1,7}})((?:/\d{{1,7}})*)(?![\w-])",
                      re.I)


def find(text: str, repos=(), prefixes=None) -> list[tuple[str, str]]:
    """The work items ``text`` names, each once: those in links first, then
    the others in the order they appear. ``[(kind, key)]`` with kind "ticket" or "pr" (see the module's
    words). ``repos``: the repos whose bare ``repo 123`` counts as a pull
    request. ``prefixes``: when given, the only ticket projects that count."""
    text = text or ""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    allowed = None if prefixes is None else {str(p).upper() for p in prefixes}

    def ticket(key: str) -> None:
        proj = key.split("-", 1)[0]
        if proj in NOT_TICKETS or (allowed is not None and proj not in allowed):
            return
        _add(out, seen, TICKET, key)

    # Links first, then taken out of the text so their parts are not read twice.
    def link(m):
        got = _link_item(m.group(1) if m.re is _SLACK_LINK else m.group(0))
        if got:
            if got[0] == TICKET:
                ticket(got[1])
            else:
                _add(out, seen, *got)
        return " "
    rest = _SLACK_LINK.sub(link, text)
    rest = _URL.sub(link, rest)
    found: list[tuple[int, str, str]] = []
    for m in _HASH_PR.finditer(rest):
        found.append((m.start(), PR, f"{m.group(1).lower()}#{int(m.group(2))}"))
    pat = _repo_numbers(repos)
    if pat:
        for m in pat.finditer(rest):
            repo = m.group(1).lower()
            for n in [m.group(2)] + [x for x in m.group(3).split("/") if x]:
                found.append((m.start(), PR, f"{repo}#{int(n)}"))
    for m in _TICKET.finditer(rest):
        for n in [m.group(2)] + [x for x in m.group(3).split("/") if x]:
            found.append((m.start(), TICKET, f"{m.group(1)}-{int(n)}"))
    for _pos, kind, key in sorted(found, key=lambda x: x[0]):
        if kind == TICKET:
            ticket(key)
        else:
            _add(out, seen, kind, key)
    return out


def find_in_name(name: str, prefixes) -> list[tuple[str, str]]:
    """Tickets in a branch or folder name (``abc_123_fix``, ``ABC-123``):
    only those of the projects in ``prefixes``."""
    allowed = {str(p).upper() for p in prefixes or ()}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _NAME_TICKET.finditer(name or ""):
        proj = m.group(1).upper()
        if proj in allowed and proj not in NOT_TICKETS:
            _add(out, seen, TICKET, f"{proj}-{int(m.group(2))}")
    return out


def prefix_of(key: str) -> str:
    """A ticket key's project (``ABC`` of ``ABC-12``)."""
    return key.split("-", 1)[0].upper() if "-" in key else ""


def repo_of(key: str) -> str:
    """A pull request key's repo (``example-repo`` of ``example-repo#12``)."""
    return key.split("#", 1)[0] if "#" in key else ""


def label(kind: str, key: str) -> str:
    """An item as the log names it: "pull request example-repo 12",
    "ticket ABC-123", "epic ABC-10"."""
    if kind == PR and "#" in key:
        repo, _, n = key.partition("#")
        return f"pull request {repo} {n}"
    return f"{'epic' if kind == 'epic' else 'ticket'} {key}"


_PR_LINK = re.compile(r"https?://(?:www\.)?github\.com/([^/\s|>]+)/([^/\s|>]+)/pulls?/(\d+)", re.I)


def pr_links(text: str) -> list[tuple[str, str, int]]:
    """``[(org, repo, number)]`` of the GitHub pull request links in
    ``text``: where a pull request's state can be read later (its key has
    no organisation)."""
    return [(m.group(1), m.group(2).lower(), int(m.group(3))) for m in _PR_LINK.finditer(text or "")]
