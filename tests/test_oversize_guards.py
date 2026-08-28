"""Guards for documents too large for a model to process in one call.

Atlas ingests technical manuals that run to thousands of pages. Judge and
refine both embed the entire document in their prompt with no truncation, so
these paths need explicit budgets — without them an oversized manual becomes an
over-length API request, which comes back as a non-retryable 4xx and loses the
whole ingest over a quality check the model was too small to perform.
"""

from __future__ import annotations

import httpx
import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.openai_compat import (
    _NON_RETRYABLE_TIMEOUT,
    OpenAICompatibleProvider,
    _is_retryable_http,
)
from atlas.pipeline.cleanup import CleanupNode
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import create_pipeline_context


def _orchestrator(*, judge_ctx: int | None, config: dict | None = None) -> PipelineOrchestrator:
    provider = DeterministicProvider()
    return PipelineOrchestrator(
        ingest_node=IngestNode(),
        cleanup_node=CleanupNode(),
        judge_node=JudgeNode(
            provider=provider,
            model_name="det-judge",
            model_params={},
            max_context_tokens=judge_ctx,
        ),
        refine_node=RefineNode(provider=provider, model_name="det-refine", model_params={}),
        metadata_node=MetadataNode(tier1_provider=provider, tier1_model="det-meta"),
        config=config or {"thresholds": {"judge_cutoff_refine": 4}},
    )


def _context(markdown: str):
    ctx = create_pipeline_context(
        doc_id="d", doc_version="v1", tenant_id="t", project_id="p", corpus_id="c"
    )
    ctx.state.markdown_projection = markdown
    return ctx


# ---------------------------------------------------------------------------
# Judge oversize guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_document_skips_judging_instead_of_failing() -> None:
    orch = _orchestrator(judge_ctx=1_000)
    ctx = _context("x" * 400_000)          # ~100k tokens, far over budget

    await orch._process_judge(ctx)

    judge = ctx.results["judge"]
    assert judge["judge_version"].startswith("skipped-oversize")
    assert "SKIPPED" in judge["confidence_rationale"]
    # Reported at cutoff so routing continues to metadata rather than refining
    # a document the judge never actually read.
    assert judge["needs_refinement"] is False


@pytest.mark.asyncio
async def test_document_within_budget_is_graded_normally() -> None:
    orch = _orchestrator(judge_ctx=1_000_000)
    ctx = _context("# Title\n\nShort body.")

    await orch._process_judge(ctx)

    assert not ctx.results["judge"]["judge_version"].startswith("skipped-oversize")


@pytest.mark.asyncio
async def test_guard_is_opt_in() -> None:
    """No configured budget keeps the pre-existing behaviour."""
    orch = _orchestrator(judge_ctx=None)
    ctx = _context("x" * 400_000)

    await orch._process_judge(ctx)

    assert not ctx.results["judge"]["judge_version"].startswith("skipped-oversize")


# ---------------------------------------------------------------------------
# Timeout classification
# ---------------------------------------------------------------------------


def test_read_timeouts_are_not_retried() -> None:
    """A read timeout means the model was still working, not that the call failed.

    Retrying replays the same oversized request and burns another full timeout
    window — with max_retries=3 that is 4x the wall clock before failing anyway.
    """
    assert not _is_retryable_http(httpx.ReadTimeout("too slow"))
    assert not _is_retryable_http(httpx.WriteTimeout("too slow"))


def test_connect_failures_are_still_retried() -> None:
    """These never reached the model, so replaying them is free."""
    assert _is_retryable_http(httpx.ConnectError("refused"))
    assert _is_retryable_http(httpx.ConnectTimeout("no route"))


