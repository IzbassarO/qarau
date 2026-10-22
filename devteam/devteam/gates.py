"""Automated gates that replace human review: secret scan, benchmark, GitHub PR + CI, merge,
and the weekly dev -> main release.

Everything that talks to the remote goes through `GitHub` (real, via gh) or `FakeGitHub`
(a local bare repo, for --fake and tests). Neither ever force-pushes, rewrites history,
deletes branches or bypasses branch protection.
"""

import hashlib
import itertools
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


NOT_FINAL = "not-final"  # in a cache dir: the gate found these numbers regressed


def gated(cfg: dict, name: str) -> bool:
    return cfg.get("per_video", {}).get(name, {}).get("gated", True)


def cached_benchmark(team, sha: str) -> dict | None:
    """The final result for the pipeline tree at `sha`. A run where a gated video failed, or
    one the gate found regressed, is never final: the next call measures again, so one noisy
    run cannot decide every later gate on the same tree."""
    out = team.home / "bench" / bench_key(team, sha)
    f = out / "results.json"
    if not f.exists() or (out / NOT_FINAL).exists():
        return None
    res = json.loads(f.read_text())
    bcfg = team.cfg["gates"]["benchmark"]
    return None if any(gated(bcfg, n) for n in res.get("errors", {})) else res


def benchmark(team, sha: str) -> dict | None:
    """Full benchmark results for commit `sha`, cached per pipeline tree (see
    cached_benchmark). Videos that failed are in results["errors"] (the command exits 1 then,
    but the others still count). None only if the command wrote no results at all."""
    if (res := cached_benchmark(team, sha)) is not None:
        return res
    return run_benchmark(team, sha, team.home / "bench" / bench_key(team, sha))


def run_benchmark(team, sha: str, out: Path, only: list[str] | None = None) -> dict | None:
    """Measures `sha` into `out` (only these videos, if given) in a throwaway detached worktree,
    so nothing it writes can end up in a task branch."""
    bcfg = team.cfg["gates"]["benchmark"]
    out.mkdir(parents=True, exist_ok=True)
    for old in ("results.json", NOT_FINAL):  # a run that writes nothing must not read as old
        (out / old).unlink(missing_ok=True)
    tmp = team.home / "bench-wt" / sha
    if not tmp.exists():
        team.git("worktree", "add", "--detach", str(tmp), sha)
    try:
        cmd = bcfg["command"].format(data=team.root / "data" / "samples", out=out)
        if only:
            cmd += " --only " + shlex.quote(",".join(only))
        r = team.run_proc(cmd, cwd=str(tmp), timeout=bcfg["timeout_min"] * 60)
        (out / "log.txt").write_text(team.redact(r["output"]), encoding="utf-8", errors="replace")
        if (out / "results.json").exists():
            return json.loads((out / "results.json").read_text())
        return None
    finally:
        team.git("worktree", "remove", "--force", str(tmp))


def recheck(team, sha: str, base: dict, first: dict, cfg: dict) -> tuple[dict, list[str], list]:
    """A regression must repeat to count: the regressed videos only are measured once more
    (run-to-run noise, docs/notes/benchmark-method.md). Returns (results with the second
    numbers for those videos, regressions left, packet lines showing both attempts). The
    tree's cache stays not final while a regression is left."""
    out = team.home / "bench" / bench_key(team, sha)
    worse = regressed(base, first, cfg)
    names = sorted(worse)
    out.mkdir(parents=True, exist_ok=True)
    (out / NOT_FINAL).write_text(", ".join(names))
    (out / "results.attempt-1.json").write_text(json.dumps(first))
    second = run_benchmark(team, sha, out / "retry", only=names) or {}
    videos = {n: v for n, v in first.get("videos", {}).items() if n not in worse}
    errors = {n: e for n, e in first.get("errors", {}).items() if n not in worse}
    videos.update({n: v for n, v in second.get("videos", {}).items() if n in worse})
    errors.update({n: e for n, e in second.get("errors", {}).items() if n in worse})
    final = {**first, "videos": videos, "errors": errors}
    left = regressions(base, final, cfg)
    (out / "results.json").write_text(json.dumps(final))
    if not left:
        (out / NOT_FINAL).unlink()
    found = "\n".join(f"  - {x}" for x in regressions(base, first, cfg))
    lines = [
        f"benchmark attempt 1: regression vs dev\n{found}\n\n{bench_table(first)}",
        f"benchmark attempt 2, only {', '.join(names)}: "
        + ("the regression repeated" if left else "no regression, so attempt 1 was noise")
        + "\n\n"
        + (bench_table(second) if second else "(the re-run wrote no results)"),
    ]
    return final, left, lines


