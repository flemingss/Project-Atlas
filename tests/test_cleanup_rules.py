"""Tests for atlas.pipeline.cleanup_rules — config-driven cleanup rule engine."""

from __future__ import annotations

import pytest

from atlas.pipeline.cleanup_rules import (
    CleanupRule,
    DocContext,
    RuleMatch,
    RuleStep,
    RuleApplicationResult,
    apply_rule,
    find_matching_rule,
    parse_rules,
    _step_strip_lines_matching,
    _step_rewrite_pattern,
    _step_strip_headers_footers,
    _step_normalize_headings,
    _step_merge_hardwrapped,
    _step_fix_bullets,
    _step_html_unescape,
)
from atlas.pipeline.cleanup import CleanupNode


# ---------------------------------------------------------------------------
# parse_rules
# ---------------------------------------------------------------------------

class TestParseRules:
    def test_empty_list(self) -> None:
        assert parse_rules([]) == []

    def test_basic_rule(self) -> None:
        raw = [
            {
                "name": "r1",
                "match": {"tenant_id": "t1"},
                "steps": [{"kind": "fix_bullets"}],
                "tags": ["auto_fix_only"],
            }
        ]
        rules = parse_rules(raw)
        assert len(rules) == 1
        assert rules[0].name == "r1"
        assert rules[0].match.tenant_id == "t1"
        assert rules[0].steps[0].kind == "fix_bullets"
        assert rules[0].tags == ["auto_fix_only"]

    def test_step_as_string(self) -> None:
        raw = [{"name": "r2", "steps": ["normalize_headings"]}]
        rules = parse_rules(raw)
        assert rules[0].steps[0].kind == "normalize_headings"

    def test_skips_invalid(self) -> None:
        raw = [None, {"name": "r3", "steps": []}]  # type: ignore[list-item]
        rules = parse_rules(raw)
        # None entry should be skipped; valid entry kept
        assert len(rules) == 1
        assert rules[0].name == "r3"

    def test_empty_match_is_catch_all(self) -> None:
        raw = [{"name": "all", "match": {}, "steps": []}]
        rules = parse_rules(raw)
        assert rules[0].match == RuleMatch()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

class TestFindMatchingRule:
    def _rule(self, name: str, **match_kwargs: str) -> CleanupRule:
        return CleanupRule(name=name, match=RuleMatch(**match_kwargs), steps=[], tags=[])

    def test_first_match_wins(self) -> None:
        r1 = self._rule("first", tenant_id="t1")
        r2 = self._rule("second", tenant_id="t1")
        ctx = DocContext(tenant_id="t1")
        assert find_matching_rule([r1, r2], ctx) is r1

    def test_no_match(self) -> None:
        r1 = self._rule("specific", tenant_id="other")
        ctx = DocContext(tenant_id="t1")
        assert find_matching_rule([r1], ctx) is None

    def test_catch_all(self) -> None:
        specific = self._rule("specific", tenant_id="other")
        catch_all = self._rule("catch_all")
        ctx = DocContext(tenant_id="t1")
        assert find_matching_rule([specific, catch_all], ctx) is catch_all

    def test_multi_field_match(self) -> None:
        r = self._rule("multi", tenant_id="t1", project_id="p1")
        assert find_matching_rule([r], DocContext(tenant_id="t1", project_id="p1")) is r
        assert find_matching_rule([r], DocContext(tenant_id="t1", project_id="p2")) is None

    def test_filename_pattern(self) -> None:
        r = CleanupRule(
            name="pdfs",
            match=RuleMatch(filename_pattern="*.pdf"),
            steps=[],
            tags=[],
        )
        assert find_matching_rule([r], DocContext(filename="report.pdf")) is r
        assert find_matching_rule([r], DocContext(filename="notes.txt")) is None

    def test_mime_type_case_insensitive(self) -> None:
        r = self._rule("mime", mime_type="Application/PDF")
        assert find_matching_rule([r], DocContext(mime_type="application/pdf")) is r

    def test_corpus_id_must_match(self) -> None:
        """Regression: corpus_id mismatch caused rules to silently skip."""
        r = self._rule("corp", tenant_id="local", project_id="default", corpus_id="default")
        # Correct corpus_id matches
        assert find_matching_rule(
            [r], DocContext(tenant_id="local", project_id="default", corpus_id="default")
        ) is r
        # Empty corpus_id (the old bug) must NOT match
        assert find_matching_rule(
            [r], DocContext(tenant_id="local", project_id="default", corpus_id="")
        ) is None
        # Wrong corpus_id must NOT match
        assert find_matching_rule(
            [r], DocContext(tenant_id="local", project_id="default", corpus_id="other")
        ) is None


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

