"""Hard scope enforcement (owner, 2026-10-17), in code rather than in prompts: after A7, where the
web developer needed backend/ and shipped the backend as a .patch file under docs/.

- scope: every path a task changes must be in its developer's `owns` (config.yaml) or the
  task's `writable`; a path in another developer's folders means the task needs a split.
- protected: paths no agent may change at all (devteam/, CI, the rules, gate configs).
- bypass: diff patterns that make gates pass without doing the work (skipped or focused tests,
  fewer assertions, looser thresholds, patch files, code parked under docs/).
"""

import fnmatch
import re

# Always protected; config.yaml `gates.protected` adds more (benchmark set, lint configs).
PROTECTED = ["devteam/*", ".github/*", "*backlog.yaml", "CLAUDE.md", "AGENTS.md", "*/CLAUDE.md",
             "*/AGENTS.md"]  # fmt: skip
DOCS = ("docs/",)  # another developer's docs/: reverted, not a reason to split the task
CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".sql", ".toml", ".rs")
PATCH_EXT = (".patch", ".diff", ".rej", ".orig")
MAX_DOC_CODE_LINES = 60  # one fenced block in a doc longer than this is code parked in docs/

TEST_FILE = re.compile(
    r"(^|/)(tests?|e2e|__tests__)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$"
)
SKIP = re.compile(
    r"\b(it|test|describe|suite)\.(skip|only|todo)\b|(?<![.\w])(xit|xdescribe|xtest|fit|fdescribe)"
    r"\s*\(|pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\s*\(|@unittest\.skip"
    r"|test\.fixme\b"
)
# a skip only while local test data is missing (CI has no data/): e2e on real bundles (A5)
DATA_SKIP = re.compile(r"\b(skip|skipif)\s*\(.*\b(existsSync|exists|is_file|isfile)\s*\(")
ASSERT = re.compile(
    r"^\s*assert\b|\bexpect\s*\(|\bself\.assert\w*\s*\(|\bpytest\.raises\b|\bassert_\w+\s*\("
)
TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test_|\b(it|test)\s*\(\s*['\"`]")
NUM = r"(-?\d+(?:\.\d+)?(?:e-?\d+)?)"
# (pattern, the bound gets looser when the number goes: "down" or "up")
BOUNDS = [
    (re.compile(r"(?<![<>=!])>=?\s*" + NUM), "down"),
    (re.compile(r"(?<![<>=!-])<=?\s*" + NUM), "up"),
    (re.compile(r"toBeGreaterThan(?:OrEqual)?\(\s*" + NUM), "down"),
    (re.compile(r"toBeLessThan(?:OrEqual)?\(\s*" + NUM), "up"),
    (re.compile(r"\b(?:abs|rel|atol|rtol)\s*=\s*" + NUM), "up"),
    (re.compile(r"toBeCloseTo\([^,]+,\s*" + NUM), "down"),  # fewer digits = looser
]


def is_protected(path: str, cfg: dict) -> bool:
    pats = PROTECTED + list(cfg.get("gates", {}).get("protected") or [])
    return any(fnmatch.fnmatch(path, p) for p in pats)


def allowed(cfg: dict, dev: str, task: dict) -> tuple[str, ...]:
    return tuple(cfg["developers"][dev]["owns"]) + tuple(task.get("writable") or [])


def foreign(path: str, cfg: dict, dev: str) -> bool:
    """In another developer's folders (the task needs that developer)."""
    mine = tuple(cfg["developers"][dev]["owns"])
    return not path.startswith(DOCS) and any(
        path.startswith(d) and not path.startswith(mine)
        for name, c in cfg["developers"].items()
        if name != dev
        for d in c["owns"]
    )


def check_paths(paths: list[str], cfg: dict, dev: str, task: dict) -> dict[str, list[str]]:
    """{"protected": [...], "out_of_scope": [...]} for the changed paths."""
    ok = allowed(cfg, dev, task)
    prot = sorted(p for p in paths if is_protected(p, cfg))
    out = sorted(p for p in paths if p not in prot and not p.startswith(ok))
    return {"protected": prot, "out_of_scope": out}


def _hunks(diff: str):
    """(path, removed lines, added lines) per hunk of a unified diff; path is the new name, or
    the old one for a deleted file."""
    path, old, minus, plus = "", "", [], []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            if minus or plus:
                yield path, minus, plus
            path, old, minus, plus = "", "", [], []
        elif line.startswith("--- "):
            old = line[6:] if line.startswith("--- a/") else ""
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else old
        elif line.startswith("@@"):
            if minus or plus:
                yield path, minus, plus
            minus, plus = [], []
        elif line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    if minus or plus:
        yield path, minus, plus


def _looser(old: str, new: str) -> str:
    """A number in a bound got looser between two otherwise equal lines."""
    if old == new or re.sub(NUM, "#", old) != re.sub(NUM, "#", new):
        return ""
    for pat, loose in BOUNDS:
        for a, b in zip(pat.findall(old), pat.findall(new), strict=False):
            a, b = float(a), float(b)
            if (loose == "down" and b < a) or (loose == "up" and b > a):
                return f"{old.strip()}  ->  {new.strip()}"
    return ""


def bypass(diff: str, paths: list[str]) -> list[str]:
    """Findings in `git diff -U0 dev...HEAD` that would let gates pass without the work."""
    found: list[str] = []
    for p in paths:
        if p.endswith(PATCH_EXT):
            found.append(f"adds a patch/diff file {p}: changes go in the code, never in a patch")
        elif p.startswith("docs/") and p.endswith(CODE_EXT):
            found.append(f"puts code under docs/: {p}")
    removed_asserts = added_asserts = removed_tests = added_tests = 0
    for path, minus, plus in _hunks(diff):
        is_test = bool(TEST_FILE.search(path))
        if is_test:
            for line in plus:
                if SKIP.search(line) and not DATA_SKIP.search(line):
                    found.append(f"skips or focuses a test in {path}: {line.strip()[:120]}")
            removed_asserts += sum(bool(ASSERT.search(x)) for x in minus)
            added_asserts += sum(bool(ASSERT.search(x)) for x in plus)
            removed_tests += sum(bool(TEST_DEF.search(x)) for x in minus)
            added_tests += sum(bool(TEST_DEF.search(x)) for x in plus)
            for old, new in zip(minus, plus, strict=False):
                if change := _looser(old, new):
                    found.append(f"loosens a test threshold in {path}: {change[:200]}")
        if path.startswith("docs/") and path.endswith(".md"):
            inside, run, longest = False, 0, 0
            for line in plus:
                if line.lstrip().startswith("```"):
                    inside, run = not inside, 0
                elif inside:
                    run += 1
                    longest = max(longest, run)
            if longest > MAX_DOC_CODE_LINES:
                found.append(f"puts a {longest}-line code block under docs/: {path}")
    if removed_asserts > added_asserts:
        found.append(f"removes test assertions: {removed_asserts} removed, {added_asserts} added")
    if removed_tests > added_tests:
        found.append(f"removes tests: {removed_tests} removed, {added_tests} added")
    return found
