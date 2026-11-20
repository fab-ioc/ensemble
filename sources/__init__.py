"""The outside sources the hub knows (design #1, section 1).

``TYPES`` maps a source type's kind to its class. Adding a service is one
module under ``sources/`` and one line here: step 2 adds
``"slack": slack.SlackSource``, round 2 step 3 adds ``"jira"``. ``LATER`` lists the types the Input sources
panel shows as "later" (revised plan, P15): with no module yet (Discord), or
built but not offered on the page (Jira: the PO dropped page work from #14,
P28; a Jira source is set up in the hub's sources file, not by the panel).

The made-up source in ``sources/fake.py`` is for tests and is not listed.
"""
from __future__ import annotations

from . import jira, slack
from .base import Source

TYPES: dict[str, type[Source]] = {"slack": slack.SlackSource, "jira": jira.JiraSource}

LATER: dict[str, str] = {"discord": "Discord", "jira": "Jira"}


def catalog() -> list[dict]:
    """Every type the Input sources panel offers under Add input source: the
    ones built, each with its fields, then the later ones."""
    out = [cls().describe() for kind, cls in sorted(TYPES.items()) if kind not in LATER]
    out += [{"kind": k, "label": v, "canSend": False, "ready": False, "fields": [],
             "routeFields": []} for k, v in LATER.items()]
    return out
