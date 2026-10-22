"""Supervisor (owner, 2026-10-17): a task that goes wrong costs a bounded amount and stops alone.

Limits: config.yaml -> supervisor, overridden per task by its own `supervisor:` in backlog.yaml
(P3e re-runs COLMAP on every video: `max_round_min: 180`). A developer round is watched while
it runs (Team.run_proc polls): tokens are counted live from the tool's own output; a round with
no progress for `max_idle_min` (no output from the developer and no CPU work in its processes)
is stalled; no round runs longer than `max_round_min`. Long compute (COLMAP, a benchmark, e2e)
is progress: P3e was killed at 60 min while its refresh had just finished (2026-10-18). Over a
limit the round is killed with its whole process group and the task is blocked with the reason;
the other developers keep going.
"""

import json
import os
from pathlib import Path

DEFAULTS = {
    "max_tokens_per_round": 8_000_000,
    "max_tokens_per_task": 12_000_000,
    "max_round_min": 60,
    "max_idle_min": 20,
}
CPU_ACTIVE_S = 3.0  # CPU seconds the round's processes use per sample (60 s) to count as working


class TaskHalted(Exception):
    """A supervisor limit or `devteam stop <task>`: the step was killed, the task gets blocked.
    `output` is what the step printed until then (its tokens still count)."""

    def __init__(self, reason: str, output: str = ""):
        super().__init__(reason)
        self.output = output


def limits(cfg: dict, task: dict | None = None) -> dict:
    return {**DEFAULTS, **(cfg.get("supervisor") or {}), **((task or {}).get("supervisor") or {})}


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
            yield json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue


def _in_out(u: dict) -> tuple[int, int]:
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return sum(u.get(k) or 0 for k in keys), u.get("output_tokens") or 0


def _assistant_usage(lines: bytes) -> dict:
    """{message id: (in, out)} from a Claude Code transcript; one message is written as several
    entries (one per content block): the latest per id wins."""
    into: dict = {}
    for e in _json_lines(bytearray(), lines):
        m = e.get("message") if e.get("type") == "assistant" else None
        if isinstance(m, dict) and m.get("usage"):
            into[m.get("id") or len(into)] = _in_out(m["usage"])
    return into


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


class ClaudeTokens:
    """`claude -p --output-format stream-json --verbose`: every API message carries its usage,
    but as of the message's start: output tokens so far, often a handful. Live totals (for the
    limits) come from the stream; the split in / out of a killed round from the session
    transcript (~/.claude/projects/*/<session>.jsonl), which has each message's final counts
    (P3e: 954 output tokens in the stream, 27,543 in the transcript)."""

    def __init__(self, home: Path | None = None):
        self.home, self.buf, self.per_msg, self.session = home or claude_home(), bytearray(), {}, ""

    def feed(self, chunk: bytes) -> int:
        for e in _json_lines(self.buf, chunk):
            self.session = self.session or e.get("session_id") or ""
            m = e.get("message") if e.get("type") == "assistant" else None
            if isinstance(m, dict) and m.get("usage"):
                self.per_msg[m.get("id") or len(self.per_msg)] = _in_out(m["usage"])
        return sum(a + b for a, b in self.per_msg.values())

    def usage(self) -> dict:
        per = dict(self.per_msg)
        if self.session:
            for path in (self.home / "projects").glob(f"*/{self.session}.jsonl"):
                final = _assistant_usage(path.read_bytes())
                per.update((k, v) for k, v in final.items() if k in per)  # this round only
        return {"in": sum(a for a, _ in per.values()), "out": sum(b for _, b in per.values())}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


class CodexTokens:
    """`codex exec --json` prints usage only when the turn ends; its session rollout file
    (~/.codex/sessions/.../rollout-*-<thread>.jsonl) gets a `token_count` event after every
    model call, with the session's running total. A resumed session starts from its old total."""

    def __init__(self, session: str = "", home: Path | None = None):
        self.home, self.buf, self.thread = home or codex_home(), bytearray(), session
        self.path, self.pos, self.total, self.split = None, 0, 0, (0, 0)
        self.base = self._read() if session else 0  # tokens the session had before this round
        self.base_split = self.split

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
                self.split = (u.get("input_tokens") or 0, u.get("output_tokens") or 0)
                self.total = sum(self.split)
        self.pos -= len(tmp)  # an unfinished last line is read again next time
        return self.total

    def feed(self, chunk: bytes) -> int:
        for e in _json_lines(self.buf, chunk):
            if e.get("type") == "thread.started" and e.get("thread_id") != self.thread:
                self.thread, self.path, self.pos, self.total = e["thread_id"], None, 0, 0
                self.base, self.base_split = 0, (0, 0)  # a new session, not the resumed one
        return max(0, self._read() - self.base) if self.thread else 0

    def usage(self) -> dict:
        (i, o), (bi, bo) = self.split, self.base_split
        return {"in": max(0, i - bi), "out": max(0, o - bo)}


class RoundWatch:
    """Passed to Team.run_proc as `watch`: returns a reason to kill the round, or ""."""

    def __init__(self, counter, task_tokens: int, lim: dict):
        self.counter, self.task_tokens, self.lim, self.tokens = counter, task_tokens, lim, 0
        self.active_at, self.cpu = 0.0, None  # last progress (seconds into the round), CPU by pid

    def busy(self, chunk: bytes, cpu: dict[int, float] | None) -> bool:
        """Progress: the developer printed something, or its processes (the agent and what it
        runs: COLMAP, a benchmark, tests) used CPU since the last sample."""
        worked = False
        if cpu is not None:
            if self.cpu is not None:
                worked = sum(max(0.0, t - self.cpu.get(p, 0.0)) for p, t in cpu.items()) >= (
                    CPU_ACTIVE_S
                )
            self.cpu = cpu
        return bool(chunk) or worked

    def __call__(self, chunk: bytes, elapsed_s: float, cpu: dict[int, float] | None = None) -> str:
        self.tokens = n = self.counter.feed(chunk)
        if self.busy(chunk, cpu):
            self.active_at = elapsed_s
        lim = self.lim
        if n >= lim["max_tokens_per_round"]:
            return (
                f"supervisor: this round used {n / 1e6:.1f} M subscription tokens "
                f"(limit {lim['max_tokens_per_round'] / 1e6:g} M per round)"
            )
        if why := task_over(self.task_tokens + n, lim):
            return why
        if (idle := elapsed_s - self.active_at) >= lim["max_idle_min"] * 60:
            return (
                f"supervisor: no progress for {idle / 60:.0f} min: no output from the developer "
                f"and no CPU work in its processes (limit {lim['max_idle_min']} min)"
            )
        if elapsed_s >= lim["max_round_min"] * 60:
            return (
                f"supervisor: the round ran {elapsed_s / 60:.0f} min (limit {lim['max_round_min']} "
                "min per round; a task may set its own in backlog.yaml: "
                "`supervisor: {max_round_min: N}`)"
            )
        return ""
