"""The one versioned event schema: the only contract between the engine and the UI (DESIGN section 7.5). Pure: no I/O."""
from dataclasses import dataclass, field

from .errors import EventSchemaError

SCHEMA_VERSION = 1
MAX_STR = 2000
NUMERIC = {"used_pct", "ctx_pct", "five_hour_pct", "seven_day_pct"}   # data keys that are numbers (0..100); every other data value is a string
AMOUNTS = {"tokens_out", "cost_usd", "lines_added", "lines_removed", "duration_ms", "five_hour_resets", "seven_day_resets"}   # data keys that are numbers >= 0 (dollars, counts, ms, epoch seconds)

# event type -> (required data keys, optional data keys)
SPEC = {
    "task_created": ({"title"}, {"details", "why", "size", "risk", "needs_plain"}),     # D-360: plain-language quest fields
    "task_ready": ({"title"}, set()),
    "agent_started": (set(), {"role"}),
    "agent_working": (set(), {"activity"}),
    "agent_idle": (set(), {"note"}),
    "agent_waiting": ({"reason"}, {"note"}),     # V-026: Claude is waiting on the owner (permission prompt, a question, or idle waiting for input)
    "agent_stunned": ({"reason"}, set()),           # V-026: the turn failed on the API side (rate limit, billing, overloaded, ...)
    "agent_left": (set(), {"reason"}),           # V-026: the Claude session ended
    "tool_failed": ({"tool"}, {"error"}),         # V-028: a tool step failed (Claude usually tries again)
    "agent_rested": (set(), {"phase"}),           # V-028: Claude tidied its memory (compaction): phase start / done
    "todo_update": ({"text"}, {"done"}),          # V-028: Claude's own task list changed (a step added or finished)
    "tool_use": ({"tool"}, {"file", "command", "output_excerpt"}),
    "sent_back": ({"check", "evidence"}, set()),
    "plan_approved": (set(), {"title", "who"}),
    "done_elsewhere": (set(), {"title", "who", "note"}),
    "quest_auto_approved": ({"title"}, {"why"}),      # D-371: a low-risk, in-scope quest went ahead without a click
    "quest_vetoed": ({"title"}, {"who"}),               # D-376: You paused/undid an auto-approved quest
    "explain_requested": ({"title"}, set()),          # D-374: 'Explain it more'; an interactive Claude session answers
    "change_requested": ({"title"}, {"note"}),           # D-374: 'Change it'
    "explain_answered": ({"title", "text"}, set()),   # D-386: a Claude session answered 'Explain it more' (scripts/answer_explain.py)
    "change_answered": ({"title"}, {"text"}),         # D-386: the requested change was made
    "route_chosen": ({"route", "why"}, set()),
    "check_started": ({"check"}, set()),
    "check_finished": ({"check", "verdict"}, set()),
    "task_verified": (set(), {"title"}),
    "task_failed": (set(), {"title", "reason"}),
    "task_blocked": (set(), {"title", "reason"}),
    "lease_acquired": ({"resource"}, {"expires_at"}),
    "lease_expired": ({"resource"}, set()),
    "usage_update": ({"used_pct"}, {"resets_at"}),
    "message_sent": ({"text", "to"}, {"who"}),        # V-013: the owner whispered to one helper (to = its id) or to everyone (to = everyone)
    "message_answered": ({"text"}, set()),            # V-013: a helper answered a whisper (MCP reply tool or scripts/reply.py)
    "quest_claimed": ({"title"}, set()),              # V-013: a helper took a quest from the board (MCP claim_quest)
    # V-030: Claude Code's own numbers for one session, from the statusline bridge (scripts/statusline.py); every field may be missing
    # V-038/V-039: where a Claude session runs: its session id (for Resurrect), folder, and git checkout (root folder, linked worktree name, branch)
    "agent_place": (set(), {"session", "cwd", "root", "worktree", "branch"}),
    # V-045: competing fixes. The owner opens / stops a competition and picks the winner (window only; contests.json is the truth, a feed line alone decides nothing);
    # helpers propose (MCP propose_fix: summary + their branch, files changed); the checker can run on one proposal (fix_checked)
    "contest_opened": ({"title", "tries"}, {"base"}),
    "fix_proposed": ({"title", "summary", "branch"}, {"worktree", "files", "lines_added", "lines_removed"}),
    "fix_checked": ({"title", "verdict"}, {"reason", "branch"}),
    "fix_picked": ({"title", "winner"}, {"base", "branch"}),
    "contest_closed": ({"title"}, set()),
    "session_stats": (set(), {"model", "ctx_pct", "cost_usd", "lines_added", "lines_removed", "duration_ms", "five_hour_pct", "five_hour_resets", "seven_day_pct", "seven_day_resets", "tokens_out"}),
}
EVENT_TYPES = tuple(SPEC)


