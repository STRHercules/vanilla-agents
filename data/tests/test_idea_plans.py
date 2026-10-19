"""Idea draft from prose-style plans (D-350): roadmap tables, numbered lists, Non-goals / scope-table headings. Deterministic, temp dirs only."""
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.onboard import idea

ROADMAP = """# Big project and roadmap

## 1. Definition
1. Side-by-side screenshots match
2. Packet coverage is full

## 2. Scope
| Area | Default | Why |
|---|---|---|
| Gameplay | IN | core |
| Public hosting | OUT (copyright) | legal |
| Mobile | OUT for now | later |

## 5. Priority order
| # | Task | Size | Why |
|---|---|---|---|
| 1 | **DONE** smoke script | S | x |
| 2 | **PARTIAL** melee attack | M | y |
| 3 | Build the **HUD** | L | z |

## 6. Decisions I need
1. Do you want the Lua UI?
"""
PLAIN = """# Notes
## Out of scope
- Mobile apps
- Do not edit `vendor/`
## Next
- Ship the lobby
- Fix login
"""


def repo(files):
    d = tempfile.TemporaryDirectory()
    for n, t in files.items():
        p = Path(d.name) / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
    return d


class PlanTables(unittest.TestCase):
    def test_table_rows_become_quests_done_rows_and_other_lists_do_not(self):
        with repo({"docs/PROJECT_ROADMAP.md": ROADMAP}) as r:
            self.assertEqual([t for t, _ in idea.quests(r)], ["melee attack", "Build the HUD"])

    def test_scope_table_out_rows_are_non_goals(self):
        with repo({"docs/PROJECT_ROADMAP.md": ROADMAP}) as r:
            self.assertEqual(idea.draft(r)["non_goals"], ["Public hosting", "Mobile"])

    def test_non_goal_headings_and_bullets_under_next(self):
        with repo({"NEXT_STEPS.md": PLAIN}) as r:
            d = idea.draft(r)
            self.assertEqual(d["non_goals"][0], "Mobile apps")
            self.assertEqual(d["out_of_scope"], ["vendor/"])
            self.assertEqual([t for t, _ in idea.quests(r)], ["Ship the lobby", "Fix login"])

    def test_plan_files_come_first_and_no_duplicates(self):
        with repo({"README.md": "# R\n## Next\n- Ship the lobby\n", "ROADMAP.md": "# Roadmap\n1. Ship the lobby\n2. Add chat\n"}) as r:
            q = idea.quests(r)
            self.assertEqual([t for t, _ in q], ["Ship the lobby", "Add chat"])
            self.assertEqual(q[0][1], "ROADMAP.md")


if __name__ == "__main__":
    unittest.main()
