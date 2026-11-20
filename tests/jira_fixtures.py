"""Made-up Jira answers for the Jira reader's tests, in the shape Claude's
Atlassian connection answers (seen on a real site, 2026-11-17; every id,
name, site and key here is made up: the repo is public)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sources.jira_connector import TOOL_FOR, ReadResult, add_usage, read_stream

PREFIX = "mcp__claude_ai_Atlassian_MCP__"
ME = "000000:00000000-0000-4000-8000-000000000001"
OTHER = "000000:00000000-0000-4000-8000-000000000002"
BOT = "000000:00000000-0000-4000-8000-000000000003"
MY_NAME = "Me Example"
OTHER_NAME = "Other Person"
BOT_NAME = "Release Bot"
SITE = "example.atlassian.net"
CLOUD = "00000000-0000-4000-8000-0000000000aa"

ALL_TOOLS = ["addGraphContext", "addOrEditJiraIssueComment", "atlassianHome", "atlassianUserInfo",
             "createConfluenceContent", "createJiraIssue", "discover", "editJiraIssue",
             "executeDestructive", "executeRead", "executeWrite", "getAccessibleAtlassianResources",
             "getCollaborators", "getConfluenceContent", "getGraphContext", "getGraphObject", "getJiraIssue",
             "getLoomObjectsContent", "getPeopleInfo", "getWorkActivity", "getWorkPortfolio",
             "listJiraIssueComments", "search", "searchConfluence", "searchJiraIssuesUsingJql",
             "showConfluenceEditor", "transitionJiraIssue", "updateConfluenceContent"]


def jtime(ts: float) -> str:
    """A Jira time ("2026-11-16T11:50:48.744+0000") of seconds since 1970."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def person(aid, name):
    # The real answer carries an e-mail address too: the reader must never keep it.
    return {"accountId": aid, "displayName": name, "emailAddress": f"{name.split()[0].lower()}@example.com",
            "active": True}


def issue(key, summary, updated, created, status="To Do", assignee=(ME, MY_NAME), reporter=(OTHER, OTHER_NAME),
          epic=("ABC-10", "Price feed"), components=(), kind="Task"):
    f = {"summary": summary, "updated": jtime(updated), "created": jtime(created),
         "status": {"name": status}, "issuetype": {"name": kind, "hierarchyLevel": 1 if kind == "Epic" else 0},
         "project": {"key": key.split("-")[0], "name": "Made-up project"},
         "components": [{"name": c} for c in components],
         "assignee": person(*assignee) if assignee else None,
         "reporter": person(*reporter) if reporter else None}
    if epic:
        f["parent"] = {"key": epic[0], "fields": {"summary": epic[1],
                                                  "issuetype": {"name": "Epic", "hierarchyLevel": 1}}}
    return {"id": "1" + key.split("-")[1], "key": key, "fields": f}


def search_answer(*issues, last=True):
    return json.dumps({"data": {"issues": list(issues), "isLast": last, "appliedContentFormat": "markdown"}})


def history(hid, at, who, *items):
    return {"id": str(hid), "created": jtime(at), "author": {"accountId": who[0], "displayName": who[1]},
            "items": [dict(i) for i in items]}


def status_item(was, now):
    return {"field": "status", "fromString": was, "toString": now}


def assignee_item(was, now):
    return {"field": "assignee", "fromString": was, "toString": now}


def development(count, state="OPEN", at=None):
    js = {"cachedValue": {"errors": [], "summary": {"pullrequest": {"overall": {
        "count": count, "lastUpdated": jtime(at) if at else None, "stateCount": count, "state": state,
        "dataType": "pullrequest", "open": state == "OPEN"}}}}}
    return {"id": "customfield_10000",
            "value": f"{{pullrequest={{dataType=pullrequest, state={state}, stateCount={count}}}, "
                     f"json={json.dumps(js, separators=(',', ':'))}}}"}