def placeholders(event_type):
    """Names a plain-language template for this event type may use."""
    req, opt = SPEC[event_type]
    return {"agent", "task"} | req | opt


@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    type: str
    task: str = None
    agent: str = None
    data: dict = field(default_factory=dict)
    v: int = SCHEMA_VERSION

    def to_dict(self):
        return {"v": self.v, "id": self.id, "ts": self.ts, "type": self.type, "task": self.task, "agent": self.agent, "data": dict(self.data)}


def validate_parts(event_type, task, agent, data):
    """Check type, optional task/agent strings and the data keys/values; raises EventSchemaError."""
    if event_type not in SPEC:
        raise EventSchemaError("unknown event type %r" % (event_type,), code="unknown_event_type")
    for name, val in (("task", task), ("agent", agent)):
        if val is not None and (not isinstance(val, str) or len(val) > MAX_STR):
            raise EventSchemaError("%s must be a string up to %d chars" % (name, MAX_STR), code="bad_field")
    if not isinstance(data, dict):
        raise EventSchemaError("data must be an object", code="bad_field")
    req, opt = SPEC[event_type]
    missing = req - set(data)
    if missing:
        raise EventSchemaError("%s is missing %s" % (event_type, sorted(missing)), code="missing_field")
    extra = set(data) - req - opt
    if extra:
        raise EventSchemaError("%s has unknown fields %s" % (event_type, sorted(extra)), code="unknown_field")
    for k, val in data.items():
        if k in NUMERIC:
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val <= 100:
                raise EventSchemaError("%s must be a number from 0 to 100" % k, code="bad_field")
        elif k in AMOUNTS:
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val < 1e15:
                raise EventSchemaError("%s must be a number of at least 0" % k, code="bad_field")
        elif k == "expires_at":
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                raise EventSchemaError("expires_at must be a time in seconds", code="bad_field")
        elif not isinstance(val, str) or len(val) > MAX_STR:
            raise EventSchemaError("%s must be a string up to %d chars" % (k, MAX_STR), code="bad_field")


def from_dict(d):
    """Validate a decoded feed record and return an Event."""
    if not isinstance(d, dict):
        raise EventSchemaError("event must be an object", code="bad_field")
    if d.get("v") != SCHEMA_VERSION:
        raise EventSchemaError("unsupported schema version %r" % (d.get("v"),), code="bad_version")
    if isinstance(d.get("id"), bool) or not isinstance(d.get("id"), int) or d["id"] < 1:
        raise EventSchemaError("id must be a positive integer", code="bad_field")
    if isinstance(d.get("ts"), bool) or not isinstance(d.get("ts"), (int, float)):
        raise EventSchemaError("ts must be a number", code="bad_field")
    extra = set(d) - {"v", "id", "ts", "type", "task", "agent", "data"}
    if extra:
        raise EventSchemaError("unknown fields %s" % sorted(extra), code="unknown_field")
    validate_parts(d.get("type"), d.get("task"), d.get("agent"), d.get("data"))
    return Event(d["id"], d["ts"], d["type"], d.get("task"), d.get("agent"), dict(d["data"]))
