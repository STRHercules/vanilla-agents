"""D-418: notice a quest finished by a normal Claude Code session, without a click.
Rule (strict on purpose, a wrong 'done' would hide real work): a commit whose message contains the quest's exact id, AND a passing Verify run on that commit or a later one.
Pure apart from one git call; the caller records the result with AppState.mark_done_elsewhere."""
import subprocess

SEP = "\x1f"


def git_log(repo, n=300, timeout=30):
    """[(sha, message)] newest first, or [] if git cannot tell."""
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), "log", "-n", str(n), "--format=%H" + SEP + "%B" + "\x1e"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode:
        return []
    out = []
    for rec in r.stdout.decode("utf-8", "replace").split("\x1e"):
        sha, _, msg = rec.strip().partition(SEP)
        if sha:
            out.append((sha, msg))
    return out


def finished(quest_ids, log, runs):
    """{quest id: note} for open quests named in a commit that a passing Verify run covers. `runs` = runs.read() records (commit, verdict)."""
    passed = {r["commit"] for r in runs if r.get("verdict") == "VERIFIED" and r.get("commit")}
    done = {}
    for qid in quest_ids:
        for i, (sha, msg) in enumerate(log):
            if qid in msg:
                if any(s in passed for s, _ in log[:i + 1]):      # that commit or a newer one passed Verify
                    done[qid] = "commit %s names it and Verify passed afterwards" % sha[:8]
                break
    return done
