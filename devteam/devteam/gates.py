"""Automated gates that replace human review: secret scan, benchmark, GitHub PR + CI, merge,
and the weekly dev -> main release.

Everything that talks to the remote goes through `GitHub` (real, via gh) or `FakeGitHub`
(a local bare repo, for --fake and tests). Neither ever force-pushes, rewrites history,
deletes branches or bypasses branch protection.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from devteam import procs

FORBIDDEN_GH_FLAGS = {"--admin", "--delete-branch", "-d"}
FORBIDDEN_GH_COMMANDS = {"api", "ruleset", "secret", "variable", "repo", "workflow", "release"}
CI_OK = {"SUCCESS", "SKIPPED", "NEUTRAL"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


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
# --fake and tests: a benchmark that measures nothing (the real set takes ~70 min per commit)
EMPTY_BENCH = (
    'python3 -c "import json, pathlib, sys; o = pathlib.Path(sys.argv[1]); '
    "o.mkdir(parents=True, exist_ok=True); "
    "(o / 'results.json').write_text(json.dumps(dict(videos=dict(), errors=dict())))\" {out}"
)


def bench_key(team, sha: str) -> str:
    """Cache key: the git trees of `when_paths` at `sha`. A merge that leaves pipeline/ alone
    (a web task, docs) reuses the result instead of another ~70 min run."""
    paths = team.cfg["gates"]["benchmark"]["when_paths"]
    trees = [team.git("rev-parse", f"{sha}:{p.rstrip('/')}").strip() for p in paths]
    return hashlib.sha1(" ".join(trees).encode()).hexdigest()


def cached_benchmark(team, sha: str) -> dict | None:
    f = team.home / "bench" / bench_key(team, sha) / "results.json"
    return json.loads(f.read_text()) if f.exists() else None


def benchmark(team, sha: str) -> dict | None:
    """Full benchmark results for commit `sha`, run in a throwaway detached worktree (so
    nothing it writes can end up in a task branch) and cached per pipeline tree. Videos that
    failed are in results["errors"] (the command exits 1 then, but the others still count).
    None only if the command wrote no results at all."""
    bcfg = team.cfg["gates"]["benchmark"]
    out = team.home / "bench" / bench_key(team, sha)
    if (res := cached_benchmark(team, sha)) is not None:
        return res
    out.mkdir(parents=True, exist_ok=True)
    tmp = team.home / "bench-wt" / sha
    if not tmp.exists():
        team.git("worktree", "add", "--detach", str(tmp), sha)
    try:
        cmd = bcfg["command"].format(data=team.root / "data" / "samples", out=out)
        r = team.run_proc(cmd, cwd=str(tmp), timeout=bcfg["timeout_min"] * 60)
        (out / "log.txt").write_text(team.redact(r["output"]))
        if (out / "results.json").exists():
            return json.loads((out / "results.json").read_text())
        return None
    finally:
        team.git("worktree", "remove", "--force", str(tmp))


def regressions(base: dict, new: dict, cfg: dict) -> list[str]:
    """Per video that ran in the base run and is gated. Contract (task P1): results.json =
    {"videos": {name: {registered_pct, models, reproj_px, points, scale_err_cm, time_s}},
    "errors": {name: reason}}."""
    out = []
    for name, b in base.get("videos", {}).items():
        c = {**cfg, **cfg.get("per_video", {}).get(name, {})}  # measured run-to-run noise
        if not c.get("gated", True):
            continue
        n = new.get("videos", {}).get(name)
        if n is None:
            why = new.get("errors", {}).get(name, "missing from the new run")
            out.append(f"{name}: {why}")
            continue
        if n["registered_pct"] < b["registered_pct"] - c["max_drop_registered_pct"]:
            out.append(
                f"{name}: registered {b['registered_pct']:.1f}% -> {n['registered_pct']:.1f}%"
            )
        if n["models"] > b["models"]:
            out.append(f"{name}: models {b['models']} -> {n['models']}")
        if n["reproj_px"] > b["reproj_px"] * (1 + c["max_rise_reproj_pct"] / 100):
            out.append(f"{name}: reprojection {b['reproj_px']:.2f} -> {n['reproj_px']:.2f} px")
        if n["points"] < b["points"] * (1 - c["max_drop_points_pct"] / 100):
            out.append(f"{name}: points {b['points']} -> {n['points']}")
        bs, ns = b.get("scale_err_cm"), n.get("scale_err_cm")
        if bs is not None and (ns is None or ns > bs + c["max_rise_scale_err_cm"]):
            out.append(f"{name}: scale error {bs} -> {ns} cm")
    return out


def not_compared(base: dict, cfg: dict) -> list[str]:
    """Said out loud in the packet: videos the gate could not or does not compare."""
    out = [f"{n}: failed on the base run ({e})" for n, e in base.get("errors", {}).items()]
    out += [
        f"{n}: not gated ({c.get('why', 'see config')})"
        for n, c in cfg.get("per_video", {}).items()
        if not c.get("gated", True)
    ]
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
    rows += [f"| {n} | FAILED: {e} | | | | | |" for n, e in sorted(res.get("errors", {}).items())]
    return "\n".join(rows)


# --- screenshots (web and pipeline tasks) ---------------------------------------------------
def screenshots(
    team, s, routes: list[str], runs: Path | None = None, prefix: str = ""
) -> tuple[list[str], list[str]]:
    """One PNG per route of the dev server in the task's worktree, for the tester (it sees
    them) and the packet. Returns (paths, problems); a route that fails is a problem, never
    silently dropped. `runs`: serve these runs instead of the developer's quick benchmark."""
    sc = team.cfg["developers"][s["assignee"]].get("screenshots")
    if not sc or not routes:
        return [], []
    out = team.home / "packets" / s["task"]["id"]
    out.mkdir(parents=True, exist_ok=True)
    if _answers(sc["url"]):  # never screenshot a server left over from earlier (old :5199)
        return [], [
            f"{sc['url']} already answers before our dev server started: a stale server holds "
            "the port. Stop it; screenshots are only taken of a fresh server."
        ]
    marker = procs.new_marker()
    srv = subprocess.Popen(  # {data}: the main checkout's data/ (runs to show, read-only)
        sc["serve"].format(  # {runs}: the developer's quick benchmark output (pipeline tasks)
            data=shlex.quote(str(team.root / "data")),
            runs=shlex.quote(str(runs or team.home / "bench-dev" / s["task"]["id"])),
        ),
        shell=True,
        cwd=s["worktree"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, procs.MARKER: marker},
    )
    with team._lock:
        team._procs.add(srv)  # `devteam stop` kills it too
    paths, problems = [], []
    try:
        for _ in range(60):
            if srv.poll() is not None:  # exited, e.g. --strictPort on a busy port
                return [], [f"dev server exited with {srv.returncode}: {sc['serve']}"]
            if _answers(sc["url"]):
                break
            time.sleep(1)
        else:
            return [], [f"dev server did not start: {sc['serve']}"]
        for route in routes:
            name = prefix + (re.sub(r"[^A-Za-z0-9]+", "-", route).strip("-") or "root")
            png = out / f"{name}.png"
            r = team.run_proc(
                sc["shoot"].format(url=shlex.quote(sc["url"] + route), png=shlex.quote(str(png))),
                cwd=s["worktree"],
                timeout=120,
            )
            if r["ok"] and png.exists():
                paths.append(str(png))
            else:
                problems.append(f"{route}: {r['output'][-300:]}")
    finally:
        procs.reap(srv.pid, marker)  # the server and anything it started; the port is free
        srv.wait()
        with team._lock:
            team._procs.discard(srv)
    return paths, problems


