"""Supervisor (owner, 2026-10-17): a task that goes wrong costs a bounded amount and stops alone.

Limits: config.yaml -> supervisor. A developer round is watched while it runs (Team.run_proc
polls): tokens are counted live from the tool's own output, and the round may run at most
`max_round_min` (the orchestrator commits only when a round ends, so that is the longest a task
goes without a new commit). Over a limit the round is killed with its whole process group and
the task is blocked with the reason; the other developers keep going.
"""

import json
import os
from pathlib import Path

DEFAULTS = {
    "max_tokens_per_round": 8_000_000,
    "max_tokens_per_task": 12_000_000,
    "max_round_min": 60,
}


class TaskHalted(Exception):
    """A supervisor limit or `devteam stop <task>`: the step was killed, the task gets blocked.
    `output` is what the step printed until then (its tokens still count)."""

    def __init__(self, reason: str, output: str = ""):
        super().__init__(reason)
        self.output = output


def limits(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("supervisor") or {})}


def task_over(tokens: int, lim: dict) -> str:
    if tokens >= lim["max_tokens_per_task"]:
        return (
            f"supervisor: the task used {tokens / 1e6:.1f} M subscription tokens "
            f"(limit {lim['max_tokens_per_task'] / 1e6:g} M per task)"
        )
    return ""


def _json_lines(buf: bytearray, chunk: bytes):
    """Whole JSON lines from a byte stream (a chunk may end mid-line)."""
    buf += chunk
    *lines, rest = bytes(buf).split(b"\n")
    buf[:] = rest
    for line in lines:
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _usage_sum(u: dict) -> int:
    return sum(
        u.get(k) or 0
        for k in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        )
    )


class ClaudeTokens:
    """`claude -p --output-format stream-json --verbose`: every API message carries its usage.
    One message can arrive as several events (one per content block): latest per id wins."""

    def __init__(self):
        self.buf, self.per_msg = bytearray(), {}

    def feed(self, chunk: bytes) -> int:
        for e in _json_lines(self.buf, chunk):
            m = e.get("message") if e.get("type") == "assistant" else None
            if isinstance(m, dict) and m.get("usage"):
                self.per_msg[m.get("id") or len(self.per_msg)] = _usage_sum(m["usage"])
        return sum(self.per_msg.values())


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


class CodexTokens:
    """`codex exec --json` prints usage only when the turn ends; its session rollout file
    (~/.codex/sessions/.../rollout-*-<thread>.jsonl) gets a `token_count` event after every
    model call, with the session's running total. A resumed session starts from its old total."""

    def __init__(self, session: str = "", home: Path | None = None):
        self.home, self.buf, self.thread = home or codex_home(), bytearray(), session
        self.path, self.pos, self.total = None, 0, 0
        self.base = self._read() if session else 0  # tokens the session had before this round

    def _read(self) -> int:
        if self.path is None:
            found = sorted((self.home / "sessions").glob(f"*/*/*/rollout-*{self.thread}.jsonl"))
            if not found:
                return self.total
            self.path = found[-1]
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            chunk = f.read()
        self.pos += len(chunk)
        tmp = bytearray()
        for e in _json_lines(tmp, chunk):
            p = e.get("payload") or {}
            info = p.get("info") if p.get("type") == "token_count" else None
            if info and info.get("total_token_usage"):
                u = info["total_token_usage"]  # input_tokens includes the cached ones
                self.total = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        self.pos -= len(tmp)  # an unfinished last line is read again next time
        return self.total

    def feed(self, chunk: bytes) -> int:
        for e in _json_lines(self.buf, chunk):
            if e.get("type") == "thread.started" and e.get("thread_id") != self.thread:
                self.thread, self.path, self.pos, self.total = e["thread_id"], None, 0, 0
                self.base = 0  # a new session, not the resumed one
        return max(0, self._read() - self.base) if self.thread else 0


class RoundWatch:
    """Passed to Team.run_proc as `watch`: returns a reason to kill the round, or ""."""

    def __init__(self, counter, task_tokens: int, lim: dict):
        self.counter, self.task_tokens, self.lim, self.tokens = counter, task_tokens, lim, 0

    def __call__(self, chunk: bytes, elapsed_s: float) -> str:
        self.tokens = n = self.counter.feed(chunk)
        lim = self.lim
        if n >= lim["max_tokens_per_round"]:
            return (
                f"supervisor: this round used {n / 1e6:.1f} M subscription tokens "
                f"(limit {lim['max_tokens_per_round'] / 1e6:g} M per round)"
            )
        if why := task_over(self.task_tokens + n, lim):
            return why
        if elapsed_s >= lim["max_round_min"] * 60:
            return (
                f"supervisor: the round ran {elapsed_s / 60:.0f} min without a new commit "
                f"(limit {lim['max_round_min']} min)"
            )
        return ""
