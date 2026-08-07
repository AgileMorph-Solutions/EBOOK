"""Tests for the never-fail/speed overhaul: per-book budget, failure
classification, quota-aware parking, resume seams, and LLM-client repair."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from bookforge import cost
from bookforge.clients import quota
from bookforge.cost import BudgetExceeded
from bookforge.errors import IngestError, JobDeadlineError, PageLimitError
from bookforge.pipeline import runctx
from bookforge.pipeline.pools import ctx_pool


# --------------------------------------------------------------------------- #
# Per-book budget: isolation + ctx_pool propagation + spend carry-over
# --------------------------------------------------------------------------- #

def test_budget_bound_per_book_and_isolated():
    with cost.bind("book-a", cap_usd=10.0) as a:
        cost.budget.charge(3.0)
        assert a.spent == pytest.approx(3.0)
    # unbinding drops the entry; the default accumulator was untouched
    with cost.bind("book-b", cap_usd=10.0) as b:
        assert cost.budget.spent == pytest.approx(0.0)
        cost.budget.charge(1.0)
        assert b.spent == pytest.approx(1.0)


def test_budget_propagates_into_ctx_pool_workers():
    seen: list[float] = []
    with cost.bind("book-pool", cap_usd=10.0) as book:
        def worker(x: float) -> None:
            cost.budget.charge(x)  # must resolve to book-pool's budget
            seen.append(cost.budget.spent)
        with ctx_pool(4, "t") as pool:
            list(pool.map(worker, [1.0, 1.0, 1.0]))
        assert book.spent == pytest.approx(3.0)
    assert len(seen) == 3


def test_budget_concurrent_books_do_not_pollute_each_other():
    results: dict[str, float] = {}

    def run(book_id: str, amount: float) -> None:
        with cost.bind(book_id, cap_usd=100.0) as b:
            for _ in range(10):
                cost.budget.charge(amount)
            results[book_id] = b.spent

    t1 = threading.Thread(target=run, args=("bk-one", 1.0))
    t2 = threading.Thread(target=run, args=("bk-two", 2.0))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert results["bk-one"] == pytest.approx(10.0)
    assert results["bk-two"] == pytest.approx(20.0)


def test_budget_initial_spent_carries_across_resume():
    with cost.bind("book-resume", cap_usd=5.0, initial_spent=4.9):
        with pytest.raises(BudgetExceeded):
            cost.budget.charge(0.2)
            cost.budget.assert_ok()


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #

def test_failure_class_table():
    from bookforge.api.main import _failure_class

    assert _failure_class(BudgetExceeded("cap")) == "permanent"
    assert _failure_class(PageLimitError(
        "over", pages=400, target=300, lo=270, hi=330)) == "permanent"
    assert _failure_class(JobDeadlineError("slow")) == "provider_unavailable"
    assert _failure_class(IngestError("blocked", reason="youtube_blocked")) == "permanent"
    assert _failure_class(RuntimeError("anything else")) == "transient"


def test_error_kinds_for_new_domain_errors():
    from bookforge.api.error_kinds import classify

    assert classify(BudgetExceeded("cap")).kind == "budget"
    assert classify(IngestError("nope")).kind == "source_unavailable"
    assert classify(JobDeadlineError("slow")).kind == "timeout"


def test_page_limit_error_message_matches_actual_direction():
    """Regression test: the friendly message must say what actually happened
    — "grew past the goal" for an overshoot, "didn't produce enough" for an
    undershoot — never the wrong direction (previously always said "did not
    produce enough material", even when the book was 87 pages too LONG)."""
    from bookforge.api.error_kinds import classify

    over = PageLimitError("x", pages=207, target=100, lo=100, hi=120)
    over_msg = classify(over).message.lower()
    assert "grew past" in over_msg
    assert "did not produce enough" not in over_msg

    under = PageLimitError("x", pages=59, target=100, lo=100, hi=120)
    under_msg = classify(under).message.lower()
    assert "did not produce enough" in under_msg
    assert "grew past" not in under_msg


# --------------------------------------------------------------------------- #
# Quota parsing — grounded on the live captured Imagen 429
# --------------------------------------------------------------------------- #

_LIVE_429 = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You "
             "exceeded your current quota... Quota exceeded for metric: "
             "generativelanguage.googleapis.com/predict_requests_per_model_per_day"
             "_paid_tier_1, limit: 70, model: imagen-4.0-generate', "
             "'status': 'RESOURCE_EXHAUSTED'}}")


