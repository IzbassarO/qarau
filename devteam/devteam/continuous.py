"""Continuous mode: one loop per developer. When a developer's task ends, the brain plans the
next task for that developer only; the others keep working.

Each developer round is an ordinary graph run (its own checkpoint thread) planned for one
developer, so crash / stop / resume work exactly as in a single `devteam run`. Rounds that did
not finish are kept in .state/continuous.json and resumed first by the next continuous run.

Never here: a release to main (the single run's weekly release is not called), paid services
(no such step exists; developer env has no paid-service keys), edits to devteam/ (no developer
owns it; checked at start).
"""

import json
import os
import queue
import threading
import time
import traceback
from pathlib import Path

from devteam import costs
from devteam.costs import BudgetExceeded, SubscriptionLimit
from devteam.graph import OWNER_STOP, StopRun, Team

UNKNOWN_RESET_RETRY_S = 1800  # a limit without a reset time: try again every 30 min
MIN_WAIT_S = 60


class Continuous:
    def __init__(self, team: Team, graph, clock=time.time):
        c = team.cfg.get("continuous", {})
        self.max_s = c.get("max_hours", 10) * 3600
        self.max_tasks = c.get("max_tasks", 8)
        self.max_wait_s = c.get("limit_wait_max_hours", 5) * 3600
        self.team, self.graph, self.clock = team, graph, clock
        for name, d in team.cfg["developers"].items():  # devteam edits itself only by hand
            if any(p.startswith("devteam") for p in d["owns"]):
                raise ValueError(f"{name} owns devteam/: continuous mode refuses to start")
        self.events: queue.Queue = queue.Queue()  # for the live view
        self.cond = threading.Condition()
        self.busy: set[str] = set()  # developers with a round in flight
        self.started = 0  # tasks taken
        self.ended = 0  # rounds that ran a task and ended
        self.results: list[dict] = []  # in the order they ended
        self.stop_reason = ""
        self.crash: Exception | None = None  # a developer loop's own bug (not a round's)
        self.draining = False  # no new tasks, rounds in flight finish
        self.limit_since: dict[str, float] = {}
        self.paused: dict[str, str] = {}  # developer -> why it takes no more tasks this run
        self.state_file = team.home / "continuous.json"
        self.run_id = time.strftime("cont-%Y%m%d-%H%M%S")
        self.t0 = clock()
        self.mark = costs.last_row(team.home)
        self.resume = self.pending()  # rounds the last run left unfinished

    # --- pending rounds (survive a crash or a stop) ----------------------------------------
    def pending(self) -> dict[str, str]:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _set_pending(self, dev: str, thread: str | None):
        with self.cond:
            p = self.pending()
            if thread:
                p[dev] = thread
            else:
                p.pop(dev, None)
            tmp = self.state_file.with_suffix(".tmp")  # readers never see a half-written file
            tmp.write_text(json.dumps(p))
            os.replace(tmp, self.state_file)

    def ignored_drift(self) -> set[str]:
        """Task ids claimed by pending rounds: their in_progress is expected, not drift."""
        threads = set(self.pending().values())
        return {t["id"] for t in self.team.load_backlog() if t.get("run") in threads}

    # --- messages / stopping ---------------------------------------------------------------
    def say(self, agent: str, msg: str, task: str = "", status: str = ""):
        self.events.put(self.team.emit(agent, msg, task=task, status=status))

    def halt(self, reason: str):
        """Stop cleanly: no node starts any more; finished steps stay checkpointed."""
        with self.cond:
            if not self.stop_reason:
                self.stop_reason = reason
            self.team.stopping.set()
            self.cond.notify_all()
        self.say("brain", f"stopping: {reason}")

    def drain(self, reason: str):
        """No new tasks; rounds in flight finish."""
        with self.cond:
            if not self.stop_reason:
                self.stop_reason = reason
            self.draining = True
            self.cond.notify_all()

    def why_not_start(self) -> str:
        if self.clock() - self.t0 >= self.max_s:
            return f"max_hours reached ({self.max_s / 3600:g} h)"
        if self.started >= self.max_tasks:
            return f"max_tasks reached ({self.max_tasks})"
        spent, cap = costs.spent_24h(self.team.home), self.team.cfg["daily_budget_usd"]
        if spent >= cap:
            return f"daily API budget reached (${spent:.2f} of ${cap:.2f} in the last 24 h)"
        return ""

    def _timer(self):
        """max_hours: a clean stop even while every developer is busy."""
        while not self.team.stopping.wait(30):
            if self.clock() - self.t0 >= self.max_s:
                self.halt(f"max_hours reached ({self.max_s / 3600:g} h)")

    # --- one developer ---------------------------------------------------------------------
    def _round(self, thread: str, inp) -> dict:
        for _ns, e in self.graph.stream(
            inp, self._cfg(thread), stream_mode="custom", subgraphs=True
        ):
            self.events.put(e)
        return self.graph.get_state(self._cfg(thread)).values

    def _wait_limit(self, dev: str, task: str, e: SubscriptionLimit) -> bool:
        now = self.clock()
        left = self.limit_since.setdefault(dev, now) + self.max_wait_s - now
        if left <= 0:
            self.halt(f"{dev}: subscription limit did not reset within the wait cap")
            return False
        wait = (e.reset_at - now + 60) if e.reset_at else UNKNOWN_RESET_RETRY_S
        wait = max(MIN_WAIT_S, min(wait, left))
        costs.record_limit(self.team.home, dev, task, e.reset_at, wait, str(e))
        reset = time.strftime("%H:%M", time.localtime(e.reset_at)) if e.reset_at else "unknown"
        self.say(
            dev,
            f"subscription limit hit (resets {reset}); waiting {wait / 60:.0f} min: {e}",
            task=task,
            status="waiting",
        )
        deadline = time.monotonic() + wait
        while (left := deadline - time.monotonic()) > 0:
            if self.team.stopping.wait(min(30, left)):  # Ctrl+C ends the wait
                return False
            if task and self.team.stop_reason(task):  # the step runs again and stops the task
                break
        return True

    def dev_loop(self, dev: str):
        thread, n = self.resume.get(dev), 0
        while True:
            with self.cond:
                if self.team.stopping.is_set():
                    return
                inp = None
                if thread and not self.graph.get_state(self._cfg(thread)).tasks:  # see cli
                    thread = None  # finished, or crashed before its first step: start anew
                if not thread:
                    if self.draining:
                        return
                    if why := self.why_not_start():
                        self.drain(why)
                        return
                    n += 1
                    thread, inp = f"{self.run_id}-{dev}-{n}", {"developers": [dev]}
                self.started += 1
                self.busy.add(dev)
                ended_before = self.ended
            self._set_pending(dev, thread)
            try:
                vals = self._round(thread, inp)
            except SubscriptionLimit as e:  # stays busy while it waits: others keep waiting
                if self._wait_limit(dev, self._task_of(thread), e):
                    with self.cond:
                        self.started -= 1  # the same task again
                    continue  # same thread: the failed step runs again
                self._idle(dev)
                return
            except StopRun:
                self._idle(dev)
                return
            except BudgetExceeded as e:
                self._idle(dev)
                self.halt(f"daily API budget reached: {e}")
                return
            except Exception as e:  # crash: only this developer pauses, the others go on;
                # its round stays pending and resumes first in the next run (2026-10-18 23:39:
                # a vanished `claude` stopped the whole run)
                at = traceback.extract_tb(e.__traceback__)[-1]  # where, for the owner
                where = f"{Path(at.filename).name}:{at.lineno}"
                reason = f"{dev} paused: crashed at {where}: {self.team.redact(str(e))[:300]}"
                with self.cond:
                    self.paused[dev] = reason
                self._idle(dev)
                self.say("brain", reason)
                return
            self.limit_since.pop(dev, None)
            self._set_pending(dev, None)
            thread = None
            planned = bool(vals.get("assignments"))
            with self.cond:
                self.busy.discard(dev)
                if planned:
                    self.ended += 1
                    self.results += [{**r, "dev": dev} for r in vals.get("results", [])]
                    self.cond.notify_all()  # an ended task can unblock work for the others
                    # per developer: one developer's bad streak never stops the others; the
                    # owner's own `devteam stop <task>` is not a failure of the team
                    mine = [
                        r
                        for r in self.results
                        if r["dev"] == dev and not str(r.get("reason")).startswith(OWNER_STOP)
                    ]
                    streak = mine[-2:]
                    if len(streak) == 2 and all(r["status"] == "blocked" for r in streak):
                        reason = f"{dev} paused: 2 of its tasks in a row ended blocked "
                        reason += f"({streak[0]['id']}, {streak[1]['id']})"
                        self.paused[dev] = reason
                    else:
                        continue
                else:
                    self.started -= 1
                    self.cond.notify_all()  # a waiting developer may now see nobody busy
                    if self.ended != ended_before:
                        continue  # something ended while the brain looked: look again
                    if not self.busy:
                        return  # nobody can unblock anything: this developer is done
                    # wait until another round ends, then look again
                    self.cond.wait_for(
                        lambda: (
                            self.ended != ended_before  # noqa: B023 (called right here)
                            or not self.busy
                            or self.draining
                            or self.team.stopping.is_set()
                        )
                    )
                    if self.ended == ended_before:
                        return
                    continue
            self.say("brain", reason, status="")  # the run goes on while anyone can work
            return

    def _dev_main(self, dev: str):
        try:
            self.dev_loop(dev)
        except Exception as e:  # its thread would end silently: stop the run, it is a crash
            self.crash = self.crash or e
            self._idle(dev)
            self.halt(f"{dev}: the developer loop crashed: {self.team.redact(str(e))[:300]}")

    def _idle(self, dev: str):
        with self.cond:
            self.busy.discard(dev)
            self.cond.notify_all()

    def _cfg(self, thread: str) -> dict:
        return {"configurable": {"thread_id": thread}}

    def _task_of(self, thread: str) -> str:
        a = self.graph.get_state(self._cfg(thread)).values.get("assignments")
        return a[0]["task"]["id"] if a else ""

    # --- the run ---------------------------------------------------------------------------
    def start(self) -> list[threading.Thread]:
        self.say("brain", f"Kerege continuous run {self.run_id}", status="")
        threading.Thread(target=self._timer, daemon=True).start()
        threads = [
            threading.Thread(target=self._dev_main, args=(d,), name=d, daemon=True)
            for d in self.team.cfg["developers"]
        ]
        for t in threads:
            t.start()
        return threads

    def run(self) -> dict:
        """Blocking, no live view (tests, scripts)."""
        for t in self.start():
            t.join()
        return self.finish()

    def finish(self) -> dict:
        if not self.stop_reason:
            why = list(self.paused.values())
            if len(why) < len(self.team.cfg["developers"]):
                why.append(
                    "no plannable task left for " + ("the others" if why else "any developer")
                )
            self.stop_reason = (
                "no developer can take a task: " if self.paused else ""
            ) + "; ".join(why)
        self.team.stopping.set()  # the timer thread ends
        summary = {
            "run": self.run_id,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.t0)),
            "ended": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stop_reason": self.stop_reason,
            "tasks": self.results,
            "in_flight": {d: self._task_of(t) for d, t in self.pending().items()},
            "spend": [
                {"task": t, "agent": a, "billed": bool(b), "calls": n, "usd": usd or 0.0,
                 "tokens_in": ti or 0, "tokens_out": to or 0}
                for t, a, b, n, usd, ti, to in costs.spend_since(self.team.home, self.mark)
            ],
        }  # fmt: skip
        out = self.team.home / "runs" / f"{self.run_id}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(summary, indent=1))
        return summary