def detail_answer(key, histories=(), description="<p>What to do.</p>", prs=0, pr_at=None):
    fields = {"description": description, "comment": {"total": 0}, "customFields": {}}
    if prs:
        fields["customFields"]["Development"] = development(prs, at=pr_at)
    return json.dumps({"data": {"id": "1", "key": key, "fields": fields,
                                "changelog": {"total": len(histories),
                                              "histories": sorted(histories, key=lambda h: h["created"],
                                                                  reverse=True)}}})


def comment(cid, at, name, body):
    return {"id": str(cid), "author": {"displayName": name}, "body": body, "created": jtime(at),
            "updated": jtime(at), "jsdPublic": "True"}


def comments_answer(*comments):
    return json.dumps({"startAt": 0, "maxResults": 20, "total": len(comments), "isLast": True,
                       "appliedContentFormat": "markdown", "comments": list(comments)})


def mention(aid, name):
    return f'<span data-type="mention" data-user-id="{aid}">@{name}</span>'


# What the run carries instead of an answer larger than Claude passes on.
TOO_LARGE = ("<persisted-output>\nOutput too large (59.1KB). Full output saved to: "
             "/made-up/.claude/projects/x/tool-results/toolu_1.json\n\nPreview (first 2KB):\n{\"data\": {")

ME_ANSWER = json.dumps({"data": {"accountId": ME}})
SITES_ANSWER = json.dumps({"data": {"resources": [
    {"cloudId": "00000000-0000-4000-8000-0000000000bb", "url": "https://wiki-only.example.com",
     "products": [{"id": "confluence"}]},
    {"cloudId": CLOUD, "url": f"https://{SITE}", "products": [{"id": "jira"}, {"id": "confluence"}]}]}})


def stream(queries, answers, tools_available=None, extra_calls=(), prefix=PREFIX, errors=()):
    """Claude's stream-json output for a run that made the planned calls."""
    if tools_available is None:
        tools_available = sorted({prefix + TOOL_FOR[q.kind] for q in queries})
    events = [{"type": "system", "subtype": "init", "tools": tools_available,
               "mcp_servers": [{"name": "claude.ai Atlassian MCP", "status": "connected"}]}]
    content = [{"type": "tool_use", "id": f"toolu_{i}", "name": prefix + TOOL_FOR[q.kind], "input": dict(q.args)}
               for i, q in enumerate(queries)]
    content += [{"type": "tool_use", "id": f"toolu_x{j}", "name": n, "input": a}
                for j, (n, a) in enumerate(extra_calls)]
    events.append({"type": "assistant", "message": {"content": content}})
    results = []
    for i, q in enumerate(queries):
        if q.label in errors:
            results.append({"type": "tool_result", "tool_use_id": f"toolu_{i}", "is_error": True,
                            "content": [{"type": "text", "text": "Error: 404"}]})
        elif q.label in answers:
            results.append({"type": "tool_result", "tool_use_id": f"toolu_{i}",
                            "content": [{"type": "text", "text": answers[q.label]}]})
    events.append({"type": "user", "message": {"content": results}})
    events.append({"type": "result", "subtype": "error_max_turns", "is_error": True, "total_cost_usd": 0.002,
                   "duration_ms": 9000, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 30000, "output_tokens": 300}})
    return [json.dumps(e) for e in events]


class FakeReader:
    """A reader for JiraSource(reader_factory=...): answers each read from
    `answer(query)` (answer text, or None for none), keeps every batch in
    `batches`, and goes through the real read_stream."""

    def __init__(self, answer, prefix=PREFIX):
        self.answer = answer
        self.prefix = prefix
        self.batches = []
        self.usage = {}
        self.runs = 0

    def read(self, queries) -> ReadResult:
        self.batches.append(list(queries))
        self.runs += 1
        answers = {}
        for q in queries:
            a = self.answer(q)
            if a is not None:
                answers[q.label] = a
        result = read_stream(stream(queries, answers, prefix=self.prefix), queries, self.prefix)
        self.usage = add_usage(self.usage, result.usage)
        return result