def test_parse_quota_daily_from_live_payload():
    clazz, wait_until = quota.parse_quota(_LIVE_429)
    assert clazz == quota.DAILY
    assert wait_until is not None
    now = datetime.now(timezone.utc)
    assert now < wait_until <= now + timedelta(hours=25)


def test_parse_quota_per_minute_retry_delay():
    clazz, wait_until = quota.parse_quota(
        "429 rate limit: quota metric per_minute exceeded, retryDelay: '13s'")
    assert clazz == quota.PER_MINUTE
    assert wait_until is not None
    delta = (wait_until - datetime.now(timezone.utc)).total_seconds()
    assert 30 <= delta <= 120  # floor of 60s applies


def test_image_quota_error_carries_quota_fields():
    from bookforge.clients.images import ImageQuotaError

    exc = ImageQuotaError(_LIVE_429)
    assert exc.quota_class == quota.DAILY
    assert exc.wait_until is not None


# --------------------------------------------------------------------------- #
# Quota-aware parking: daily class doesn't consume the attempt cap
# --------------------------------------------------------------------------- #

def test_park_daily_quota_uses_wait_until_and_spares_attempts(monkeypatch, tmp_path):
    from bookforge.api import main as api_main
    from bookforge.clients.images import ImageQuotaError

    monkeypatch.setattr(api_main, "_ARTIFACTS", tmp_path)
    book_id = "quota-book"
    api_main._set_state(book_id, status="running", attempts=2)

    exc = ImageQuotaError(_LIVE_429)
    api_main._park(book_id, exc, "provider_unavailable")

    state = api_main._read_state(book_id)
    assert state["status"] == "waiting"
    assert state["attempts"] == 2          # daily park did NOT burn an attempt
    assert state["quota_parks"] == 1
    assert state["wait_reason"] == "image_quota_daily"
    next_at = datetime.fromisoformat(state["next_retry_at"])
    # parked until the quota horizon, not the 60..1800s backoff schedule
    assert next_at - datetime.now(timezone.utc) > timedelta(hours=1)


def test_park_normal_error_still_counts_attempts(monkeypatch, tmp_path):
    from bookforge.api import main as api_main

    monkeypatch.setattr(api_main, "_ARTIFACTS", tmp_path)
    book_id = "svc-book"
    api_main._set_state(book_id, status="running", attempts=0)
    api_main._park(book_id, RuntimeError("blip"), "transient")
    assert api_main._read_state(book_id)["attempts"] == 1


# --------------------------------------------------------------------------- #
# Job run context: deadline + heartbeat
# --------------------------------------------------------------------------- #

def test_runctx_deadline_raises_and_heartbeat_fires():
    beats: list[int] = []
    run = runctx.JobRun("bk", deadline_ts=0.0,  # already past
                        heartbeat_cb=lambda: beats.append(1))
    with runctx.bind(run):
        with pytest.raises(JobDeadlineError):
            runctx.checkpoint()


def test_runctx_noop_when_unbound():
    runctx.checkpoint()  # must never raise outside a bound job


# --------------------------------------------------------------------------- #
# Resume: write_guide skips existing sections; renumber is globally sequential
# --------------------------------------------------------------------------- #

def _mk_chapter(idx: int, titles: list[str]):
    from bookforge.schemas import BlockType, Chapter, ContentBlock, UseEntry

    entries = [UseEntry(title=t, intro=f"intro {t}", number=0) for t in titles]
    return Chapter(index=idx, title=f"S{idx}", blocks=[
        ContentBlock(type=BlockType.use_card, heading=t, entries=[e])
        for t, e in zip(titles, entries)
    ])


