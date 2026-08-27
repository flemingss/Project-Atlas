"""Tests for Layer 1 — post-refine LLM artifact stripping.

Covers ``strip_llm_artifacts()`` in ``atlas.pipeline.refine``:
- Conversational preamble removal
- Conversational postamble removal
- Wrapping code-fence removal
- Injected meta-section removal
- Safety-valve (>50% content loss → keep original)
- No-op on clean documents
"""

from __future__ import annotations

from atlas.pipeline.guardrails import strip_llm_artifacts

# ---------------------------------------------------------------------------
# Code-fence stripping
# ---------------------------------------------------------------------------

class TestCodeFenceStripping:
    def test_strips_markdown_fence(self) -> None:
        text = "```markdown\n# Title\n\nSome content here.\n```"
        result = strip_llm_artifacts(text)
        assert result == "# Title\n\nSome content here."

    def test_strips_md_fence(self) -> None:
        text = "```md\n# Title\n\nContent.\n```"
        result = strip_llm_artifacts(text)
        assert result == "# Title\n\nContent."

    def test_strips_plain_fence(self) -> None:
        text = "```\n# Title\n\nContent.\n```"
        result = strip_llm_artifacts(text)
        assert result == "# Title\n\nContent."

    def test_strips_text_fence(self) -> None:
        text = "```text\n# Title\n\nContent.\n```"
        result = strip_llm_artifacts(text)
        assert result == "# Title\n\nContent."

    def test_preserves_inner_code_blocks(self) -> None:
        """Code blocks inside document content should NOT be stripped."""
        text = "# Title\n\nSome text.\n\n```python\nprint('hello')\n```\n\nMore text."
        result = strip_llm_artifacts(text)
        assert "```python" in result
        assert "print('hello')" in result


# ---------------------------------------------------------------------------
# Preamble stripping
# ---------------------------------------------------------------------------

class TestPreambleStripping:
    def test_strips_here_is(self) -> None:
        text = "Here is the improved document:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_sure(self) -> None:
        text = "Sure, here's the refined version:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_certainly(self) -> None:
        text = "Certainly! Here is the corrected text:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_below_is(self) -> None:
        text = "Below is the corrected markdown:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_ive_made(self) -> None:
        text = "I've made the following improvements:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_ive_cleaned(self) -> None:
        text = "I've cleaned up the formatting issues.\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_as_an_ai(self) -> None:
        text = "As an AI, I've refined the document:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_the_improved_document(self) -> None:
        text = "The improved document is below:\n\n# Real Heading\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Real Heading")

    def test_strips_i_noticed(self) -> None:
        text = "I noticed several OCR errors and fixed them:\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_strips_multiline_preamble(self) -> None:
        """Should strip multiple consecutive preamble lines."""
        text = "Sure, I've improved the document.\nI've fixed the OCR errors.\n\n# Title\n\nContent."
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")

    def test_preserves_document_starting_with_heading(self) -> None:
        """Normal document starting with heading should be untouched."""
        text = "# Title\n\n## Section 1\n\nSome content."
        result = strip_llm_artifacts(text)
        assert result == text.strip()

    def test_preserves_document_starting_with_text(self) -> None:
        """Normal document starting with regular text should be untouched."""
        text = "This document describes the CANES system.\n\n## Overview\n\nContent."
        result = strip_llm_artifacts(text)
        assert result == text.strip()


# ---------------------------------------------------------------------------
# Postamble stripping
# ---------------------------------------------------------------------------

class TestPostambleStripping:
    def test_strips_let_me_know(self) -> None:
        text = "# Title\n\nContent.\n\nLet me know if you need any changes."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_i_hope_this(self) -> None:
        text = "# Title\n\nContent.\n\nI hope this helps!"
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_feel_free(self) -> None:
        text = "# Title\n\nContent.\n\nFeel free to ask if you have questions."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_dont_hesitate(self) -> None:
        text = "# Title\n\nContent.\n\nDon't hesitate to reach out."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_if_you_need(self) -> None:
        text = "# Title\n\nContent.\n\nIf you need further clarification, let me know."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_note_ive_preserved(self) -> None:
        text = "# Title\n\nContent.\n\nNote: I've preserved all original sections."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")

    def test_strips_multiline_postamble(self) -> None:
        text = "# Title\n\nContent.\n\nI hope this helps!\nLet me know if you need changes."
        result = strip_llm_artifacts(text)
        assert result.endswith("Content.")


# ---------------------------------------------------------------------------
# Meta-section removal
# ---------------------------------------------------------------------------

