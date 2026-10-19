"""Plain-language sentences from fixed templates (never an AI call, D-120). Templates live in app/config/strings/eli5.json."""
import json
import re
from pathlib import Path

from .errors import TemplateError
from .schema import SPEC, placeholders

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "app" / "config" / "strings" / "eli5.json"
_PH = re.compile(r"\{([^{}]*)\}")
_FALLBACK = {"agent": "someone", "task": "a task"}


def load_templates(path=DEFAULT_PATH):
    try:
        t = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TemplateError("cannot read templates: %s" % e, code="eli5_unreadable")
    check_templates(t)
    return t


def check_templates(t):
    """Every event type has a string template and uses only placeholders that exist for that type; no unknown event types."""
    if not isinstance(t, dict):
        raise TemplateError("templates must be an object", code="eli5_unreadable")
    for et in SPEC:
        if not isinstance(t.get(et), str):
            raise TemplateError("no template for event type %s" % et, code="eli5_missing")
    for et, s in t.items():
        if et not in SPEC:
            raise TemplateError("template for unknown event type %s" % et, code="eli5_unknown_type")
        bad = set(_PH.findall(s)) - placeholders(et)
        if bad or re.sub(_PH, "", s).count("{") or re.sub(_PH, "", s).count("}"):
            raise TemplateError("template %s uses unknown placeholder(s) %s" % (et, sorted(bad) or "(stray brace)"), code="eli5_unknown_placeholder")


def render(event, templates):
    """Sentence for an Event. Optional fields that are absent render as empty text."""
    s = templates.get(event.type)
    if s is None:
        raise TemplateError("no template for event type %s" % event.type, code="eli5_missing")
    allowed = placeholders(event.type)
    ctx = {"agent": event.agent or _FALLBACK["agent"], "task": event.task or _FALLBACK["task"]}
    ctx.update({k: "" for k in allowed - set(ctx)})
    ctx.update({k: (("%g" % v) if isinstance(v, (int, float)) else v) for k, v in event.data.items()})

    def sub(m):
        if m.group(1) not in allowed:
            raise TemplateError("unknown placeholder {%s} in template %s" % (m.group(1), event.type), code="eli5_unknown_placeholder")
        return ctx[m.group(1)]
    return re.sub(r"\s+", " ", _PH.sub(sub, s)).strip()