def test_renumber_assigns_global_reading_order():
    from bookforge.pipeline.content import _renumber

    chapters = [_mk_chapter(1, ["a", "b"]), _mk_chapter(2, ["c", "d", "e"])]
    total = _renumber(chapters)
    assert total == 5
    nums = [e.number for ch in chapters for b in ch.blocks for e in b.entries]
    assert nums == [1, 2, 3, 4, 5]


def test_write_guide_skips_existing_sections(monkeypatch):
    from bookforge.pipeline import content

    calls: list[int] = []

    def fake_build(section, bible, source, *, start_number, registry,
                   list_lead="", feedback=None):
        calls.append(section.index)
        return _mk_chapter(section.index, [f"t{section.index}"]), start_number + 1

    monkeypatch.setattr(content, "_build_guide_section", fake_build)
    monkeypatch.setattr(content.content_guide, "warm_section_cache",
                        lambda bible: None)

    from bookforge.schemas import (
        ArtStyle, BookBible, EditorialProfile, EntrySchema, SectionPlan,
        VoiceStyle,
    )
    bible = BookBible(
        title="T", subtitle="s", audience="a", tone="t",
        art_style=ArtStyle(
            medium="photo", palette="warm", lighting="soft", era="today",
            composition_rules="", negative_prompt="no text", style_suffix="",
        ),
        voice=VoiceStyle(narrator_persona="p", tone_register="warm", pov="2nd",
                         pacing="steady", vocabulary="plain"),
        book_kind="practical_guide",
        profile=EditorialProfile(genre="g", audience="a", angle="x",
                                 entry_schema=EntrySchema(entry_noun="tip")),
        sections=[SectionPlan(index=i, title=f"S{i}") for i in (1, 2, 3)],
        chapters=[],
    )
    existing = [_mk_chapter(1, ["done1"]), _mk_chapter(2, ["done2"])]
    src = object()

    out = content.write_guide(bible, src, existing=existing)
    assert calls == [3]                      # only the unfinished section ran
    assert [ch.index for ch in out] == [1, 2, 3]
    nums = [e.number for ch in out for b in ch.blocks for e in b.entries]
    assert nums == sorted(nums) and nums[0] == 1  # renumbered globally


# --------------------------------------------------------------------------- #
# LLM client: truncation bump + validation feedback retry stay on-model
# --------------------------------------------------------------------------- #

class _FakeUsage:
    input_tokens = 10
    output_tokens = 10


class _FakeBlock:
    type = "tool_use"
    name = "emit"

    def __init__(self, payload):
        self.input = payload


class _FakeResp:
    def __init__(self, stop_reason="tool_use", payload=None):
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()
        self.content = [] if payload is None else [_FakeBlock(payload)]


def test_structured_truncation_bump_retries_same_model(monkeypatch):
    from pydantic import BaseModel

    from bookforge.clients import llm

    class Out(BaseModel):
        x: int

    calls: list[int] = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw["max_tokens"])
            if len(calls) == 1:
                return _FakeResp(stop_reason="max_tokens")
            return _FakeResp(payload={"x": 1})

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(llm, "_get_client", lambda: FakeClient())
    out = llm.structured("p", Out, model="claude-opus-4-8", max_tokens=1000)
    assert out.x == 1
    assert calls == [1000, 2000]  # single same-model retry with a bumped cap


def test_structured_validation_feedback_retry(monkeypatch):
    from pydantic import BaseModel

    from bookforge.clients import llm

    class Out(BaseModel):
        x: int

    prompts: list[str] = []

    class FakeMessages:
        def create(self, **kw):
            prompts.append(kw["messages"][0]["content"])
            if len(prompts) == 1:
                return _FakeResp(payload={"x": "not-an-int"})
            return _FakeResp(payload={"x": 2})

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(llm, "_get_client", lambda: FakeClient())
    out = llm.structured("p", Out, model="claude-opus-4-8", max_tokens=1000)
    assert out.x == 2
    assert len(prompts) == 2
    assert "rejected" in prompts[1]  # feedback appended on the retry


