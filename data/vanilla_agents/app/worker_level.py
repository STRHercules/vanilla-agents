"""How hard a worker's job is, in plain rules (no model): the 3D workshop picks a worker of the matching level band (easy = low level, hard = full epics).
Score = quest size (small 0, medium 1, big 2) + risk (low 0, medium 1, high 2); an unknown size or risk counts as medium."""

_SIZE = {"small": 0, "medium": 1, "big": 2}
_RISK = {"low": 0, "medium": 1, "high": 2}


def difficulty(task):
    """-> {'band': easy|medium|hard, 'legendary': bool} for a quest dict (None = the agent has no quest yet = medium)."""
    t = task or {}
    score = _SIZE.get(t.get("size"), 1) + _RISK.get(t.get("risk"), 1)
    return {"band": "easy" if score == 0 else "medium" if score <= 2 else "hard", "legendary": score == 4}


def annotate_agents(snap):
    """Add `difficulty` to every agent of a snapshot, from the quest it is working on (copies)."""
    by_id = {t["id"]: t for t in snap["tasks"]}
    return {**snap, "agents": [{**a, "difficulty": difficulty(by_id.get(a.get("task")))} for a in snap["agents"]]}