def _answers(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError:  # a server answered, with an error status
        return True
    except OSError:
        return False


def dev_screenshots(team, s, routes: list[str]) -> tuple[list[str], list[str]]:
    """Pipeline tasks: the same routes on dev's cached gate benchmark, same viewer code, so the
    tester compares dev and the branch. Never runs the benchmark (~70 min) for this."""
    sc = team.cfg["developers"][s["assignee"]].get("screenshots") or {}
    if "{runs}" not in sc.get("serve", "") or not routes:
        return [], []
    dev = f"{team.remote_name}/{team.cfg['base_branch']}"
    runs = team.home / "bench" / bench_key(team, dev)
    if not (runs / "results.json").exists():
        return [], [f"no cached benchmark of {dev} to compare with: dev screenshots skipped"]
    return screenshots(team, s, routes, runs=runs, prefix="dev-")


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
                "number,state,url,headRefOid",
            )
        )
        prs = [p for p in prs if p["state"] in ("OPEN", "MERGED")]
        return prs[0] if prs else None

    def open_pr(self, head: str, base: str, title: str, body_file: Path) -> dict:
        pr = self.find_pr(head, base)
        if pr and pr["state"] == "OPEN":
            self.gh("pr", "edit", str(pr["number"]), "--body-file", str(body_file))
            return pr
        self.gh(  # an old merged PR is never reused: new commits get a new PR
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
                return (not bad, "\n".join(bad + self.failed_logs(bad)) or "all checks green")
            time.sleep(30)
        return False, f"CI did not finish in {timeout_s // 60} min"

    def failed_logs(self, bad: list[str], lines: int = 150) -> list[str]:
        """The developer sees the error itself, not only a link (P2b sent back blind)."""
        out = []
        for run in dict.fromkeys(re.findall(r"/actions/runs/(\d+)", "\n".join(bad))):
            try:
                log = self.gh("run", "view", run, "--log-failed")
            except RuntimeError as e:
                log = f"(log unavailable: {e})"
            tail = "\n".join(ANSI.sub("", log).splitlines()[-lines:]).replace("```", "'''")
            out.append(
                f"\nLast {lines} lines of `gh run view {run} --log-failed`:\n```\n{tail}\n```"
            )
        return out

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
        return next(  # newest first, like `gh pr list`
            (
                p
                for p in reversed(self.prs.values())
                if p["head"] == head and p["base"] == base and p["state"] in ("OPEN", "MERGED")
            ),
            None,
        )

    def open_pr(self, head, base, title, body_file):
        pr = self.find_pr(head, base)
        if not pr or pr["state"] != "OPEN":
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
        pr["state"], pr["headRefOid"] = "MERGED", sha
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
    if on_dev is None or on_dev.get("errors"):
        log = team.home / "bench" / bench_key(team, dev_sha) / "log.txt"
        tail = log.read_text()[-600:] if log.exists() else "(no log)"
        why = "\n".join(f"- {n}: {e}" for n, e in (on_dev or {}).get("errors", {}).items())
        return f"release blocked: the full benchmark failed on {base} @ {dev_sha[:8]}:\n" + (
            why or tail
        )
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
    tag = tag_release(team, team.gh.url(pr))
    team.emit("brain", f"released {base} -> {main}: {team.gh.url(pr)}, tag {tag}", status="idle")
    return f"released: {team.gh.url(pr)} (tag {tag})"


def tag_release(team, note: str) -> str:
    """Annotated tag release-YYYY-MM-DD[.N] on the released main, pushed; tags never move, so
    every release can be checked out or rolled back to. Idempotent (resume after a crash)."""
    main = team.cfg["release_branch"]
    team.fetch(main)
    sha = team.git("rev-parse", f"{team.remote_name}/{main}").strip()
    day, n = time.strftime("%Y-%m-%d"), 1
    while True:
        tag = f"release-{day}" + (f".{n}" if n > 1 else "")
        have = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
            cwd=team.root,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not have:
            team.git("tag", "-a", tag, sha, "-m", f"Release {day}: {note}")
            break
        if have == sha:  # already tagged (re-run)
            break
        n += 1  # a second release the same day
    team.git("push", "-q", team.remote_url, f"refs/tags/{tag}")
    return tag