def summary_lines(s: dict) -> list[str]:
    """Markdown for the report and the terminal."""
    api = sum(x["usd"] for x in s["spend"] if x["billed"])
    lines = [
        f"### Kerege continuous run {s['run']} ({s['started'][11:16]}–{s['ended'][11:16]})",
        "",
        f"Stopped because: **{s['stop_reason']}** · API spend: **${api:.2f}**",
        "",
        "| Task | Developer | Result | PR | API $ | Subscription tokens in / out |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in s["tasks"]:
        mine = [x for x in s["spend"] if x["task"] == r["id"]]
        usd = sum(x["usd"] for x in mine if x["billed"])
        sub = [x for x in mine if not x["billed"]]
        lines.append(
            f"| {r['id']} | {r['dev']} | {r['status']} | {r.get('pr') or '—'} | {usd:.2f} | "
            f"{sum(x['tokens_in'] for x in sub):,} / {sum(x['tokens_out'] for x in sub):,} |"
        )
    for dev, tid in s.get("in_flight", {}).items():
        lines.append(f"| {tid or '(planning)'} | {dev} | not finished, resumes next run | — | | |")
    if not s["tasks"] and not s.get("in_flight"):
        lines.append("| — | — | no task ran | — | 0 | — |")
    brain = sum(x["usd"] for x in s["spend"] if x["agent"] == "brain")
    lines += ["", f"Brain (planning) API: ${brain:.2f}.", ""]
    return lines


def runs_of_day(home: Path, day: str) -> list[dict]:
    out = []
    for p in sorted((home / "runs").glob("cont-*.json")):
        try:
            s = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if s.get("started", "")[:10] <= day <= s.get("ended", s.get("started", ""))[:10]:
            out.append(s)  # every day the run touched
    return out
