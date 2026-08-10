"""plan_source/plan_bible/plan_book: checkpoint-hit-or-build semantics.

These are the functions the background job's cold-start path
(orchestrator._run_book_impl) uses as its own first stage — real planning
(ingest + build_bible) runs there, not synchronously in the API request
thread, so a genuine PlanInfeasibleError surfaces as a failed job rather than
blocking POST /books. Checkpointing means a partial plan already on disk
(e.g. from a resumed/retried job) is reused instead of recomputed.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("BOOKFORGE_LOCAL_STORE", "/tmp/bookforge-test-artifacts")

from bookforge.pipeline import orchestrator  # noqa: E402


def test_plan_source_builds_and_checkpoints_on_miss():
    fake_source = MagicMock()
    with patch("bookforge.pipeline.orchestrator.checkpoints.load_source", return_value=None):
        with patch("bookforge.pipeline.orchestrator.ingest.ingest", return_value=fake_source) as mock_ingest:
            with patch("bookforge.pipeline.orchestrator.checkpoints.save_source") as mock_save:
                result = orchestrator.plan_source(
                    "book-1", "Some Topic", text="source text", youtube_url=None,
                )
    assert result is fake_source
    mock_ingest.assert_called_once()
    mock_save.assert_called_once_with("book-1", fake_source)


def test_plan_source_reuses_checkpoint_without_reingesting():
    fake_source = MagicMock(passages=[])
    with patch("bookforge.pipeline.orchestrator.checkpoints.load_source", return_value=fake_source):
        with patch("bookforge.pipeline.orchestrator.ingest.ingest") as mock_ingest:
            result = orchestrator.plan_source(
                "book-1", "Some Topic", text="source text", youtube_url=None,
            )
    assert result is fake_source
    mock_ingest.assert_not_called()


def test_plan_bible_builds_and_checkpoints_on_miss():
    fake_source = MagicMock()
    fake_bible = MagicMock()
    with patch("bookforge.pipeline.orchestrator.checkpoints.load_bible", return_value=None):
        with patch(
            "bookforge.pipeline.orchestrator.bible_stage.build_bible",
            return_value=fake_bible,
        ) as mock_build:
            with patch("bookforge.pipeline.orchestrator.checkpoints.save_bible") as mock_save:
                result = orchestrator.plan_bible(
                    "book-1", fake_source, target_pages=100,
                )
    assert result is fake_bible
    mock_build.assert_called_once()
    mock_save.assert_called_once_with("book-1", fake_bible)


def test_plan_bible_reuses_checkpoint_without_rebuilding():
    """The core guarantee behind the sync pre-flight gate costing nothing
    extra: once checkpointed (by the gate), the background job's own call to
    plan_bible must NOT pay for build_bible again."""
    fake_source = MagicMock()
    fake_bible = MagicMock()
    with patch("bookforge.pipeline.orchestrator.checkpoints.load_bible", return_value=fake_bible):
        with patch("bookforge.pipeline.orchestrator.bible_stage.build_bible") as mock_build:
            result = orchestrator.plan_bible(
                "book-1", fake_source, target_pages=100,
            )
    assert result is fake_bible
    mock_build.assert_not_called()


def test_plan_book_propagates_infeasible_error_before_any_checkpoint_write():
    from bookforge.errors import PlanInfeasibleError

    with patch("bookforge.pipeline.orchestrator.checkpoints.load_source", return_value=None):
        with patch("bookforge.pipeline.orchestrator.ingest.ingest", return_value=MagicMock()):
            with patch("bookforge.pipeline.orchestrator.checkpoints.save_source"):
                with patch("bookforge.pipeline.orchestrator.checkpoints.load_bible", return_value=None):
                    with patch(
                        "bookforge.pipeline.orchestrator.bible_stage.build_bible",
                        side_effect=PlanInfeasibleError("plan estimates 59 pages but need at least 100"),
                    ):
                        with patch("bookforge.pipeline.orchestrator.checkpoints.save_bible") as mock_save_bible:
                            try:
                                orchestrator.plan_book(
                                    "book-1", "Some Topic", text="source text",
                                    youtube_url=None, target_pages=100,
                                )
                                raised = False
                            except PlanInfeasibleError:
                                raised = True
    assert raised
    mock_save_bible.assert_not_called()
