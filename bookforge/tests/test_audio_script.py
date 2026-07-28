"""Audio script pipeline unit tests."""
from __future__ import annotations

from bookforge.pipeline.audio_script import (
    body_from_paragraphs,
    duration_word_budget,
    format_transcript,
    is_generic_audio_title,
    parse_title_duration_minutes,
    resolve_effective_word_count,
    resolve_item_count,
    resolve_overall_word_limit,
    resolve_paragraph_targets,
    resolve_word_band,
    to_sentence_case,
    word_count,
)
from bookforge.pipeline.audio_script import _SOP_WORD_TARGET
from bookforge.pipeline.audio_script_prompts import (
    brainstorm_user,
    category_voice,
    feedback_block,
    item_rewrite_user,
    item_system,
    item_user,
    refine_user,
)
from bookforge.pipeline.audio_script_stages import (
    AudioScriptStage,
    stages_payload,
)
from bookforge.pipeline.audio_tts import speakable_text
from bookforge.schemas import AudioScriptItem


def test_word_count():
    assert word_count("one two three") == 3


def test_body_from_paragraphs():
    body = body_from_paragraphs(["First para.", "Second para."])
    assert body == "First para.\n\nSecond para."


def test_format_transcript():
    items = [
        AudioScriptItem(number=1, title="Map-Territory Distinction", body="Para one.\n\nPara two.", word_count=5),
        AudioScriptItem(number=2, title="Identity Protection", body="Another body.", word_count=2),
    ]
    text = format_transcript(items)
    assert "Map-Territory Distinction\n\nPara one." in text
    assert "\n\nIdentity Protection\n\nAnother body." in text


def test_resolve_item_count_from_title():
    title = "100 Dark Confidence Secrets That Make People Respect You Instantly"
    assert resolve_item_count(title) == 100
    assert resolve_item_count(title, item_limit=5) == 5


def test_resolve_item_count_from_level_range_title():
    title = "Level 1 to 100 Social Intelligence Secrets That Get Darker as You Go"
    assert resolve_item_count(title) == 100
    overall = resolve_overall_word_limit(title)
    assert overall is None
    assert resolve_effective_word_count() is None


def test_resolve_item_count_limit_overrides_title_number():
    title = "3 Hours of Emotional Control Secrets to Fall Asleep To"
    assert resolve_item_count(title) > 3
    assert resolve_item_count(title, item_limit=10) == 10


def test_parse_title_duration_minutes():
    assert parse_title_duration_minutes(
        "3 Hours of Emotional Control Secrets to Fall Asleep To"
    ) == 180
    assert parse_title_duration_minutes("45 Minutes of Calm Breathwork") == 45
    assert parse_title_duration_minutes("Three Hours of Stoic Wisdom") == 180
    assert parse_title_duration_minutes("100 Dark Psychology Tricks") is None
    assert parse_title_duration_minutes(
        "1.5 Hours of Dark Psychology Tricks to Fall Asleep To"
    ) == 90
    assert parse_title_duration_minutes("2.25 hours of calm rain sounds") == 135
    assert parse_title_duration_minutes("90.5 minutes of focus") == 90


def test_decimal_hour_title_sizes_near_runtime():
    title = "1.5 Hours of Dark Psychology Tricks to Fall Asleep To"
    assert resolve_item_count(title) != 5
    overall = resolve_overall_word_limit(title)
    items = resolve_item_count(title, overall_word_limit=overall)
    assert overall == duration_word_budget(90)
    est_minutes = (items * _SOP_WORD_TARGET) / 130
    assert 90 <= est_minutes <= 110
    assert overall < duration_word_budget(180)


def test_duration_title_sizes_near_runtime():
    title = "3 Hours of Emotional Control Secrets to Fall Asleep To"
    items = resolve_item_count(title)
    overall = resolve_overall_word_limit(title)
    assert resolve_effective_word_count() is None
    assert overall == duration_word_budget(180)
    est_minutes = (items * _SOP_WORD_TARGET) / 130
    assert est_minutes >= 180
    assert est_minutes <= 220


def test_duration_plus_count_keeps_count_and_duration_budget():
    title = "100 Secrets for 3 Hours of Deep Focus"
    assert resolve_item_count(title) == 100
    overall = resolve_overall_word_limit(title)
    assert overall == duration_word_budget(180)
    assert resolve_effective_word_count() is None


def test_is_generic_audio_title():
    assert is_generic_audio_title("How To Think Clearly") is True
    assert is_generic_audio_title("100 Dark Psychology Tricks") is False
    assert is_generic_audio_title("3 Hours of Calm") is False
    assert is_generic_audio_title("3 ways to die single") is False


def test_resolve_overall_word_limit_generic():
    assert resolve_overall_word_limit("How To Think Clearly") == 1000
    assert resolve_overall_word_limit("How To Think Clearly", 500) == 500


def test_resolve_item_count_generic():
    title = "How To Think Clearly"
    overall = resolve_overall_word_limit(title)
    assert overall == 1000
    assert resolve_item_count(title, overall_word_limit=overall) == 3


def test_generic_sizing_uses_sop_band():
    title = "How to die single"
    overall = resolve_overall_word_limit(title)
    items = resolve_item_count(title, overall_word_limit=overall)
    assert overall == 1000
    assert items == 3
    assert resolve_effective_word_count() is None


def test_resolve_item_count_default():
    assert resolve_item_count("How To Think Clearly", overall_word_limit=1000) == 3