def cosmetic_only(diff: str) -> bool:
    """`git diff -U0` changes nothing but whole-line comments (# or //), blank lines or spaces
    inside a line; an empty diff counts too. Indentation and line order still count as code."""

    def code(lines):
        return [
            x[: len(x) - len(x.lstrip())] + " ".join(x.split())
            for x in lines
            if x.strip() and not x.strip().startswith(("#", "//"))
        ]

    rows = [x for x in diff.splitlines() if not x.startswith(("+++", "---"))]
    return code(x[1:] for x in rows if x[:1] == "-") == code(x[1:] for x in rows if x[:1] == "+")


def regressed(base: dict, new: dict, cfg: dict) -> dict[str, list[str]]:
    """Per video that ran in the base run and is gated: what got worse. Contract (task P1):
    results.json = {"videos": {name: {registered_pct, models, reproj_px, points, scale_err_cm,
    time_s}}, "errors": {name: reason}}."""
    out: dict[str, list[str]] = {}
    for name, b in base.get("videos", {}).items():
        c = {**cfg, **cfg.get("per_video", {}).get(name, {})}  # measured run-to-run noise
        if not c.get("gated", True):
            continue
        found = out.setdefault(name, [])
        n = new.get("videos", {}).get(name)
        if n is None:
            why = new.get("errors", {}).get(name, "missing from the new run")
            found.append(f"{name}: {why}")
            continue
        if n["registered_pct"] < b["registered_pct"] - c["max_drop_registered_pct"]:
            found.append(
                f"{name}: registered {b['registered_pct']:.1f}% -> {n['registered_pct']:.1f}%"
            )
        if n["models"] > b["models"]:
            found.append(f"{name}: models {b['models']} -> {n['models']}")
        if n["reproj_px"] > b["reproj_px"] * (1 + c["max_rise_reproj_pct"] / 100):
            found.append(f"{name}: reprojection {b['reproj_px']:.2f} -> {n['reproj_px']:.2f} px")
        if n["points"] < b["points"] * (1 - c["max_drop_points_pct"] / 100):
            found.append(f"{name}: points {b['points']} -> {n['points']}")
        bs, ns = b.get("scale_err_cm"), n.get("scale_err_cm")
        if bs is not None and (ns is None or ns > bs + c["max_rise_scale_err_cm"]):
            found.append(f"{name}: scale error {bs} -> {ns} cm")
    return {k: v for k, v in out.items() if v}


def regressions(base: dict, new: dict, cfg: dict) -> list[str]:
    return [x for found in regressed(base, new, cfg).values() for x in found]


