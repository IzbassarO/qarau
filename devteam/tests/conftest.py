import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from devteam.gates import EMPTY_BENCH

HERE = Path(__file__).parents[1]
CFG = yaml.safe_load((HERE / "config.yaml").read_text())
CFG["gates"]["benchmark"]["command"] = EMPTY_BENCH  # tests that need numbers set their own


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


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    monkeypatch.setattr("devteam.real.RETRY_WAITS", (0, 0, 0))
