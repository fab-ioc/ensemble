"""Feedback drafts: preview once, send exactly that snapshot, never log content.

The relay receives only title/body/kind/repo. Anonymous drafts never invoke gh
to post, and diagnostics are an allowlist rather than a dump of hub state.
"""
import getpass
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_REPO = "fab-ioc/ensemble"
DEFAULT_RELAY = ""
MAX_REQUEST = 40000
MAX_BODY = 24000
MAX_RELAY_REQUEST = 32000
_drafts = {}
_lock = threading.Lock()


class FeedbackError(ValueError):
    """A validation message safe to show without echoing private input."""


def run(*args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True,
                          encoding="utf-8", timeout=20,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def output(*args):
    try:
        r = run(*args)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def valid_repo(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value))


def valid_relay(value):
    if not isinstance(value, str) or len(value) > 500:
        return False
    if not value:
        return True
    try:
        u = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname) and not (u.username or u.password or u.query or u.fragment)


def scrub(text, identities=()):
    """Conservative redaction, including encoded paths and links/tracking images.

    Unknown people in prose cannot be inferred reliably; the preview and warning
    remain essential. Known local identity values are removed case-insensitively.
    """
    text = urllib.parse.unquote(str(text))
    text = re.sub(r"(?i)[a-z]:[\\/]+users[\\/]+[^\\/\r\n]+[\\/]", "~/", text)
    text = re.sub(r"(?i)/(?:Users|home)/[^/\r\n]+/", "~/", text)
    text = re.sub(r"(?i)[a-z]:[\\/]+users[\\/]+[^\\/\s]+", "~", text)
    text = re.sub(r"(?i)/(?:Users|home)/[^/\s]+", "~", text)
    text = re.sub(r"(?i)(?:https?://|www\.)[^\s<>\])]+", "[link removed]", text)
    text = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "[email removed]", text)
    text = re.sub(r"(?i)\b(?:github_pat_[\w]+|gh[pousr]_[\w]+|sk-[\w-]+|eyJ[\w.-]+)\b", "[token removed]", text)
    secret_key = r'''["']?[\w-]*(?:token|secret|password|api[_-]?key)["']?\s*[:=]\s*'''
    secret_value = r'''(?:"(?:\\.|[^"\\])*"|'(?:''|\\.|[^'\\])*'|[^\s,;]+)'''
    text = re.sub(secret_key + secret_value, "[secret removed]", text, flags=re.I)
    text = re.sub(r"(?i)\bbearer\s+\S+", "[secret removed]", text)
    text = re.sub(r"(?i)\b(?:[\w-]+\.)+(?:ts\.net|local|internal)\b", "[host removed]", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "[address removed]", text)
    text = re.sub(r"(?i)(?<!\w)(?:[a-f0-9]{0,4}:){2,}[a-f0-9:]+(?:%[\w]+)?", "[address removed]", text)
    text = re.sub(r"\\\\[^\\\s]+", "[host removed]", text)
    text = re.sub(r"(?<!\w)@[\w-]+", "[account removed]", text)
    # Strip image markup too: a relative filename or embedded data may identify
    # someone or cause GitHub to fetch a tracking image when reading the issue.
    # Drop whole image-bearing lines, including full/collapsed/shortcut reference
    # forms and nested alt text, plus reference definitions with relative URLs.
    text = re.sub(r"(?m)^.*!\[.*$|^\s{0,3}\[[^\]\n]+\]:[^\n]*", "[image omitted]", text)
    text = re.sub(r"<[^>]+>|\[image\][^\n]*", "[image omitted]", text)
    for value in sorted(set(identities), key=len, reverse=True):
        if value:
            text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", "[identity removed]", text, flags=re.I)
    return text