class TestSteps:
    def test_strip_lines_matching(self) -> None:
        text = "keep\nPage 1\nkeep2"
        result, count = _step_strip_lines_matching(text, {"pattern": r"^Page \d+"})
        assert count == 1
        assert "Page 1" not in result
        assert "keep" in result

    def test_strip_lines_no_pattern(self) -> None:
        text, count = _step_strip_lines_matching("hello", {})
        assert text == "hello"
        assert count == 0

    def test_rewrite_pattern(self) -> None:
        text = "foo bar foo"
        result, count = _step_rewrite_pattern(text, {"pattern": "foo", "replacement": "baz"})
        assert result == "baz bar baz"
        assert count == 2

    def test_strip_headers_footers_first_last(self) -> None:
        text = "h1\nh2\nbody\nf1\nf2"
        result, count = _step_strip_headers_footers(text, {"first_n": 2, "last_n": 2})
        assert result == "body"
        assert count == 4

    def test_strip_headers_footers_patterns(self) -> None:
        text = "CONFIDENTIAL\nbody\nPage 1 of 5"
        result, count = _step_strip_headers_footers(
            text, {"patterns": [r"^CONFIDENTIAL$", r"^Page \d+ of \d+$"]}
        )
        assert result == "body"
        assert count == 2

    def test_normalize_headings_setext_h1(self) -> None:
        text = "Title\n=====\nBody"
        result, count = _step_normalize_headings(text, {})
        assert result == "# Title\nBody"
        assert count == 1

    def test_normalize_headings_setext_h2(self) -> None:
        text = "Sub\n---\nBody"
        result, count = _step_normalize_headings(text, {})
        assert result == "## Sub\nBody"
        assert count == 1

    def test_normalize_headings_already_atx(self) -> None:
        text = "# Title\n## Sub"
        result, count = _step_normalize_headings(text, {})
        assert result == text
        assert count == 0

    def test_merge_hardwrapped(self) -> None:
        text = "This is a long\nsentence that was\nhard-wrapped.\n\nNew para."
        result, count = _step_merge_hardwrapped(text, {})
        assert "This is a long sentence that was hard-wrapped." in result
        assert "New para." in result
        assert count == 2  # 2 extra lines joined

    def test_merge_hardwrapped_preserves_headings(self) -> None:
        text = "# Title\nBody line"
        result, _count = _step_merge_hardwrapped(text, {})
        assert result.startswith("# Title")

    def test_fix_bullets_normalises(self) -> None:
        text = "* item1\n+ item2\n- item3"
        result, count = _step_fix_bullets(text, {"marker": "-"})
        assert "- item1" in result
        assert "- item2" in result
        assert count == 2  # * and + converted

    def test_fix_bullets_in_code_fence(self) -> None:
        text = "```\n* keep\n```"
        result, count = _step_fix_bullets(text, {})
        assert "* keep" in result
        assert count == 0

    def test_html_unescape_named_entities(self) -> None:
        text = "Veeam Backup &amp; Replication &lt;v12&gt;"
        result, count = _step_html_unescape(text, {})
        assert result == "Veeam Backup & Replication <v12>"
        assert count == 3

    def test_html_unescape_decimal_entity(self) -> None:
        text = "em-dash&#8212;here"
        result, count = _step_html_unescape(text, {})
        assert result == "em-dash\u2014here"
        assert count == 1

    def test_html_unescape_hex_entity(self) -> None:
        text = "smart&#x2019;quote"
        result, count = _step_html_unescape(text, {})
        assert result == "smart\u2019quote"
        assert count == 1

    def test_html_unescape_no_entities(self) -> None:
        text = "No entities here & just text."
        result, count = _step_html_unescape(text, {})
        assert result == text
        assert count == 0

    def test_html_unescape_nbsp(self) -> None:
        text = "word&nbsp;word"
        result, count = _step_html_unescape(text, {})
        assert result == "word\xa0word"  # non-breaking space
        assert count == 1


# ---------------------------------------------------------------------------
# apply_rule
# ---------------------------------------------------------------------------