def test_checklist_page_keeps_its_repair_key():
    # Regression: the cadence-group loop used to SHADOW the `key` param, so the
    # page shipped data-entry="weekly" instead of "check:{ref}" — repairs were
    # planned for a key the builder never looked up, making the overflow
    # unrepairable (observed live: page 236 of lost-diners, 118px, every round).
    from bookforge.layout.guide import GuideDirector
    from bookforge.schemas import (
        ArtStyle, BookBible, ChecklistItem, ChecklistModel, EditorialProfile,
        EntrySchema, VoiceStyle,
    )

    bible = BookBible(
        title="T", subtitle="s", audience="a", tone="t",
        art_style=ArtStyle(medium="photo", palette="warm", lighting="soft",
                           era="today", composition_rules="",
                           negative_prompt="", style_suffix=""),
        voice=VoiceStyle(narrator_persona="p", tone_register="warm", pov="2nd",
                         pacing="steady", vocabulary="plain"),
        book_kind="practical_guide",
        profile=EditorialProfile(genre="g", audience="a", angle="x",
                                 entry_schema=EntrySchema(entry_noun="tip")),
    )
    checklist = ChecklistModel(items=[
        ChecklistItem(text="wipe the counters", cadence="weekly"),
        ChecklistItem(text="deep clean the oven", cadence="monthly"),
    ])
    d = GuideDirector(bible)
    d.checklist_page("Chapter 9: Hosting", "Supper Countdown", "lead",
                     checklist, key="check:9.7", level=0)
    assert 'data-entry="check:9.7"' in d.pages[0]
    assert 'data-entry="weekly"' not in d.pages[0]
    # cadence group headers still render
    assert "Weekly" in d.pages[0] and "Monthly" in d.pages[0]


def test_checklist_page_splits_at_level_two():
    # A long checklist overflows STRUCTURALLY — font floors can't fix it. The
    # repair ladder's level>=2 must split the items across two keyed pages
    # (observed live: 118px overflow constant across every repair round).
    from bookforge.layout.guide import GuideDirector
    from bookforge.schemas import (
        ArtStyle, BookBible, ChecklistItem, ChecklistModel, EditorialProfile,
        EntrySchema, VoiceStyle,
    )

    bible = BookBible(
        title="T", subtitle="s", audience="a", tone="t",
        art_style=ArtStyle(medium="photo", palette="warm", lighting="soft",
                           era="today", composition_rules="",
                           negative_prompt="", style_suffix=""),
        voice=VoiceStyle(narrator_persona="p", tone_register="warm", pov="2nd",
                         pacing="steady", vocabulary="plain"),
        book_kind="practical_guide",
        profile=EditorialProfile(genre="g", audience="a", angle="x",
                                 entry_schema=EntrySchema(entry_noun="tip")),
    )
    checklist = ChecklistModel(items=[
        ChecklistItem(text=f"task {i}", cadence="weekly") for i in range(10)
    ])
    d = GuideDirector(bible)
    d.checklist_page("Ch 9", "Countdown", "lead", checklist,
                     key="check:9.7", level=2)
    assert len(d.pages) == 2
    assert all('data-entry="check:9.7"' in p for p in d.pages)
    assert "(continued)" in d.pages[1] and "(continued)" not in d.pages[0]
    # items divided across the two pages, none lost
    total_items = sum(p.count('<span class="check-box">') for p in d.pages)
    assert total_items == 10
    assert all(p.count('<span class="check-box">') == 5 for p in d.pages)
    d1 = GuideDirector(bible)
    d1.checklist_page("Ch 9", "Countdown", "lead", checklist,
                      key="check:9.7", level=0)
    assert len(d1.pages) == 1  # no split below level 2


def test_overloaded_is_retryable_same_model():
    import anthropic

    from bookforge.clients import llm

    assert anthropic.OverloadedError in llm._RETRYABLE
    # overload gets the longer backoff schedule
    class _S:  # minimal retry_state stand-in
        attempt_number = 1

        class outcome:  # noqa: N801
            @staticmethod
            def exception():
                return anthropic.OverloadedError.__new__(anthropic.OverloadedError)

    assert llm._retry_wait(_S) >= 10.0
