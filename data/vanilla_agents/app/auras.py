"""Raid frames and buff/debuff icons (D-270). Pure: reads the snapshot (agents, tasks, leases, verify status), returns what the UI draws.
Only real data is used: a frame's bar is quests passed / quests that agent owns; an icon appears only for a state that is true right now."""
ORDER = ("running", "paused", "failed", "blocked", "approved", "auto", "verified", "lease")     # icon order left to right; wording, icons and colours live in metaphors.json auras


def _aura(i, n=1):
    return {"id": i, "count": n}


def _done(t):
    """A finished quest that counts (D-313: with plan approval required, only approved ones do; snapshots without that field fall back to the status)."""
    return t.get("counts", t["status"] == "complete")


def _auto(t):
    """D-372: approved by the program (low risk, in scope), not undone by the owner."""
    return bool(t.get("approved") and t.get("approved_by") == "auto")


def _agent_auras(a, owned, paused):
    by = lambda s: sum(1 for t in owned if t["status"] == s)
    found = {"running": 1 if a["state"] == "working" else 0, "failed": by("failed"), "blocked": by("blocked"),
             "paused": by("ready") if paused else 0,
             "approved": sum(1 for t in owned if t.get("approved")),
             "auto": sum(1 for t in owned if _auto(t)),
             "verified": len(owned) if owned and sum(map(_done, owned)) == len(owned) else 0}
    return [_aura(i, found[i]) for i in ORDER if found.get(i)]


def compute(snap, max_frames=8):
    paused, v = bool((snap.get("verify") or {}).get("paused")), snap.get("verify") or {}
    tasks, raid = snap["tasks"], []
    for a in snap["agents"][:max_frames]:
        owned = [t for t in tasks if t["agent"] == a["id"]]
        passed = sum(map(_done, owned))
        raid.append({"id": a["id"], "role": a["role"], "state": a["state"], "passed": passed, "total": len(owned),
                     "pct": round(100 * passed / len(owned)) if owned else 0, "auras": _agent_auras(a, owned, paused)})
    by = lambda s: sum(1 for t in tasks if t["status"] == s)
    found = {"running": 1 if v.get("running") else 0, "paused": 1 if paused else 0, "failed": by("failed"), "blocked": by("blocked"),
             "approved": sum(1 for t in tasks if t.get("approved")), "auto": sum(1 for t in tasks if _auto(t)), "verified": 1 if tasks and sum(map(_done, tasks)) == len(tasks) else 0, "lease": len(snap["leases"])}
    player = [_aura(i, found[i]) for i in ORDER if found[i]]
    exp = snap.get("lease_expires") or []
    for a in player:
        if a["id"] == "lease" and exp:
            a["expires_at"] = min(exp)         # D-410: soonest expiry; the page counts down to it
    return {"raid": raid, "raid_more": max(0, len(snap["agents"]) - max_frames), "player": player}
