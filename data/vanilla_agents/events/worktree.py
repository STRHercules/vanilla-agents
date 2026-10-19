"""V-038: which git checkout a folder belongs to, from plain file reads (never runs git, never raises).
Walk up from the folder to the first `.git`:
- a `.git` FOLDER: the repository's main checkout; the branch is in `.git/HEAD`;
- a `.git` FILE with `gitdir: <repo>/.git/worktrees/<name>`: a linked worktree called <name>; its branch is in that folder's HEAD;
- any other `gitdir:` (a submodule): its own checkout, no worktree name.
Two Claude sessions in different worktrees of one repository are separate checkouts: the same file name there is not the same file."""
import os

MAX_UP = 40
MAX_READ = 4096


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(MAX_READ).strip()
    except OSError:
        return ""


def branch_of(head_text):
    """'ref: refs/heads/feat-x' -> 'feat-x'; a detached HEAD (a commit id) -> 'detached at <7 chars>'; unreadable -> ''."""
    t = (head_text or "").strip()
    if t.startswith("ref:"):
        ref = t[4:].strip()
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    if len(t) >= 7 and all(c in "0123456789abcdef" for c in t[:40].lower()):
        return "detached at " + t[:7]
    return ""


def locate(cwd):
    """{root, worktree, branch, repo} for the checkout holding `cwd`, or {} when it is not in a git checkout (or unreadable).
    root = the checkout's top folder; worktree = the linked worktree's name ('' for the main checkout); repo = the main checkout's folder."""
    try:
        p = os.path.abspath(str(cwd or ""))
    except (TypeError, ValueError):
        return {}
    if not cwd or not os.path.isdir(p):
        return {}
    for _ in range(MAX_UP):
        g = os.path.join(p, ".git")
        try:
            if os.path.isdir(g):
                return {"root": p, "worktree": "", "branch": branch_of(_read(os.path.join(g, "HEAD"))), "repo": p}
            if os.path.isfile(g):
                line = _read(g)
                if not line.startswith("gitdir:"):
                    return {}
                gd = line[len("gitdir:"):].strip()
                gd = os.path.normpath(gd if os.path.isabs(gd) else os.path.join(p, gd))
                parent = os.path.dirname(gd)
                linked = os.path.basename(parent) == "worktrees"
                repo = os.path.dirname(os.path.dirname(parent)) if linked else p
                return {"root": p, "worktree": os.path.basename(gd) if linked else "", "branch": branch_of(_read(os.path.join(gd, "HEAD"))), "repo": repo}
        except OSError:
            return {}
        up = os.path.dirname(p)
        if up == p:
            return {}
        p = up
    return {}
