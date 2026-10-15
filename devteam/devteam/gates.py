"""Automated gates that replace human review: secret scan, benchmark, GitHub PR + CI, merge,
and the weekly dev -> main release.

Everything that talks to the remote goes through `GitHub` (real, via gh) or `FakeGitHub`
(a local bare repo, for --fake and tests). Neither ever force-pushes, rewrites history,
deletes branches or bypasses branch protection.
"""

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

FORBIDDEN_GH_FLAGS = {"--admin", "--delete-branch", "-d"}
FORBIDDEN_GH_COMMANDS = {"api", "ruleset", "secret", "variable", "repo", "workflow", "release"}
CI_OK = {"SUCCESS", "SKIPPED", "NEUTRAL"}


# --- secret scan -----------------------------------------------------------------------------
def gitleaks(team, cwd: str, *args: str) -> list[dict]:
    """Findings (secrets redacted). Fails closed: no gitleaks -> an error, not a pass."""
    if not shutil.which("gitleaks"):
        raise RuntimeError("secret scan unavailable: install gitleaks (brew install gitleaks)")
    r = subprocess.run(
        ["gitleaks", "git", *args, "--no-banner", "--redact", "-f", "json", "-r", "-", "."],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if r.returncode not in (0, 1):
        raise RuntimeError(f"gitleaks failed: {r.stderr[-500:]}")
    out = r.stdout.strip()
    found = json.loads(out) if out.startswith("[") else []
    return [{"file": f["File"], "line": f["StartLine"], "rule": f["RuleID"]} for f in found]


def env_secrets_in(team, cwd: str, paths: list[str]) -> list[str]:
    """Files that contain a value from devteam/.env (gitleaks may not know its format)."""
    values = [v for v in team.secrets.values() if v and len(v) > 8]
    hits = []
    for p in paths:
        f = Path(cwd) / p
        if values and f.is_file() and not f.is_symlink():
            text = f.read_bytes().decode("utf-8", "replace")
            if any(v in text for v in values):
                hits.append(p)
    return hits


# --- benchmark -------------------------------------------------------------------------------
def benchmark(team, sha: str) -> dict | None:
    """Benchmark results for commit `sha`, run in a throwaway detached worktree (so nothing it
    writes can end up in a task branch) and cached per commit. None if the command is missing
    or fails, e.g. before the benchmark task (P1) has landed."""
    bcfg = team.cfg["gates"]["benchmark"]
    out = team.home / "bench" / sha
    if (out / "results.json").exists():
        return json.loads((out / "results.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    tmp = team.home / "bench-wt" / sha
    if not tmp.exists():
        team.git("worktree", "add", "--detach", str(tmp), sha)
    try:
        cmd = bcfg["command"].format(data=team.root / "data" / "samples", out=out)
        r = team.run_proc(cmd, cwd=str(tmp), timeout=bcfg["timeout_min"] * 60)
        (out / "log.txt").write_text(team.redact(r["output"]))
        if r["ok"] and (out / "results.json").exists():
            return json.loads((out / "results.json").read_text())
        (out / "results.json").unlink(missing_ok=True)
        return None
    finally:
        team.git("worktree", "remove", "--force", str(tmp))


def regressions(base: dict, new: dict, cfg: dict) -> list[str]:
    """Per video present in both runs. Contract (task P1): results.json =
    {"videos": {name: {registered_pct, models, reproj_px, points, scale_err_cm, time_s}}}."""
    out = []
    for name, b in base.get("videos", {}).items():
        n = new.get("videos", {}).get(name)
        if n is None:
            out.append(f"{name}: missing from the new run")
            continue
        if n["registered_pct"] < b["registered_pct"] - cfg["max_drop_registered_pct"]:
            out.append(
                f"{name}: registered {b['registered_pct']:.1f}% -> {n['registered_pct']:.1f}%"
            )
        if n["models"] > b["models"]:
            out.append(f"{name}: models {b['models']} -> {n['models']}")
        if n["reproj_px"] > b["reproj_px"] * (1 + cfg["max_rise_reproj_pct"] / 100):
            out.append(f"{name}: reprojection {b['reproj_px']:.2f} -> {n['reproj_px']:.2f} px")
        if n["points"] < b["points"] * (1 - cfg["max_drop_points_pct"] / 100):
            out.append(f"{name}: points {b['points']} -> {n['points']}")
        bs, ns = b.get("scale_err_cm"), n.get("scale_err_cm")
        if bs is not None and (ns is None or ns > bs + cfg["max_rise_scale_err_cm"]):
            out.append(f"{name}: scale error {bs} -> {ns} cm")
    return out


def bench_table(res: dict) -> str:
    rows = [
        "| Video | Registered % | Models | Reproj. px | Points | Scale err. cm | Time s |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, v in sorted(res.get("videos", {}).items()):
        rows.append(
            f"| {name} | {v['registered_pct']:.1f} | {v['models']} | "
            f"{v['reproj_px']:.2f} | {v['points']} | {v.get('scale_err_cm', '—')} | "
            f"{v.get('time_s', '—')} |"
        )
    return "\n".join(rows)


# --- GitHub ----------------------------------------------------------------------------------
class GitHub:
    """Real GitHub through the gh CLI."""

    def __init__(self, team):
        self.team = team

    def gh(self, *args: str) -> str:
        if args[0] in FORBIDDEN_GH_COMMANDS or FORBIDDEN_GH_FLAGS & set(args):
            raise PermissionError(f"forbidden for agents: gh {' '.join(args)}")
        r = subprocess.run(["gh", *args], cwd=self.team.root, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[-500:]}")
        return r.stdout

    def find_pr(self, head: str, base: str) -> dict | None:
        prs = json.loads(
            self.gh(
                "pr",
                "list",
                "--head",
                head,
                "--base",
                base,
                "--state",
                "all",
                "--json",
                "number,state,url",
            )
        )
        prs = [p for p in prs if p["state"] in ("OPEN", "MERGED")]
        return prs[0] if prs else None

    def open_pr(self, head: str, base: str, title: str, body_file: Path) -> dict:
        pr = self.find_pr(head, base)
        if pr and pr["state"] == "OPEN":
            self.gh("pr", "edit", str(pr["number"]), "--body-file", str(body_file))
            return pr
        if pr:  # already merged (resume after a crash)
            return pr
        self.gh(
            "pr",
            "create",
            "--head",
            head,
            "--base",
            base,
            "--title",
            title,
            "--body-file",
            str(body_file),
        )
        return self.find_pr(head, base)

    def wait_ci(self, number: int, sha: str, timeout_s: int) -> tuple[bool, str]:
        """Waits until every check on exactly `sha` has finished."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            pr = json.loads(
                self.gh("pr", "view", str(number), "--json", "headRefOid,statusCheckRollup")
            )
            checks = pr["statusCheckRollup"] or []
            if (
                pr["headRefOid"] == sha
                and checks
                and all(c.get("status", "COMPLETED") == "COMPLETED" for c in checks)
            ):
                bad = [
                    f"{c.get('name')}: {c.get('conclusion') or c.get('state')} "
                    f"{c.get('detailsUrl', '')}"
                    for c in checks
                    if (c.get("conclusion") or c.get("state")) not in CI_OK
                ]
                return (not bad, "\n".join(bad) or "all checks green")
            time.sleep(30)
        return False, f"CI did not finish in {timeout_s // 60} min"

    def merge(self, number: int, sha: str):
        # Merge commit, exactly the tested commit; no --admin (branch protection applies).
        self.gh("pr", "merge", str(number), "--merge", "--match-head-commit", sha)

    def url(self, pr: dict) -> str:
        return pr["url"]


class FakeGitHub:
    """Same interface on a local bare repo: PRs in memory, CI result configurable,
    merge = a merge commit pushed to the base branch of the bare repo."""

    def __init__(self, team, ci_ok: bool = True):
        self.team, self.ci_ok, self.prs = team, ci_ok, {}
        self.merged: list[int] = []
        self._lock = threading.Lock()  # GitHub serializes merges too

    def find_pr(self, head, base):
        return next(
            (
                p
                for p in self.prs.values()
                if p["head"] == head and p["base"] == base and p["state"] in ("OPEN", "MERGED")
            ),
            None,
        )

    def open_pr(self, head, base, title, body_file):
        pr = self.find_pr(head, base)
        if not pr:
            n = len(self.prs) + 1
            pr = self.prs[n] = {
                "number": n,
                "head": head,
                "base": base,
                "state": "OPEN",
                "url": f"fake://pr/{n}",
                "title": title,
            }
        pr["body"] = Path(body_file).read_text()
        return pr

    def wait_ci(self, number, sha, timeout_s):
        return (True, "all checks green") if self.ci_ok else (False, "web: FAILURE (fake CI)")

    def merge(self, number, sha):
        with self._lock:
            self._merge(number, sha)

    def _merge(self, number, sha):
        pr, t = self.prs[number], self.team
        tmp = t.home / "merge-wt"
        t.fetch(pr["base"])
        t.git("worktree", "add", "--detach", str(tmp), f"{t.remote_name}/{pr['base']}")
        try:
            t.git("merge", "--no-ff", "--no-edit", sha, cwd=tmp)
            subprocess.run(
                ["git", "push", "-q", t.remote_url, f"HEAD:refs/heads/{pr['base']}"],
                cwd=tmp,
                check=True,
                capture_output=True,
            )
        finally:
            t.git("worktree", "remove", "--force", str(tmp))
        pr["state"] = "MERGED"
        self.merged.append(number)

    def url(self, pr):
        return pr["url"]


# --- weekly release --------------------------------------------------------------------------
def release(team) -> str:
    """dev -> main through a PR, merged only if CI and the full benchmark are green on dev."""
    base, main = team.cfg["base_branch"], team.cfg["release_branch"]
    team.fetch(base, main)
    dev_sha = team.git("rev-parse", f"{team.remote_name}/{base}").strip()
    main_sha = team.git("rev-parse", f"{team.remote_name}/{main}").strip()
    ahead = int(team.git("rev-list", "--count", f"{main_sha}..{dev_sha}").strip())
    if not ahead:
        return f"release: nothing new on {base}"
    team.emit("brain", f"release: benchmark on {base} @ {dev_sha[:8]}", status="working")
    on_dev = benchmark(team, dev_sha)
    if on_dev is None:
        log = team.home / "bench" / dev_sha / "log.txt"
        tail = log.read_text()[-600:] if log.exists() else "(no log)"
        return f"release blocked: the full benchmark failed on {base} @ {dev_sha[:8]}:\n{tail}"
    on_main = benchmark(team, main_sha)
    if on_main and (reg := regressions(on_main, on_dev, team.cfg["gates"]["benchmark"])):
        return "release blocked, benchmark regression vs main:\n- " + "\n- ".join(reg)
    log = team.git("log", "--oneline", "--no-merges", f"{main_sha}..{dev_sha}")
    body = team.home / "release.md"
    body.write_text(
        f"Weekly release: {ahead} commits from `{base}`.\n\n## Benchmark on "
        f"`{base}`\n\n{bench_table(on_dev)}\n\n## Commits\n\n```\n{log}```\n\n"
        "Merged automatically when CI and the full benchmark are green.\n"
    )
    pr = team.gh.open_pr(base, main, f"Release {time.strftime('%Y-%m-%d')}", body)
    ok, why = team.gh.wait_ci(pr["number"], dev_sha, team.cfg["gates"]["ci_timeout_min"] * 60)
    if not ok:
        return f"release blocked, CI on {base}: {why} ({team.gh.url(pr)})"
    team.gh.merge(pr["number"], dev_sha)
    (team.home / "last_release").write_text(time.strftime("%Y-%m-%d"))
    team.emit("brain", f"released {base} -> {main}: {team.gh.url(pr)}", status="idle")
    return f"released: {team.gh.url(pr)}"
