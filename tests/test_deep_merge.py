from __future__ import annotations

from atlas.deep_merge import deep_merge


def test_deep_merge_nested() -> None:
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    patch = {"b": {"c": 99}, "e": "x"}
    out = deep_merge(base, patch)
    assert out == {"a": 1, "b": {"c": 99, "d": 3}, "e": "x"}
