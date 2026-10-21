"""ADR numbers are unique (2026-10-20: UX1a and P4a-1b both merged an ADR 0018). Runs in the
devteam CI job, so a task that adds a duplicate number is sent back by GitHub CI."""

import re
from collections import Counter

from conftest import HERE

DECISIONS = HERE.parent / "docs" / "decisions"


def duplicate_numbers(names: list[str]) -> list[str]:
    nums = Counter(m.group(1) for n in names if (m := re.match(r"(\d{4})-.*\.md$", n)))
    return sorted(n for n, k in nums.items() if k > 1)


def test_duplicate_adr_numbers_are_found():
    assert duplicate_numbers(["0018-a.md", "0018-b.md", "0019-c.md", "README.md"]) == ["0018"]
    assert duplicate_numbers(["0001-a.md", "0002-a.md"]) == []


def test_adr_numbers_are_unique():
    names = [p.name for p in DECISIONS.glob("*.md")]
    assert names, f"no ADRs found in {DECISIONS}"
    assert duplicate_numbers(names) == [], "renumber the newer ADR (and its references)"
