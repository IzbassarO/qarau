"""Agent implementations. FakeAgents costs nothing and exercises the whole graph."""

import time
from pathlib import Path


class FakeAgents:
    """Brain plans by keyword, devs write a stub file, tester fails round 1, passes round 2."""

    delay = 1.0

    def plan(self, team, todo: list[dict]) -> list[dict]:
        time.sleep(self.delay)
        out = []
        for t in todo:
            text = (t["title"] + " " + t.get("goal", "")).lower()
            dev = t.get("owner") or (
                "dev_app" if "viewer" in text or "web" in text else "dev_pipeline"
            )
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
        time.sleep(self.delay)
        folder = team.cfg["developers"][s["assignee"]]["owns"][0]
        stub = Path(s["worktree"]) / folder / f"devteam-fake-{s['task']['id']}.txt"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(f"round {s.get('loops', 0) + 1}\n")
        return {
            "session": s.get("session") or f"fake-{s['task']['id']}",
            "summary": f"wrote {stub.name}",
        }

    def judge(self, team, s, checks: list[dict]) -> dict:
        time.sleep(self.delay)
        passed = s.get("loops", 0) >= 1
        return {
            "passed": passed,
            "feedback": "" if passed else "stub file must say round 2",
            "criteria": [{"criterion": c, "met": passed} for c in s["criteria"]],
            "summary": "Fake change: a stub file in the developer's folder.",
            "how_to_try": f"`git -C {s['worktree']} log --oneline -3`",
        }
