import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parents[1]
CFG = yaml.safe_load((HERE / "config.yaml").read_text())


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo shaped like ours: dev branch, pipeline/, web/, ignored .env/data/."""
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
    return repo