def test_server_errors_retry_client_errors_do_not() -> None:
    def _status(code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://x/v1/chat/completions")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    assert _is_retryable_http(_status(429))
    assert _is_retryable_http(_status(503))
    assert not _is_retryable_http(_status(400))
    assert not _is_retryable_http(_status(404))


def test_read_timeout_is_in_the_non_retryable_set() -> None:
    assert httpx.ReadTimeout in _NON_RETRYABLE_TIMEOUT


# ---------------------------------------------------------------------------
# Error shaping
# ---------------------------------------------------------------------------


def test_zdr_provider_explains_routing_restriction_on_4xx() -> None:
    """A ZDR-restricted model failing looks like a bad model id without this."""
    provider = OpenAICompatibleProvider(
        base_url="https://openrouter.ai/api/v1", api_key="k", zdr_enforced=True
    )
    msg = provider._describe_http_failure(
        status_code=404, body="no endpoints", model="vendor/model", op="chat"
    )
    assert "zero-data-retention" in msg
    assert "enforce_zdr: false" in msg


def test_non_zdr_provider_does_not_mention_it() -> None:
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234")
    msg = provider._describe_http_failure(
        status_code=404, body="nope", model="qwen3-14b", op="chat"
    )
    assert "zero-data-retention" not in msg


def test_timeout_message_points_at_the_two_real_fixes() -> None:
    provider = OpenAICompatibleProvider(base_url="http://x", read_timeout_s=1800)
    msg = provider._timeout_message(
        model="m", op="chat", exc=httpx.ReadTimeout("slow")
    )
    assert "read_timeout_s" in msg
    assert "max_context_tokens" in msg


# ---------------------------------------------------------------------------
# Reasoning models returning no content
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _StubClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        return _StubResponse(self._payload)


@pytest.mark.asyncio
async def test_null_content_from_exhausted_reasoning_budget(monkeypatch) -> None:
    """Reasoning models bill thinking against max_tokens and can return no answer.

    Observed live against z-ai/glm-5.3-flash: HTTP 200, a populated `reasoning`
    field, and `content: null`, because the budget was sized for the visible
    answer alone. Left unhandled this reaches the tag stripper as a regex over
    None and surfaces as "expected string or bytes-like object, got 'NoneType'",
    which points at neither the cause nor the fix.
    """
    payload = {
        "choices": [
            {
                "message": {"content": None, "reasoning": "thinking " * 50},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 17, "completion_tokens": 16, "total_tokens": 33},
    }
    provider = OpenAICompatibleProvider(base_url="http://stub")
    monkeypatch.setattr(
        "atlas.llm.openai_compat.httpx.AsyncClient",
        lambda *a, **k: _StubClient(payload),
    )

    with pytest.raises(ValueError) as excinfo:
        await provider.chat(
            model="z-ai/glm-5.3-flash",
            messages=[],
            params={"max_tokens": 16},
        )

    msg = str(excinfo.value)
    assert "max_tokens" in msg
    assert "reasoning" in msg
    assert "NoneType" not in msg


@pytest.mark.asyncio
async def test_normal_content_is_unaffected(monkeypatch) -> None:
    payload = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {},
    }
    provider = OpenAICompatibleProvider(base_url="http://stub")
    monkeypatch.setattr(
        "atlas.llm.openai_compat.httpx.AsyncClient",
        lambda *a, **k: _StubClient(payload),
    )
    assert await provider.chat(model="m", messages=[], params={}) == "ok"


# ---------------------------------------------------------------------------
# Layout parser page-limit guard
# ---------------------------------------------------------------------------


def _make_pdf(num_pages: int) -> bytes:
    """Minimal multi-page PDF built with PyMuPDF (same as test_docling_e2e)."""
    import fitz

    doc = fitz.open()
    for i in range(num_pages):
        doc.new_page().insert_text((72, 72), f"Page {i}", fontsize=11)
    return doc.tobytes()


class _FakeTextExtractor:
    def extract_page(self, img_np, chars, page_number, zoom):
        return [], 10.0

    @staticmethod
    def assign_columns(boxes):
        return boxes, 1

    @staticmethod
    def merge_text_horizontal(boxes, mean_heights):
        return boxes

    @staticmethod
    def merge_text_vertical(boxes, mean_heights, is_english=True):
        return boxes


class _FakeLayoutRecognizer:
    def __call__(self, images, ocr_boxes, scale_factor, thr, drop):
        return [], []


class _FakeTableRecognizer:
    def __call__(self, imgs, thr):
        return []


def _parser_with_stubs():
    """Real LayoutPdfParser with stubbed OCR/layout/table sub-components.

    __init__ pulls ONNX models, so we bypass it and wire the three
    sub-components the parse pipeline needs. This gives a real at-cap parse
    without heavyweight deps.
    """
    from atlas.ingest.pdf_parser import LayoutPdfParser

    parser = LayoutPdfParser.__new__(LayoutPdfParser)
    parser.text_extractor = _FakeTextExtractor()
    parser.layout_recognizer = _FakeLayoutRecognizer()
    parser.table_recognizer = _FakeTableRecognizer()
    return parser


def test_layout_parser_rejects_pdf_over_page_cap(monkeypatch) -> None:
    """Over-cap PDFs are refused with the same error Docling raises, pre-render."""
    from atlas.diagnostics import ErrorCode
    from atlas.ingest.docling_adapter import DoclingLimitsError
    from atlas.ingest.pdf_parser import LayoutPdfParser

    monkeypatch.setenv("ATLAS_PDF_MAX_PAGES", "3")

    # Bypassing __init__ skips ONNX; the guard raises before any sub-component
    # is touched, so a bare instance is enough.
    parser = LayoutPdfParser.__new__(LayoutPdfParser)

    with pytest.raises(DoclingLimitsError) as excinfo:
        parser(_make_pdf(4))

    assert excinfo.value.error_code == ErrorCode.DOC_PAGE_LIMIT_EXCEEDED


def test_layout_parser_accepts_pdf_at_page_cap(monkeypatch, tmp_path) -> None:
    """Under-cap PDFs pass the preflight and still parse end-to-end."""
    monkeypatch.setenv("ATLAS_PDF_MAX_PAGES", "3")

    result = _parser_with_stubs()(_make_pdf(2))

    assert result.page_count == 2
