import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from devteam.agents import FakeAgents
from devteam.gates import EMPTY_BENCH
from devteam.graph import Team

HERE = Path(__file__).parents[1]
CFG = yaml.safe_load((HERE / "config.yaml").read_text())
CFG["gates"]["benchmark"]["command"] = EMPTY_BENCH  # tests that need numbers set their own
CFG["gates"]["e2e"]["command"] = "echo e2e on port {port}"  # never a real Playwright run
CFG["gates"]["backlog_check"] = ""  # tests that need it set their own
# The flow tests model two developers (dev_pipeline, dev_app); dev_core, the second core
# developer on claude-su (owner 2026-10-30), is tested where named, with REAL_DEVELOPERS.
REAL_DEVELOPERS = copy.deepcopy(CFG["developers"])
del CFG["developers"]["dev_core"]


def real_backlog() -> list[dict]:
    """devteam/backlog.yaml, or the one a status commit is about to push (DEVTEAM_BACKLOG)."""
    path = os.environ.get("DEVTEAM_BACKLOG") or HERE / "backlog.yaml"
    return yaml.safe_load(Path(path).read_text())["tasks"]


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo shaped like ours (dev + main, pipeline/, web/, ignored .env/data/)
    with a bare repo next to it as the remote."""
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{k}_NAME", "t")
        monkeypatch.setenv(f"GIT_{k}_EMAIL", "t@t")
    repo = tmp_path / "repo"
    (repo / "devteam").mkdir(parents=True)
    shutil.copy(HERE / "tests" / "backlog.yaml", repo / "devteam" / "backlog.yaml")
    (repo / "README.md").write_text("x\n")
    (repo / "pipeline").mkdir()
    (repo / "pipeline" / "a.py").write_text("a = 1\n")
    (repo / "web").mkdir()
    (repo / "web" / "app.ts").write_text("export {}\n")
    (repo / ".gitignore").write_text(".env\ndata/\ndevteam/.state/\n")
    git(repo, "init", "-q", "-b", "dev")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    git(repo, "branch", "main")
    remote = tmp_path / "remote.git"  # stands in for GitHub
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "push", "-q", str(remote), "dev", "main")
    return repo


def remote_of(repo) -> str:
    return str(repo.parent / "remote.git")


def team_for(repo, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    return Team(repo, cfg, {}, FakeAgents(), fake=True, remote=remote_of(repo))


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr("devteam.graph.POLL_S", 0.1)


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    monkeypatch.setattr("devteam.real.RETRY_WAITS", (0, 0, 0))
    monkeypatch.setattr("devteam.graph.GIT_NET_WAITS", (0, 0, 0))


@pytest.fixture(autouse=True)
def banners(tmp_path, monkeypatch):
    """No test shows a real macOS banner: `osascript` on PATH only logs its arguments (also for
    the CLI child processes a test starts). Returns the log: one line per banner."""
    bin_ = tmp_path / "osascript-bin"
    bin_.mkdir()
    log = tmp_path / "banners.log"
    osa = bin_ / "osascript"
    osa.write_text(f'#!/bin/sh\nprintf "%s | %s\\n" "$3" "$4" >> "{log}"\n')
    osa.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    return log