class TestApplyRule:
    def test_applies_steps_in_order(self) -> None:
        rule = CleanupRule(
            name="test",
            match=RuleMatch(),
            steps=[
                RuleStep(kind="fix_bullets", params={"marker": "-"}),
                RuleStep(kind="normalize_headings"),
            ],
            tags=["auto_fix_only"],
        )
        md = "Title\n=====\n* item1\n+ item2"
        result = apply_rule(rule, md)
        assert isinstance(result, RuleApplicationResult)
        assert "fix_bullets" in result.steps_applied
        assert "normalize_headings" in result.steps_applied
        assert result.tags == ["auto_fix_only"]
        assert "# Title" in result.markdown
        assert "- item1" in result.markdown

    def test_unknown_step_skipped(self) -> None:
        rule = CleanupRule(
            name="unk",
            match=RuleMatch(),
            steps=[RuleStep(kind="nonexistent_step")],
            tags=[],
        )
        result = apply_rule(rule, "hello")
        assert result.steps_applied == []
        assert result.markdown == "hello"

    def test_no_change_rule(self) -> None:
        rule = CleanupRule(
            name="noop",
            match=RuleMatch(),
            steps=[RuleStep(kind="fix_bullets")],
            tags=[],
        )
        result = apply_rule(rule, "- already fine")
        assert result.steps_applied == []
        assert result.fix_counts["fix_bullets"] == 0


# ---------------------------------------------------------------------------
# CleanupNode integration with rules
# ---------------------------------------------------------------------------

class TestCleanupNodeWithRules:
    @pytest.fixture
    def node(self) -> CleanupNode:
        return CleanupNode()

    async def test_without_rules_backwards_compatible(self, node: CleanupNode) -> None:
        """Calling clean() without config/doc_context works as before."""
        result = await node.clean(markdown="# Title\n\nBody text for testing purposes here.")
        assert result.rules_applied == []
        assert result.rule_tags == []
        assert result.fix_counts == {}

    async def test_with_matching_rule(self, node: CleanupNode) -> None:
        config = {
            "cleanup_rules": [
                {
                    "name": "pdf_fix",
                    "match": {"mime_type": "application/pdf"},
                    "steps": [{"kind": "fix_bullets", "marker": "-"}],
                    "tags": ["auto_fix_only"],
                }
            ]
        }
        doc_ctx = {"tenant_id": "t1", "project_id": "p1", "mime_type": "application/pdf"}
        md = "* item1\n+ item2"
        result = await node.clean(markdown=md, doc_context=doc_ctx, config=config)
        assert "pdf_fix" in result.rules_applied
        assert "auto_fix_only" in result.rule_tags
        assert "- item1" in result.cleaned_markdown

    async def test_no_matching_rule(self, node: CleanupNode) -> None:
        config = {
            "cleanup_rules": [
                {
                    "name": "specific",
                    "match": {"tenant_id": "other"},
                    "steps": [{"kind": "fix_bullets"}],
                }
            ]
        }
        doc_ctx = {"tenant_id": "t1"}
        result = await node.clean(markdown="* item", doc_context=doc_ctx, config=config)
        assert result.rules_applied == []

    async def test_empty_rules_list(self, node: CleanupNode) -> None:
        config = {"cleanup_rules": []}
        doc_ctx = {"tenant_id": "t1"}
        result = await node.clean(markdown="# Title\n\nBody.", doc_context=doc_ctx, config=config)
        assert result.rules_applied == []


# ---------------------------------------------------------------------------
# Routing tag awareness
# ---------------------------------------------------------------------------

class TestRoutingTagAwareness:
    """Test that routing reads rule_tags from cleanup results."""

    def test_suspicious_content_routes_to_hitl(self) -> None:
        from atlas.pipeline.routing import decide_next_step

        results = {"cleanup": {"rule_tags": ["suspicious_content"]}}
        d = decide_next_step(
            current_node="cleanup",
            results=results,
            state_snapshot={},
            config={"thresholds": {}},
        )
        assert d.target == "hitl"
        assert "suspicious_content" in d.reason

    def test_hard_failure_routes_to_failed(self) -> None:
        from atlas.pipeline.routing import decide_next_step

        results = {"cleanup": {"rule_tags": ["hard_failure"]}}
        d = decide_next_step(
            current_node="cleanup",
            results=results,
            state_snapshot={},
            config={"thresholds": {}},
        )
        assert d.target == "failed"

    def test_auto_fix_only_routes_to_judge(self) -> None:
        from atlas.pipeline.routing import decide_next_step

        results = {"cleanup": {"rule_tags": ["auto_fix_only"]}}
        d = decide_next_step(
            current_node="cleanup",
            results=results,
            state_snapshot={},
            config={"thresholds": {}},
        )
        assert d.target == "judge"

    def test_no_tags_routes_to_judge(self) -> None:
        from atlas.pipeline.routing import decide_next_step

        d = decide_next_step(
            current_node="cleanup",
            results={},
            state_snapshot={},
            config={"thresholds": {}},
        )
        assert d.target == "judge"