def not_compared(base: dict, cfg: dict) -> list[str]:
    """Said out loud in the packet: videos the gate could not or does not compare."""
    out = [f"{n}: failed on the base run ({e})" for n, e in base.get("errors", {}).items()]
    out += [
        f"{n}: not gated ({c.get('why', 'see config')})"
        for n, c in cfg.get("per_video", {}).items()
        if not gated(cfg, n)
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
SHOOT = Path(__file__).with_name("shoot.mjs")  # {shoot}: signs in, then screenshots one page
TEST_LOGIN = ("QARAU_TEST_USER", "QARAU_TEST_PASSWORD")  # devteam/.env: the tester's test user
VIEWPORTS = ["1440x900", "390x844"]  # screenshots.viewports when not set: desktop and phone


def variants(sc: dict, before: bool = False) -> list[tuple[str, str, str]]:
    """(viewport, theme, locale) per screenshot of one route: screenshots.viewports x themes
    (light / dark; none set = the page's default) in the first of screenshots.locales, then
    every viewport in each other locale with the first theme: kk/en text breaks layouts, not
    themes, and every image counts against the tester's request limit (real.MAX_IMAGES).
    `before` (dev-* shots, for comparison): the first locale only."""
    vs, ts = sc.get("viewports") or VIEWPORTS, sc.get("themes") or [""]
    ls = sc.get("locales") or [""]
    out = [(v, t, ls[0]) for v in vs for t in ts]
    return out if before else out + [(v, ts[0], lang) for lang in ls[1:] for v in vs]


def expected_shots(cfg: dict, s) -> int:
    sc = cfg["developers"][s["assignee"]].get("screenshots") or {}
    return len(s.get("routes") or []) * len(variants(sc))


def screenshots(
    team,
    s,
    routes: list[str],
    runs: Path | None = None,
    prefix: str = "",
    before: bool = False,
) -> tuple[list[str], list[str]]:
    """One PNG per route and variant (viewport, theme, locale) of the dev server in the task's
    worktree, for the tester (it sees them) and the packet, named
    <route>-<viewport>[-<theme>][-<locale>] (just <route> when there is one variant). Returns
    (paths, problems); a shot that fails is a problem, never silently dropped. `runs`: serve
    these runs instead of the developer's quick benchmark. `before`: dev-* shots (variants)."""
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
    if runs is None:  # a task that writes runs is shown runs_dir, others their quick benchmark
        runs = (
            team.runs_dir
            if team.writes_runs(s["task"])
            else team.home / "bench-dev" / s["task"]["id"]
        )
    srv = subprocess.Popen(  # {runs_dir}: config runs_dir (QARAU_RUNS_DIR), read-only here
        sc["serve"].format(  # {runs}: the runs this task's screenshots show (pipeline tasks)
            data=shlex.quote(str(team.root / "data")),
            runs_dir=shlex.quote(str(team.runs_dir)),
            runs=shlex.quote(str(runs)),
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
    login = {k: team.secrets[k] for k in TEST_LOGIN if team.secrets.get(k)}
    if len(login) < len(TEST_LOGIN):  # said, not a failed screenshot: the page is still shot
        team.emit(
            "tester",
            f"WARNING: no {' / '.join(TEST_LOGIN)} in devteam/.env: pages behind the login show "
            "the login form",
            task=s["task"]["id"],
        )
    try:
        for _ in range(60):
            if srv.poll() is not None:  # exited, e.g. --strictPort on a busy port
                return [], [f"dev server exited with {srv.returncode}: {sc['serve']}"]
            if _answers(sc["url"]):
                break
            time.sleep(1)
        else:
            return [], [f"dev server did not start: {sc['serve']}"]
        vs = variants(sc, before)
        for route, (size, theme, lang) in itertools.product(routes, vs):
            name = prefix + (re.sub(r"[^A-Za-z0-9]+", "-", route).strip("-") or "root")
            if len(vs) > 1:
                name += f"-{size}" + "".join(f"-{x}" for x in (theme, lang) if x)
            png = out / f"{name}.png"
            r = team.run_proc(
                sc["shoot"].format(
                    url=shlex.quote(sc["url"] + route),
                    png=shlex.quote(str(png)),
                    shoot=shlex.quote(str(SHOOT)),
                    size=shlex.quote(size.replace("x", ",")),
                    theme=shlex.quote(theme),
                    lang=shlex.quote(lang),
                ),
                cwd=s["worktree"],
                timeout=120,
                env={**os.environ, **login},  # never on the command line, never printed
            )
            if r["ok"] and png.exists():
                paths.append(str(png))
            else:
                what = f"{route} ({' '.join(x for x in (size, theme, lang) if x)})"
                problems.append(f"{what}: {team.redact(r['output'][-300:])}")
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
    """`dev-*`, the "before". Pipeline tasks: the same routes on dev's cached gate benchmark,
    same viewer code (never runs the benchmark, ~70 min, for this). Web tasks: the same routes
    built from current dev, on a fresh checkout of it (UX1: before/after of each page)."""
    sc = team.cfg["developers"][s["assignee"]].get("screenshots") or {}
    if not sc or not routes or team.writes_runs(s["task"]):
        return [], []  # a task's new runs don't exist in dev's benchmark output: nothing to compare
    dev = f"{team.remote_name}/{team.cfg['base_branch']}"
    if "{runs}" not in sc["serve"]:
        tid = s["task"]["id"]
        head = team.git("rev-parse", dev).strip()
        with team.clean_checkout(
            {**s, "task": {**s["task"], "id": f"{tid}-dev"}, "head": head}
        ) as wt:
            for cmd in team.cfg["developers"][s["assignee"]].get("setup", []):
                r = team.run_proc(cmd, cwd=wt, timeout=1800)
                if not r["ok"]:
                    return [], [f"dev screenshots skipped: `{cmd}` failed on {dev}"]
            return screenshots(team, {**s, "worktree": wt}, routes, prefix="dev-", before=True)
    runs = team.home / "bench" / bench_key(team, dev)
    if not (runs / "results.json").exists():
        return [], [f"no cached benchmark of {dev} to compare with: dev screenshots skipped"]
    return screenshots(team, s, routes, runs=runs, prefix="dev-", before=True)


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
        "Merged automatically when CI and the full benchmark are green.\n",
        encoding="utf-8",
        errors="replace",
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
