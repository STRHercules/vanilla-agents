"""Claude Code hook receiver. Turns one hook JSON (stdin) into feed events. Must never block or fail Claude: every error is swallowed,
the exit code is always 0, and a watchdog ends the process after WATCHDOG_SECONDS even if the feed lock is held.
D-386: at SessionStart and UserPromptSubmit it also prints the 'Explain it more' / 'Change it' requests nobody has answered yet (Claude Code adds that text to the
conversation), so a session actually sees what the owner asked for. Answer with scripts/answer_explain.py."""
import json
import os
import sys
import threading
from pathlib import Path

from .feed import Feed
from .redact_util import clean
from .worktree import locate

WATCHDOG_SECONDS = 3
MAX_STDIN = 1_000_000


def default_feed_path():
    return os.environ.get("VANILLA_FEED") or str(Path.home() / ".vanilla-agents" / "feed.jsonl")


def _agent(h):
    return h.get("agent_type") or h.get("agent_id") or ("claude-" + str(h.get("session_id", "?"))[:8])


_SID_OK = set("0123456789abcdefABCDEF-")


def place(hook, cwd=None):
    """V-038/V-039: agent_place data: the full session id (only for the main session, so Resurrect resumes the right one), the folder,
    and its git checkout (root, linked worktree name, branch) read from files. Empty values are left out."""
    sid = str(hook.get("session_id") or "")
    d = {}
    if sid and not (hook.get("agent_type") or hook.get("agent_id")) and 8 <= len(sid) <= 64 and set(sid) <= _SID_OK:
        d["session"] = sid
    cwd = str(cwd or hook.get("cwd") or "")
    if cwd:
        d["cwd"] = cwd[:500]
        w = locate(cwd)
        for k in ("root", "worktree", "branch"):
            if w.get(k):
                d[k] = str(w[k])[:500]
    return d


def _tool_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for k in ("stdout", "output", "content", "text", "result"):
            if isinstance(resp.get(k), str):
                return resp[k]
        return json.dumps(resp)[:600]
    return "" if resp is None else str(resp)


def to_events(hook):
    """hook dict -> list of (type, agent, data); [] for hooks we do not map."""
    name, agent = hook.get("hook_event_name"), _agent(hook)
    inp = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
    if name == "PreToolUse":
        return [("agent_working", agent, {"activity": "using " + str(hook.get("tool_name", "a tool"))})]
    if name == "PostToolUse":
        d = {"tool": str(hook.get("tool_name", "tool"))}
        f = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
        if f:
            d["file"] = str(f)
        if inp.get("command"):
            d["command"] = str(inp["command"])[:300]
        out = _tool_text(hook.get("tool_response"))
        if out:
            d["output_excerpt"] = out[:500]
        return [("tool_use", agent, d)]
    if name in ("Stop", "SubagentStop"):
        return [("agent_idle", agent, {})]
    if name == "Notification":
        nt, msg = str(hook.get("notification_type") or ""), str(hook.get("message", ""))[:300]
        low = msg.lower()
        if nt in WAITING or (not nt and "permission" in low):
            return [("agent_waiting", agent, {"reason": WAITING.get(nt, "permission"), "note": msg})]
        if nt == "idle_prompt" or (not nt and "waiting for your input" in low):
            return [("agent_waiting", agent, {"reason": "input", "note": msg})]
        if nt and nt != "agent_completed":
            return []                     # sign-in and quota notices are not about the work
        return [("agent_idle", agent, {"note": msg})]
    if name == "PermissionRequest":
        return [("agent_waiting", agent, {"reason": "permission", "note": "wants to use " + str(hook.get("tool_name", "a tool"))})]
    if name == "StopFailure":
        why = next((str(hook[k]) for k in ("error_type", "reason", "error", "matcher", "stop_reason") if isinstance(hook.get(k), str) and hook.get(k)), "unknown")
        return [("agent_stunned", agent, {"reason": why[:60]})]
    if name == "SessionEnd":
        return [("agent_left", agent, {"reason": str(hook.get("reason", ""))[:60]})]
    if name == "PostToolUseFailure":
        err = hook.get("error") if isinstance(hook.get("error"), str) else _tool_text(hook.get("tool_response"))
        return [("tool_failed", agent, {"tool": str(hook.get("tool_name", "tool")), **({"error": err[:200]} if err else {})})]
    if name == "SubagentStart":
        kind = next((str(hook[k]) for k in ("agent_type", "subagent_type", "agent_name") if isinstance(hook.get(k), str) and hook.get(k)), "")
        return [("agent_working", agent, {"activity": "calling in a helper" + (" (" + kind[:40] + ")" if kind else "")})]
    if name in ("PreCompact", "PostCompact"):
        return [("agent_rested", agent, {"phase": "start" if name == "PreCompact" else "done"})]
    if name in ("TaskCreated", "TaskCompleted"):
        t = next((hook[k] for k in ("task", "todo") if isinstance(hook.get(k), dict)), hook)
        text = next((str(t[k]) for k in ("subject", "content", "title", "description", "task_subject") if isinstance(t.get(k), str) and t.get(k)), "")
        return [("todo_update", agent, {"text": text[:160], **({"done": "yes"} if name == "TaskCompleted" else {})})] if text else []
    if name == "CwdChanged":
        cwd = str(hook.get("new_cwd") or hook.get("cwd") or "")
        return [("agent_working", agent, {"activity": "moved to " + (Path(cwd).name or cwd)[:60]}), ("agent_place", agent, place(hook, cwd))] if cwd else []
    if name == "UserPromptSubmit":
        return [("agent_working", agent, {"activity": "reading your message"}), ("agent_place", agent, place(hook))]
    if name == "SessionStart":
        return [("agent_started", agent, {}), ("agent_place", agent, place(hook))]
    return []