def identities(settings, host, name, login):
    values = [getpass.getuser(), socket.gethostname(), socket.getfqdn(),
              str(Path.home()), settings.get("operatorNickname", ""), name, login,
              output("git", "config", "user.name"), output("git", "config", "user.email")]
    for address in host.split():
        values += [address, address.split(":")[0]]
    for key, value in os.environ.items():
        if any(x in key.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            values.append(value)
    values += [part for v in values[:9] for part in v.split() if len(part) > 1]
    return values


def relay_payload(plan):
    return json.dumps({k: plan[k] for k in ("title", "body", "kind", "repo")},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def preview(data, settings, host="", private_values=()):
    if not isinstance(data, dict):
        raise FeedbackError("Expected a feedback object.")
    title, body = data.get("title", ""), data.get("description", "")
    kind, name = data.get("kind", "bug"), data.get("name", "")
    if not all(isinstance(x, str) for x in (title, body, kind, name)):
        raise FeedbackError("Feedback fields must be text.")
    if not title.strip() or len(title) > 200 or not body.strip() or len(body.encode()) > 20000 or len(name) > 100 or kind not in ("bug", "idea"):
        raise FeedbackError("Enter a title (up to 200 characters) and description (up to 20 KB).")
    anonymous = data.get("anonymous") is True
    repo = settings.get("feedbackRepo", DEFAULT_REPO)
    relay = settings.get("feedbackRelayUrl", DEFAULT_RELAY)
    if not valid_repo(repo) or not valid_relay(relay):
        raise FeedbackError("Check the feedback repository and HTTPS relay URL in Settings.")
    login = output("gh", "api", "--hostname", "github.com", "user", "--jq", ".login")
    route = "gh" if login and not anonymous else "relay" if relay else "browser"
    if not anonymous and route == "relay" and not name.strip():
        raise FeedbackError("Enter a name for the relay, or select Send anonymously.")
    body = ("### Bug report" if kind == "bug" else "### Idea") + "\n\n" + body.strip()
    if data.get("technical", True):
        browser = data.get("browser")
        browser = browser if browser in ("Firefox", "Edge", "Chrome", "Safari") else "Other"
        page = data.get("page")
        page = page if page in ("/", "/session", "/fileview") else "Other"
        width = data.get("width")
        width = width if type(width) is int and 1 <= width <= 20000 else "unknown"
        commit = output("git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD")
        commit = commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else "unknown"
        body += f"\n\n### Technical details\nEnsemble commit: {commit}\nOS: {platform.system()}\nBrowser: {browser}\nPage: {page}\nWidth: {width}"
    if not anonymous and name.strip():
        body += "\n\nSubmitted by: " + name.strip()
    title = title.strip()
    if anonymous:
        known = identities(settings, host, name, login) + list(private_values)
        title, body = scrub(title, known), scrub(body, known)
    if len(title.encode("utf-16-le")) // 2 > 200 or len(body.encode()) > MAX_BODY:
        raise FeedbackError("The scrubbed preview is too large; shorten the title or description.")
    plan = dict(title=title, body=body, kind=kind, repo=repo, relay=relay,
                route=route, anonymous=anonymous, login=login if route == "gh" else "",
                attachments=[], created=time.monotonic(), state="ready")
    if route == "relay" and len(relay_payload(plan)) > MAX_RELAY_REQUEST:
        raise FeedbackError("The encoded feedback exceeds the relay's 32 KB limit; shorten the text.")
    with _lock:
        for key in list(_drafts):
            if time.monotonic() - _drafts[key]["created"] > 3600:
                del _drafts[key]
        if len(_drafts) >= 100:
            raise FeedbackError("Too many open feedback previews; try again later.")
        ident = secrets.token_urlsafe(24)
        _drafts[ident] = plan
    return {k: plan[k] for k in ("title", "body", "kind", "repo", "route", "anonymous", "login", "attachments")} | {"id": ident}


def fallback(plan, reason):
    query = urllib.parse.urlencode(dict(title=plan["title"], body=plan["body"], labels="feedback," + plan["kind"]))
    return dict(ok=False, fallback=True, message=reason + " GitHub requires your login and will post under your account; this is not anonymous. Review and submit there.",
                url=f'https://github.com/{plan["repo"]}/issues/new?{query}')


def issue_url(url, repo):
    return isinstance(url, str) and bool(re.fullmatch(r"https://github\.com/" + re.escape(repo) + r"/issues/\d+", url, re.I))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send(ident):
    with _lock:
        plan = _drafts.get(ident) if isinstance(ident, str) else None
        if not plan or time.monotonic() - plan["created"] > 3600:
            return dict(ok=False, message="Preview expired. Preview your saved text again.")
        if plan.get("result"):
            return plan["result"]
        if plan["state"] != "ready":
            return dict(ok=False, message="Already sending this preview. Wait for the result.")
        plan["state"] = "sending"
    result = None
    try:
        payload = dict(title=plan["title"], body=plan["body"], labels=["feedback", plan["kind"]])
        if plan["route"] == "browser":
            result = fallback(plan, "No feedback relay is configured.")
        elif plan["route"] == "gh":
            if output("gh", "api", "--hostname", "github.com", "user", "--jq", ".login") != plan["login"]:
                return dict(ok=False, message="GitHub account changed. Preview again before sending.")
            r = run("gh", "api", "--hostname", "github.com", f'repos/{plan["repo"]}/issues', "--method", "POST", "--input", "-", input=json.dumps(payload))
            if r.returncode:
                return dict(ok=False, message="GitHub refused the issue. Check gh authentication, repository access and labels; your text is kept.")
            issue = json.loads(r.stdout)
            url = issue.get("html_url")
            if not issue_url(url, plan["repo"]):
                raise ValueError("bad response")
            result = dict(ok=True, url=url)
            applied = {label.get("name", "").lower() for label in issue.get("labels", []) if isinstance(label, dict)}
            if not {"feedback", plan["kind"]}.issubset(applied):
                result["warning"] = "GitHub omitted requested labels (repository permission may be required). The Bug/Idea classification remains in the issue body."
        else:
            raw = relay_payload(plan)
            request = urllib.request.Request(plan["relay"], data=raw, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
                    url = json.loads(response.read(4096)).get("url")
                if not issue_url(url, plan["repo"]):
                    raise ValueError("bad response")
                result = dict(ok=True, url=url)
            except urllib.error.HTTPError as error:
                if error.code in (400, 413, 429):
                    return dict(ok=False, message={400: "Relay repository or request does not match its configuration.", 413: "Relay size limit exceeded; shorten the description.", 429: "Relay limit reached (3 per hour per hub IP). Try later."}[error.code])
                result = fallback(plan, "The relay failed. It may have created the issue; check the repository before retrying.")
            except (OSError, ValueError):
                result = fallback(plan, "The relay is unreachable or returned an invalid reply. Check the repository before retrying to avoid duplicates.")
        return result
    except (OSError, subprocess.SubprocessError, ValueError):
        return dict(ok=False, message="Could not confirm delivery. Check the repository before retrying; your text is kept.")
    finally:
        with _lock:
            plan["state"] = "ready"
            if result and result.get("ok"):
                plan["result"] = result
