"""'Import from file': a Jira CSV export or a Linear CSV export becomes bounty proposals (no network, no keys).
Jira (Issues > Export > CSV): Summary, Issue key, Issue id, Description, Status, Status Category, Labels (repeated columns).
Linear (Settings > Import/Export > Export CSV): ID, Team, Title, Description, Status, Labels, Completed, Canceled.
A column called URL / Link / Issue URL is used as the link when present (Jira rows also get <site>/browse/<key> when the Jira site is
set in Options). Finished rows (done / closed / resolved / canceled) are skipped. Quoted line breaks, a byte order mark and
semicolon- or tab-separated files are understood; files over the size limit are refused before reading."""
import csv
import io

from .board import make_item

MAX_BYTES = 2_000_000
MAX_ROWS = 200
URL_COLS = ("url", "link", "issue url", "issue link", "permalink")
DONE_WORDS = {"done", "closed", "resolved", "canceled", "cancelled", "duplicate", "won't do", "won't fix", "completed"}


class CsvImportError(ValueError):
    pass


def detect(header):
    h = {c.strip().lower() for c in header}
    if {"summary", "issue key"} <= h:
        return "jira"
    if {"id", "title"} <= h and ({"team", "status"} & h):
        return "linear"
    return None


def _rows(text):
    head = text.split("\n", 1)[0]           # the header line decides: descriptions below may hold any character
    delim = max((",", ";", "\t"), key=head.count)
    return csv.reader(io.StringIO(text, newline=""), delimiter=delim)


def parse(text, kind="auto", max_bytes=MAX_BYTES, max_rows=MAX_ROWS, jira_site="", excerpt_chars=240):
    """(kind, items, skipped) from the file's text. kind: auto | jira | linear. Raises CsvImportError with a plain message."""
    if not isinstance(text, str):
        raise CsvImportError("the file must be text")
    if len(text.encode("utf-8")) > max_bytes:
        raise CsvImportError("that file is too big (more than %d KB); export fewer rows" % (max_bytes // 1000))
    text = text.lstrip("﻿")
    try:
        rows = _rows(text)
        header = next(rows, None)
    except csv.Error as e:
        raise CsvImportError("that file is not a readable CSV (%s)" % e) from None
    if not header:
        raise CsvImportError("that file is empty")
    header = [c.strip().lstrip("﻿") for c in header]
    found = detect(header)
    if kind == "auto":
        kind = found
    if kind not in ("jira", "linear") or found != kind:
        raise CsvImportError("this does not look like a %s CSV export (no %s columns)" % (
            {"jira": "Jira", "linear": "Linear"}.get(kind, "Jira or Linear"), {"jira": "Summary / Issue key", "linear": "ID / Title"}.get(kind, "Summary / Issue key or ID / Title")))
    low = [c.lower() for c in header]
    items, skipped, seen = [], 0, set()
    site = jira_site.rstrip("/") if jira_site.startswith("https://") else ""
    try:
        for row in rows:
            if not any(x.strip() for x in row):
                continue
            first = lambda *names: next((row[i].strip() for i, c in enumerate(low) if c in names and i < len(row) and row[i].strip()), "")
            labels = [row[i].strip() for i, c in enumerate(low) if c in ("labels", "label") and i < len(row) and row[i].strip()]
            if len(labels) == 1 and "," in labels[0]:
                labels = [x.strip() for x in labels[0].split(",") if x.strip()]
            if kind == "jira":
                key, title, status = first("issue key"), first("summary"), first("status category", "status").lower()
                url = first(*URL_COLS) or (site + "/browse/" + key if site and key else "")
                done = status in DONE_WORDS or first("resolution").lower() in ("done", "fixed", "won't do", "duplicate")
            else:
                key, title, status = first("id", "identifier"), first("title"), first("status").lower()
                url = first(*URL_COLS)
                done = status in DONE_WORDS or bool(first("completed", "canceled", "cancelled", "archived"))
            if done or not title:
                skipped += 1
                continue
            it = make_item(kind, key, title, first("description"), url, "issue", labels, excerpt_chars)
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            items.append(it)
            if len(items) >= max_rows:
                break
    except csv.Error as e:
        raise CsvImportError("that file is not a readable CSV (%s)" % e) from None
    return kind, items, skipped
