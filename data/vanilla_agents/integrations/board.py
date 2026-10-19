"""The imported bounties of one project: proposals only. Kept in the owner's work folder (never the repository), one JSON file per
project. An item is identified by its link (dedupe by URL); items without a link (a CSV row) by '<source>:<key>'. Each refresh of a
source REPLACES that source's set: what is no longer open upstream disappears (a quest made from it stays). 'Make it a quest' marks the
item as made (it leaves the list; the quest is in the quest log); 'Hide' hides it for good."""
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

SOURCES = ("github", "jira", "linear")
KINDS = ("issue", "pr")
EXCERPT = 240
MAX_ITEMS = 300
MAX_MADE = 2000


def excerpt(text, n=EXCERPT):
    t = re.sub(r"<!--.*?-->", " ", str(text or ""), flags=re.S)     # GitHub issue templates hide their help text in comments
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 3].rstrip() + "..."


def safe_link(url):
    u = str(url or "").strip()
    return u if u.startswith("https://") and len(u) <= 500 and not any(c in u for c in " \r\n\t\"'<>") else ""


def make_item(source, key, title, body="", url="", kind="issue", labels=(), n=EXCERPT):
    """One bounty proposal. Raises ValueError for an item without a title."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:200]
    if not title or source not in SOURCES:
        raise ValueError("an imported item needs a title")
    link = safe_link(url)
    key = re.sub(r"\s+", "", str(key or ""))[:40]
    return {"id": link or "%s:%s" % (source, key or hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]), "source": source,
            "kind": kind if kind in KINDS else "issue", "key": key, "title": title, "excerpt": excerpt(body, n), "url": link,
            "labels": [str(x)[:40] for x in list(labels)[:6]]}


def label_of(it):
    """'GitHub #123', 'GitHub PR #45', 'Jira ABC-1', 'Linear ENG-2' (the start of the quest's why)."""
    if it["source"] == "github":
        return "GitHub %s%s" % ("PR " if it["kind"] == "pr" else "", it["key"])
    return ("Jira " if it["source"] == "jira" else "Linear ") + (it["key"] or "item")


def board_path(work_dir, project):
    h = hashlib.sha1(os.path.normcase(os.path.abspath(str(project))).encode("utf-8")).hexdigest()[:16] if project else "no-project"
    return Path(work_dir) / "integrations" / ("%s.json" % h)


class Board:
    def __init__(self, path, clock=time.time):
        self.path, self.clock, self.lock = Path(path), clock, threading.Lock()
        self.items, self.made, self.hidden = {}, {}, set()
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = {k: v for k, v in (d.get("items") or {}).items() if isinstance(v, dict) and v.get("source") in SOURCES}
            self.made = {k: v for k, v in (d.get("made") or {}).items() if isinstance(v, str)}
            self.hidden = set(x for x in d.get("hidden") or [] if isinstance(x, str))
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        made = dict(list(self.made.items())[-MAX_MADE:])
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".board-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"items": self.items, "made": made, "hidden": sorted(self.hidden)[-MAX_MADE:]}, f, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def replace(self, source, origin, new):
        """`new` is the whole open set of `source` as one origin ('gh', 'rest', 'api', 'file') sees it now. Returns True if anything changed."""
        with self.lock:
            before = json.dumps(self.items, sort_keys=True)
            ids = set()
            for pos, it in enumerate(list(new)[:MAX_ITEMS]):
                if it["id"] in ids:
                    continue
                ids.add(it["id"])
                old = self.items.get(it["id"], {})
                self.items[it["id"]] = {**it, "origins": sorted(set(old.get("origins", [])) | {origin}), "seen": old.get("seen") or int(self.clock()), "pos": pos}
            for k, it in list(self.items.items()):
                if it["source"] == source and origin in it.get("origins", []) and k not in ids:
                    rest = [o for o in it["origins"] if o != origin]
                    if rest:
                        it["origins"] = rest
                    else:
                        del self.items[k]          # closed (or gone) upstream: the bounty disappears; a quest made from it stays
            changed = json.dumps(self.items, sort_keys=True) != before
            if changed:
                self._save()
            return changed

    def get(self, item_id):
        return self.items.get(item_id)

    def mark_made(self, item_id, quest_id):
        with self.lock:
            self.made[item_id] = quest_id
            self._save()

    def hide(self, item_id):
        with self.lock:
            if item_id not in self.items:
                raise ValueError("that bounty is not on the board any more")
            self.hidden.add(item_id)
            self._save()

    def visible(self, tasks=(), limit=MAX_ITEMS):
        """Items still to decide on: not hidden, not made into a quest (also when a quest's why names the link, e.g. made in another window)."""
        named = {w.rstrip(".,;:)") for t in tasks for w in str(t.get("why") or "").split()}
        out = [dict(it) for k, it in self.items.items() if k not in self.made and k not in self.hidden and not (it.get("url") and it["url"] in named)]
        out.sort(key=lambda it: (SOURCES.index(it["source"]), it["kind"] == "pr", -it.get("seen", 0), it.get("pos", 0)))       # newest import first, then the source's own order
        return out[:limit]

    def counts(self):
        c = {s: 0 for s in SOURCES}
        for k, it in self.items.items():
            if k not in self.made and k not in self.hidden:
                c[it["source"]] += 1
        return c
