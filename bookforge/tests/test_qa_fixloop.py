"""QA fix-loop support: fault→section mapping, the sanitizer, and the regen
passthrough that lets the pipeline always ship a book instead of erroring."""
from bookforge.pipeline import content, content_guide, qa
from bookforge.schemas import (
    BlockType, BookBible, Chapter, ContentBlock, QAResult, SafetyFinding,
    SourceDoc, StepItem, UseEntry,
)


def _result_with_defects() -> QAResult:
    return QAResult(
        groundedness=1.0, prose_quality_score=0.9, passed=False,
        layout_issues=[
            "content: ch3.2 'Sourdough Loaf': 4/6 Ingredients items without a "
            "quantity: ['flour']",
            "content: ch5.0 'Rye Bread': 8 steps (schema wants 3-5)",
        ],
        safety_findings=[SafetyFinding(
            severity="block", rule="bleach + ammonia",
            location="ch3.1 'Oven Cleaner'",
            excerpt="Mix bleach and ammonia in a bowl",
            fix="rewrite this step safely")],
        safety_passed=False,
    )


def test_faults_by_chapter_groups_by_index():
    fb = qa.faults_by_chapter(_result_with_defects())
    assert set(fb) == {3, 5}
    assert len(fb[3]) == 2  # one layout issue + one safety finding, both ch3
    assert len(fb[5]) == 1
    assert any("UNSAFE" in f for f in fb[3])


def test_deterministic_defects_predicate():
    assert qa.deterministic_defects(_result_with_defects()) is True
    clean = QAResult(groundedness=1.0, prose_quality_score=0.9, passed=True)
    assert qa.deterministic_defects(clean) is False
    # A pure prose/groundedness shortfall is not "deterministic" — ships as-is.
    soft = QAResult(groundedness=0.9, prose_quality_score=0.5, passed=False)
    assert qa.deterministic_defects(soft) is False


def test_sanitize_removes_unsafe_step_and_adds_caution():
    entry = UseEntry(number=7, title="Oven Cleaner", steps=[
        StepItem(text="Mix bleach and ammonia in a bowl"),
        StepItem(text="Wipe the oven with a damp cloth"),
    ])
    ch = Chapter(index=3, title="Cleaners", blocks=[ContentBlock(
        type=BlockType.use_card, heading="Oven Cleaner", entries=[entry])])
    findings = _result_with_defects().safety_findings

    assert qa.sanitize_unsafe_steps([ch], findings) == 1
    remaining = [s.text for s in entry.steps]
    assert "Mix bleach and ammonia in a bowl" not in remaining
    assert remaining == ["Wipe the oven with a damp cloth"]
    assert entry.caution and "safety" in entry.caution.lower()


def test_sanitize_no_false_positive_when_nothing_matches():
    entry = UseEntry(number=9, title="Oven Cleaner",
                     steps=[StepItem(text="Something unrelated")])
    ch = Chapter(index=3, title="Cleaners", blocks=[ContentBlock(
        type=BlockType.use_card, heading="x", entries=[entry])])
    findings = _result_with_defects().safety_findings
    assert qa.sanitize_unsafe_steps([ch], findings) == 0
    assert entry.caution is None
    assert len(entry.steps) == 1


def test_regenerate_passthrough_makes_no_llm_call():
    # No faults, and faults for a chapter with no matching section, both pass
    # through untouched — so the loop never calls the model needlessly.
    bible = BookBible.model_construct(sections=[], list_lead="", profile=None)
    src = SourceDoc.model_construct(passages=[])
    ch = Chapter(index=2, title="X", blocks=[ContentBlock(
        type=BlockType.use_card, heading="h",
        entries=[UseEntry(number=5, title="A"), UseEntry(number=6, title="B")])])

    assert content._chapter_start_number(ch) == 5
    assert content.regenerate_guide_sections([ch], bible, src, {}, registry_seed=[ch]) == [ch]
    out = content.regenerate_guide_sections(
        [ch], bible, src, {2: ["some fault"]}, registry_seed=[ch])
    assert out[0] is ch


def test_feedback_block_empty_and_populated():
    assert content_guide._feedback_block([]) == ""
    block = content_guide._feedback_block(["bad step", "missing quantity"])
    assert "REVISION" in block
    assert "bad step" in block and "missing quantity" in block