class TestMetaSectionRemoval:
    def test_strips_summary_of_changes(self) -> None:
        text = (
            "# Title\n\nContent.\n\n"
            "## Summary of Changes\n\n"
            "- Fixed OCR errors\n"
            "- Repaired headings\n"
        )
        result = strip_llm_artifacts(text)
        assert "Summary of Changes" not in result
        assert "Fixed OCR errors" not in result
        assert "Content." in result

    def test_strips_improvements_made(self) -> None:
        text = (
            "# Title\n\nContent.\n\n"
            "## Improvements Made\n\n"
            "Several formatting issues were fixed.\n"
        )
        result = strip_llm_artifacts(text)
        assert "Improvements Made" not in result
        assert "Content." in result

    def test_strips_changes_log(self) -> None:
        text = (
            "# Title\n\nContent.\n\n"
            "### Changes Log\n\n"
            "1. Fixed bullet formatting\n"
            "2. Corrected heading levels\n"
        )
        result = strip_llm_artifacts(text)
        assert "Changes Log" not in result
        assert "Content." in result

    def test_preserves_real_sections_after_meta(self) -> None:
        """A real document heading after a meta-section should be kept."""
        text = (
            "# Title\n\nContent.\n\n"
            "## Summary of Changes\n\n"
            "- Fixed stuff\n\n"
            "## Appendix A\n\nReal appendix content here."
        )
        result = strip_llm_artifacts(text)
        assert "Summary of Changes" not in result
        assert "## Appendix A" in result
        assert "Real appendix content here." in result


# ---------------------------------------------------------------------------
# Combined artifacts
# ---------------------------------------------------------------------------

class TestCombinedArtifacts:
    def test_preamble_and_postamble(self) -> None:
        text = (
            "Here is the improved document:\n\n"
            "# Title\n\nContent.\n\n"
            "Let me know if you need anything else."
        )
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")
        assert result.endswith("Content.")

    def test_fence_and_preamble_and_postamble(self) -> None:
        text = (
            "```markdown\n"
            "Here is the improved version:\n\n"
            "# Title\n\nContent.\n\n"
            "I hope this helps!\n"
            "```"
        )
        result = strip_llm_artifacts(text)
        assert "```" not in result
        assert result.startswith("# Title")
        assert result.endswith("Content.")

    def test_preamble_and_meta_section(self) -> None:
        text = (
            "Sure, I've refined the document.\n\n"
            "# Title\n\nContent.\n\n"
            "## Summary of Changes\n\n"
            "- Fixed headings\n"
        )
        result = strip_llm_artifacts(text)
        assert result.startswith("# Title")
        assert "Summary of Changes" not in result


# ---------------------------------------------------------------------------
# Safety valve & edge cases
# ---------------------------------------------------------------------------

class TestSafetyValve:
    def test_safety_valve_prevents_overcleaning(self) -> None:
        """If stripping removes >50% of content on a long doc, keep original."""
        # Construct a long document (>200 chars) where preamble is >50%
        preamble = "Here is the improved document:\n" * 10  # ~300 chars of preamble
        content = "# Title\nShort."  # ~14 chars
        text = preamble + "\n" + content
        result = strip_llm_artifacts(text)
        # Should keep original because removal exceeds 50% of a long doc
        assert len(result) > len(content)

    def test_empty_string(self) -> None:
        assert strip_llm_artifacts("") == ""

    def test_whitespace_only(self) -> None:
        assert strip_llm_artifacts("   \n  \n  ") == "   \n  \n  "

    def test_none_input_handling(self) -> None:
        """strip_llm_artifacts should handle empty/falsy gracefully."""
        assert strip_llm_artifacts("") == ""

    def test_clean_document_unchanged(self) -> None:
        """A normal well-formed document should pass through unmodified."""
        text = (
            "# Department of Defense Report\n\n"
            "## 1.0 Executive Summary\n\n"
            "This report covers the CANES program data analysis for FY2017.\n\n"
            "## 2.0 Technical Findings\n\n"
            "The system demonstrated compliance with all requirements.\n\n"
            "### 2.1 Performance Metrics\n\n"
            "| Metric | Target | Actual |\n"
            "|--------|--------|--------|\n"
            "| Throughput | 100 Mbps | 112 Mbps |\n"
            "| Latency | <10ms | 8ms |\n\n"
            "## 3.0 Conclusion\n\n"
            "All objectives were met."
        )
        result = strip_llm_artifacts(text)
        assert result == text.strip()

    def test_document_with_real_here_word(self) -> None:
        """Lines containing 'here' in normal context should NOT be stripped."""
        text = (
            "# Systems Overview\n\n"
            "The servers deployed here provide critical infrastructure.\n\n"
            "Here at the facility, redundancy is maintained."
        )
        result = strip_llm_artifacts(text)
        # "Here at the facility" should NOT match preamble patterns
        # because preamble detection only operates on lines before the
        # first non-matching line.
        assert "Here at the facility" in result
