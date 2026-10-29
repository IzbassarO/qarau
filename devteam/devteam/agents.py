"""Agent implementations. FakeAgents costs nothing and exercises the whole graph."""

import time
from pathlib import Path

from devteam import kimi


class FakeAgents:
    """Brain plans by area, devs write a stub file, tester fails round 1, passes round 2."""

    delay = 1.0

    def plan(self, team, todo: list[dict], feedback=None, devs=None) -> list[dict]:
        """First open task per developer, matched by area."""
        time.sleep(self.delay)
        out, busy = [], set()
        allowed = team.cfg["developers"] if devs is None else devs
        for t in todo:
            devs_ = [
                d
                for d, c in team.cfg["developers"].items()
                if t.get("area") in c["areas"] and d in allowed
            ]
            if not devs_ or devs_[0] in busy:
                continue
            dev = devs_[0]
            busy.add(dev)
            out.append(
                {
                    "task_id": t["id"],
                    "assignee": dev,
                    "plan": f"Fake plan for {t['title']}",
                    "criteria": ["stub file exists", "stub file says round 2"],
                }
            )
        return out

    def develop(self, team, s) -> dict:
        dev = kimi.pick(team, s["assignee"], s["task"], real=False)  # as a real run chooses
        time.sleep(self.delay)
        folder = team.cfg["developers"][s["assignee"]]["owns"][0]
        stub = Path(s["worktree"]) / folder / f"devteam-fake-{s['task']['id']}.txt"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(f"round {s.get('rounds', 0) + 1}\n")
        return {
            "session": s.get("session") or f"fake-{s['task']['id']}",
            "summary": f"{dev['provider']} wrote {stub.name}",
        }

    def ask(self, team, role: str, task: str, system: str, prompt: str, schema: dict) -> dict:
        """Architect: a brief, nothing missing. Analyst: the owner decides; no proposals."""
        if role == "architect":
            return {"brief": f"Fake brief for {task}.", "missing": [], "prerequisite": {}}
        if "proposals" in schema["properties"]:
            return {"proposals": []}
        return {"diagnosis": "unclear", "why": "fake analyst", "action": "report", "prompt": ""}

    def judge(self, team, s, checks: list[dict]) -> dict:
        time.sleep(self.delay)
        passed = s.get("rounds", 0) >= 2  # fails round 1, passes round 2
        return {
            "passed": passed,
            "feedback": "" if passed else "stub file must say round 2",
            "criteria": [{"criterion": c, "met": passed} for c in s["criteria"]],
            "summary": "Fake change: a stub file in the developer's folder.",
            "how_to_try": f"`git -C {s['worktree']} log --oneline -3`",
        }
