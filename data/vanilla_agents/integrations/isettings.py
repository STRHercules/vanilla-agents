"""The owner's integration settings (Options > Integrations), kept in YOUR settings file (settings.py EXTRA "integrations"), never
in the repository. Secrets (GitHub token, Linear API key, Jira API token) are stored there and nowhere else: view() never returns
them (only whether one is saved and its last 4 characters), an empty secret in a save keeps the saved one, clear_<name> removes it."""
import re

from ..app import settings as settings_mod
from .github import PR_MODES

SECRETS = {"github": ("token",), "linear": ("api_key",), "jira": ("token",)}
DEFAULTS = {"github": {"enabled": False, "mode": "gh", "token": "", "host": "", "issues": True, "labels": [], "prs": "review_requested"},
            "linear": {"enabled": False, "api_key": "", "team": "", "mine": True},
            "jira": {"enabled": False, "site": "", "email": "", "token": "", "jql": ""}}
_LABEL = re.compile(r"^[^\x00-\x1f,]{1,50}$")
_TEAM = re.compile(r"^[A-Za-z0-9_-]{0,20}$")
_HOST = re.compile(r"^([a-z0-9-]+\.)+[a-z]{2,}$")
_TOKEN = re.compile(r"^[\x21-\x7e]{8,400}$")


def _secret(name, v):
    v = str(v or "").strip()
    if v and not _TOKEN.match(v):
        raise ValueError("the %s does not look right (no spaces; paste it again)" % name.replace("_", " "))
    return v


def validate(body, old=None, default_minutes=10):
    """Clean settings or ValueError. `old` = the saved settings (secrets kept when the new value is empty)."""
    old = old or {}
    if not isinstance(body, dict) or set(body) - {"github", "linear", "jira", "refresh_minutes"}:
        raise ValueError("integrations may only contain github, linear, jira and refresh_minutes")
    out = {}
    mins = body.get("refresh_minutes", old.get("refresh_minutes", default_minutes))
    if not isinstance(mins, int) or isinstance(mins, bool) or not 1 <= mins <= 1440:
        raise ValueError("refresh minutes must be a whole number from 1 to 1440")
    out["refresh_minutes"] = mins
    for svc, dflt in DEFAULTS.items():
        b, o = body.get(svc, {}), old.get(svc, {})
        allowed = set(dflt) | {"clear_" + s for s in SECRETS[svc]}
        if not isinstance(b, dict) or set(b) - allowed:
            raise ValueError("bad settings for %s" % svc)
        row = {**dflt, **{k: v for k, v in o.items() if k in dflt}}
        for k, v in b.items():
            if k.startswith("clear_"):
                continue
            if k in SECRETS[svc]:
                v = _secret(k, v)
                if v:
                    row[k] = v
                continue
            if isinstance(dflt[k], bool):
                if not isinstance(v, bool):
                    raise ValueError("%s %s must be true or false" % (svc, k))
                row[k] = v
            elif k == "labels":
                if isinstance(v, str):
                    v = [x.strip() for x in v.split(",") if x.strip()]
                if not isinstance(v, list) or len(v) > 5 or not all(isinstance(x, str) and _LABEL.match(x.strip()) for x in v):
                    raise ValueError("labels: up to 5 names, separated by commas")
                row[k] = [x.strip() for x in v]
            else:
                row[k] = str(v or "").strip()
        for s in SECRETS[svc]:
            if b.get("clear_" + s) is True:
                row[s] = ""
        out[svc] = row
    g, ln, j = out["github"], out["linear"], out["jira"]
    if g["mode"] not in ("gh", "token"):
        raise ValueError("GitHub mode must be gh or token")
    if g["prs"] not in PR_MODES:
        raise ValueError("pull requests must be one of " + ", ".join(PR_MODES))
    g["host"] = g["host"].lower()
    if g["host"] and (not _HOST.match(g["host"]) or len(g["host"]) > 100):
        raise ValueError("the GitHub Enterprise host must be a name like github.mycompany.com")
    if g["enabled"] and g["mode"] == "token" and not g["token"]:
        raise ValueError("GitHub import is on with a token, but no token is saved")
    if not _TEAM.match(ln["team"]):
        raise ValueError("the Linear team is its short key, like ENG")
    if ln["enabled"] and not ln["api_key"]:
        raise ValueError("Linear import is on, but no API key is saved")
    if j["site"]:
        from .jira import JiraError, check_site
        try:
            j["site"] = check_site(j["site"])
        except JiraError as e:
            raise ValueError(str(e)) from None
    if len(j["jql"]) > 500 or len(j["email"]) > 200 or (j["email"] and "@" not in j["email"]):
        raise ValueError("check the Jira e-mail and search (JQL)")
    if j["enabled"] and not (j["site"] and j["email"] and j["token"]):
        raise ValueError("Jira import is on, but the site, e-mail or API token is missing")
    return out


def load(settings_file, default_minutes=10):
    try:
        return validate(settings_mod.load_extra(settings_file, "integrations"), None, default_minutes)
    except ValueError:
        return validate({}, None, default_minutes)


def save(settings_file, body, default_minutes=10):
    clean = validate(body, settings_mod.load_extra(settings_file, "integrations"), default_minutes)
    settings_mod.save_extra(settings_file, "integrations", clean)
    return clean


def merged(saved, body, default_minutes=10):
    """Saved settings with what is typed in the window now on top (for Test before Save); secrets fall back to the saved ones."""
    b = {k: v for k, v in (body or {}).items() if k in DEFAULTS}
    for svc in b:
        if isinstance(b[svc], dict):
            b[svc] = {**b[svc], "enabled": False}        # a test never fails just because a switch is on with something missing
    return validate(b, saved, default_minutes)


def view(cfg):
    """What the window may see: everything but the secrets (only whether one is saved and its last 4 characters)."""
    out = {"refresh_minutes": cfg.get("refresh_minutes", 10)}
    for svc in DEFAULTS:
        row = {k: v for k, v in cfg.get(svc, DEFAULTS[svc]).items() if k not in SECRETS[svc]}
        for s in SECRETS[svc]:
            v = cfg.get(svc, {}).get(s, "")
            row[s + "_set"] = bool(v)
            row[s + "_end"] = v[-4:] if len(v) >= 16 else ""
        out[svc] = row
    out["pr_modes"] = list(PR_MODES)
    return out