def test_resolve_word_band_default_sop():
    word_min, word_max, target = resolve_word_band(None)
    assert (word_min, word_max, target) == (265, 270, 267)


def test_resolve_word_band_custom():
    word_min, word_max, target = resolve_word_band(200)
    assert (word_min, word_max, target) == (195, 205, 200)


def test_resolve_effective_word_count_default():
    assert resolve_effective_word_count() is None
    assert resolve_effective_word_count(None) is None


def test_resolve_effective_word_count_explicit():
    assert resolve_effective_word_count(200) == 200
    assert resolve_effective_word_count(270) == 270


def test_resolve_paragraph_targets_unset():
    assert resolve_paragraph_targets(10) is None
    assert resolve_paragraph_targets(10, None) is None


def test_resolve_paragraph_targets_distribution():
    assert resolve_paragraph_targets(3, 10) == [4, 3, 3]
    assert resolve_paragraph_targets(3, 9) == [3, 3, 3]
    assert resolve_paragraph_targets(5, 5) == [1, 1, 1, 1, 1]
    assert sum(resolve_paragraph_targets(100, 250)) == 250


def test_category_voice_known_and_free_text():
    assert "cognitive bias" in category_voice("psychology")
    assert "cognitive bias" in category_voice("Psychology")
    assert category_voice("true crime") == "true crime"
    assert category_voice("  stoicism  ") == "stoicism"
    assert "self-improvement" in category_voice("")


def test_to_sentence_case():
    assert to_sentence_case("Foot In The Door Escalation") == "Foot in the door escalation"
    assert to_sentence_case("  identity Protection  ") == "Identity protection"
    assert to_sentence_case("") == ""
    assert to_sentence_case("map-territory distinction") == "Map-territory distinction"


def test_duration_context_in_prompts():
    from bookforge.pipeline.audio_script_prompts import (
        brainstorm_user,
        duration_context_block,
        item_user,
    )

    ctx = duration_context_block(
        "3 Hours of Dark Psychology",
        target_duration_minutes=180,
        overall_word_limit=25740,
        item_count=96,
    )
    assert "3 hours" in ctx.lower()
    assert "RUNTIME CONTRACT" in ctx
    assert "25,740" in ctx

    brainstorm = brainstorm_user(
        "3 Hours of Dark Psychology", 96,
        target_duration_minutes=180,
        overall_word_limit=25740,
    )
    assert "RUNTIME CONTRACT" in brainstorm

    item = item_user(
        "3 Hours of Dark Psychology", "Foot in the door", 1, 96,
        duration_context=ctx,
    )
    assert "top of the band" in item
    assert "RUNTIME CONTRACT" in item


def test_item_system_bans_stock_phrases():
    system = item_system("psychology", word_min=195, word_max=205)
    assert "Opening hook:" not in system
    assert "4–5 paragraphs" not in system
    assert "natural paragraph breaks" in system
    assert "BANNED stock phrases" in system
    assert "This becomes powerful because" in system
    assert "Once you understand" in system
    assert "late-night" in system.lower()


def test_item_prompts_use_word_band():
    user = item_user("Title", "Concept", 1, 2, word_min=195, word_max=205)
    assert "195 and 205 words" in user
    rewrite = item_rewrite_user(
        "Title", "Concept", ["Short draft."], 2, word_min=195, word_max=205,
    )
    assert "195–205 words" in rewrite
    assert "current: 2 words" in rewrite
    assert "banned stock phrases" in rewrite.lower() or "stock phrases" in rewrite.lower()


def test_stages_payload_order():
    ids = [s["id"] for s in stages_payload()]
    assert ids == [
        AudioScriptStage.BRAINSTORMING,
        AudioScriptStage.REFINING_LIST,
        AudioScriptStage.WRITING_ITEMS,
        AudioScriptStage.GENERATING_AUDIO,
        AudioScriptStage.COMPLETE,
    ]


def test_speakable_text():
    item = AudioScriptItem(number=1, title="Identity Protection", body="Body here.", word_count=2)
    assert speakable_text(item) == "Identity Protection. Body here."


def test_feedback_block_empty_when_none():
    assert feedback_block(None) == ""
    assert feedback_block("") == ""
    assert feedback_block("   ") == ""


def test_feedback_block_includes_notes():
    block = feedback_block("Make openings more scene-based.")
    assert "REVISION FEEDBACK" in block
    assert "Make openings more scene-based." in block


def test_prompts_append_feedback_when_set():
    note = "Drop abstract definitions."
    brainstorm = brainstorm_user("My Title", 3, feedback=note)
    refine = refine_user("My Title", ["One", "Two", "Three"], 3, feedback=note)
    item = item_user("My Title", "Concept", 1, 3, feedback=note)
    rewrite = item_rewrite_user(
        "My Title", "Concept", ["Draft."], 1, feedback=note,
    )
    for prompt in (brainstorm, refine, item, rewrite):
        assert "REVISION FEEDBACK" in prompt
        assert note in prompt


def test_prompts_omit_feedback_when_empty():
    brainstorm = brainstorm_user("My Title", 3)
    refine = refine_user("My Title", ["One"], 1)
    item = item_user("My Title", "Concept", 1, 1)
    assert "REVISION FEEDBACK" not in brainstorm
    assert "REVISION FEEDBACK" not in refine
    assert "REVISION FEEDBACK" not in item