# Notification types that mean "Claude is waiting on you", and the word the window uses for each
WAITING = {"permission_prompt": "permission", "agent_needs_input": "question", "elicitation_dialog": "question", "elicitation_url_dialog": "question"}


ANSWER_HOOKS = ("SessionStart", "UserPromptSubmit")
MAX_PENDING = 5


def pending_full(feed):
    """Like pending() but each row also carries the owner's note for a change request: [(kind, quest id, title, note)]."""
    open_ = {}
    for ev in feed.read_since(0, 100000):
        kind = ev.type.split("_")[0]
        if ev.type in ("explain_requested", "change_requested"):
            open_[(kind, ev.task)] = (str(ev.data.get("title", "")), str(ev.data.get("note", "")))
        elif ev.type in ("explain_answered", "change_answered"):
            open_.pop((kind, ev.task), None)
    return [(k, t, v[0], v[1]) for (k, t), v in open_.items()]


def pending(feed):
    """[(kind, quest id, title)] of explain/change requests still open: a request is closed by a later explain_answered / change_answered for the same quest."""
    open_ = {}
    for ev in feed.read_since(0, 100000):
        kind = ev.type.split("_")[0]
        if ev.type in ("explain_requested", "change_requested"):
            open_[(kind, ev.task)] = str(ev.data.get("title", ""))
        elif ev.type in ("explain_answered", "change_answered"):
            open_.pop((kind, ev.task), None)
    return [(k, t, title) for (k, t), title in open_.items()]


def pending_text(feed, agent=None):
    """What a Claude session is told at start / on each prompt. '' when nothing is open.
    Whispers addressed to this session's helper (or to everyone) come first."""
    from ..app.social import pending_messages
    rows = pending_full(feed)[:MAX_PENDING]
    msgs = pending_messages(feed, agent) if agent else []
    if not rows and not msgs:
        return ""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "answer_explain.py").as_posix()
    reply = (Path(__file__).resolve().parents[2] / "scripts" / "reply.py").as_posix()
    out = ["vanilla-agents: the owner asked you for something in the program window. Do it, then record it:"]
    for _eid, to, text in msgs:
        out.append('- MESSAGE from the owner%s: %r. Answer briefly in plain words, then run: python %s --agent %s "<your answer>"' % (" to everyone" if to == "everyone" else "", text, reply, agent))
    for kind, task, title, note in rows:
        if kind == "explain":
            out.append('- EXPLAIN quest %s (%s) ELI5 for a non-coder, max 4 short sentences, no jargon or file names: (1) what it is, with an everyday comparison; (2) why it matters to the owner; (3) what he gets or has to decide. Then run: python %s %s "<your answer>"' % (task, title, script, task))
        else:
            out.append('- CHANGE quest %s (%s). The owner wants: %s. Make that change (ask him only if it is still unclear), then run: python %s %s --changed "<what you changed>"' % (task, title, repr(note) if note else '(he did not say; ask him)', script, task))
    return os.linesep.join(out)


def handle(raw, feed):
    """Parse raw hook JSON and append events. Returns the stored events; raises on bad input (main() swallows)."""
    hook = json.loads(raw)
    if not isinstance(hook, dict):
        return []
    task = clean(os.environ.get("VANILLA_TASK")) or None
    return [feed.append(t, task=task, agent=a, data=d) for t, a, d in to_events(hook)]


def main(stdin=None, feed_path=None):
    """Entry point for scripts/claude_hook.py. Always returns 0. Prints only the open requests at SessionStart / UserPromptSubmit (D-386)."""
    timer = threading.Timer(WATCHDOG_SECONDS, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    try:
        raw = (stdin or sys.stdin.buffer).read(MAX_STDIN)
        feed = Feed(feed_path or default_feed_path())
        handle(raw, feed)
        hook = json.loads(raw)
        if hook.get("hook_event_name") in ANSWER_HOOKS:
            text = pending_text(feed, _agent(hook))
            if text:
                print(text)
        elif hook.get("hook_event_name") == "Stop":
            text = stop_block(feed, hook, os.path.join(os.path.dirname(os.path.abspath(str(feed.path))), "whispers-shown.json"))
            if text:
                print(text)
    except BaseException:
        pass
    timer.cancel()
    return 0


# ---- whispers reach a busy helper when its turn ends (Stop hook): each message once, never in a loop ----------------------------------

def stop_block(feed, hook, seen_path):
    """JSON text for Claude Code's Stop hook ("decision": "block") carrying whispers to this helper it has not been shown yet, or ''."""
    if hook.get("stop_hook_active"):            # Claude is already continuing because of a Stop hook: never block twice in a row
        return ""
    from ..app.social import pending_messages
    agent = _agent(hook)
    msgs = pending_messages(feed, agent) if agent else []
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = set(json.load(f))
    except (OSError, ValueError):
        seen = set()
    new = [(eid, to, text) for eid, to, text in msgs if str(eid) not in seen]
    if not new:
        return ""
    seen.update(str(e) for e, _t, _x in new)
    try:
        os.makedirs(os.path.dirname(seen_path) or ".", exist_ok=True)
        with open(seen_path, "w", encoding="utf-8") as f:
            json.dump(sorted(seen)[-500:], f)
    except OSError:
        pass
    reply = (Path(__file__).resolve().parents[2] / "scripts" / "reply.py").as_posix()
    lines = ["The owner sent a message in the Vanilla Agents window while you were working:"]
    lines += ["- %r%s" % (text, " (to everyone)" if to == "everyone" else "") for _e, to, text in new]
    lines.append('Answer briefly with: python %s --agent %s "<answer>" and act on it if it asks for something.' % (reply, agent))
    return json.dumps({"decision": "block", "reason": "\n".join(lines)})
