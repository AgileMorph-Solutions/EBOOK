"""Audio script pipeline unit tests."""
from __future__ import annotations

from bookforge.pipeline.audio_script import (
    body_from_paragraphs,
    estimate_duration_fit,
    format_transcript,
    is_generic_audio_title,
    parse_title_duration_minutes,
    resolve_effective_word_count,
    resolve_item_count,
    resolve_overall_word_limit,
    resolve_word_band,
    to_sentence_case,
    word_count,
)
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


def test_resolve_item_count_limit_overrides_title_number():
    title = "3 Hours of Emotional Control Secrets to Fall Asleep To"
    # Without item_limit, duration wins — not the bare "3".
    assert resolve_item_count(title) > 3
    assert resolve_item_count(title, item_limit=10) == 10


def test_parse_title_duration_minutes():
    assert parse_title_duration_minutes(
        "3 Hours of Emotional Control Secrets to Fall Asleep To"
    ) == 180
    assert parse_title_duration_minutes("45 Minutes of Calm Breathwork") == 45
    assert parse_title_duration_minutes("Three Hours of Stoic Wisdom") == 180
    assert parse_title_duration_minutes("100 Dark Psychology Tricks") is None


def test_duration_title_sizes_near_runtime():
    title = "3 Hours of Emotional Control Secrets to Fall Asleep To"
    items = resolve_item_count(title)
    overall = resolve_overall_word_limit(title)
    per_item = resolve_effective_word_count(items, overall_word_limit=overall)
    assert overall == 180 * 130
    assert per_item is not None
    # ~3 hours at 130 wpm (±10%)
    est_minutes = (items * per_item) / 130
    assert 162 <= est_minutes <= 198


def test_duration_plus_count_keeps_count_and_duration_budget():
    title = "100 Secrets for 3 Hours of Deep Focus"
    assert resolve_item_count(title) == 100
    overall = resolve_overall_word_limit(title)
    assert overall == 180 * 130
    per_item = resolve_effective_word_count(100, overall_word_limit=overall)
    assert per_item == 234  # 23400 // 100


def test_estimate_duration_fit_conflict():
    fit = estimate_duration_fit("2 minutes of reading", 200)
    assert fit["conflict"] is True
    assert fit["title_duration_minutes"] == 2
    assert fit["words_needed"] == 260
    assert fit["overall_word_limit"] == 200
    assert fit["estimated_minutes_from_cap"] == 200 / 130
    assert "260" in fit["reason"]
    assert "200" in fit["reason"]


def test_estimate_duration_fit_no_conflict_when_cap_sufficient():
    fit = estimate_duration_fit("2 minutes of reading", 300)
    assert fit["conflict"] is False
    assert fit["reason"] == ""


def test_estimate_duration_fit_no_conflict_without_duration():
    fit = estimate_duration_fit("100 Dark Psychology Tricks", 200)
    assert fit["conflict"] is False


def test_estimate_duration_fit_no_conflict_without_cap():
    fit = estimate_duration_fit("2 minutes of reading", None)
    assert fit["conflict"] is False


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
    assert resolve_item_count(title, overall_word_limit=overall) == 4  # round(1000/267)


def test_generic_sizing_end_to_end():
    title = "How to die single"
    overall = resolve_overall_word_limit(title)
    items = resolve_item_count(title, overall_word_limit=overall)
    per_item = resolve_effective_word_count(items, overall_word_limit=overall)
    assert overall == 1000
    assert items == 4
    assert per_item == 250
    assert items * per_item == 1000


def test_resolve_item_count_default():
    assert resolve_item_count("How To Think Clearly", overall_word_limit=1000) == 4


def test_resolve_word_band_default_sop():
    word_min, word_max, target = resolve_word_band(None)
    assert (word_min, word_max, target) == (265, 270, 267)


def test_resolve_word_band_custom():
    word_min, word_max, target = resolve_word_band(200)
    assert (word_min, word_max, target) == (195, 205, 200)


def test_resolve_effective_word_count_neither():
    assert resolve_effective_word_count(10) is None
    assert resolve_effective_word_count(10, word_count=None, overall_word_limit=None) is None


def test_resolve_effective_word_count_overall_only():
    # 3000 / 10 = 300
    assert resolve_effective_word_count(10, overall_word_limit=3000) == 300
    # clamps to per-item max 500
    assert resolve_effective_word_count(2, overall_word_limit=5000) == 500
    # clamps to per-item min 100
    assert resolve_effective_word_count(50, overall_word_limit=1000) == 100


def test_resolve_effective_word_count_word_count_only():
    assert resolve_effective_word_count(10, word_count=200) == 200


def test_resolve_effective_word_count_overall_clamps_when_both_exceed():
    # 270 * 20 = 5400 > 2000 → overall wins → 2000 // 20 = 100
    assert (
        resolve_effective_word_count(20, word_count=270, overall_word_limit=2000)
        == 100
    )


def test_resolve_effective_word_count_word_count_wins_when_under_overall():
    # 200 * 5 = 1000 < 3000 → keep explicit per-item
    assert (
        resolve_effective_word_count(5, word_count=200, overall_word_limit=3000)
        == 200
    )


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


def test_item_system_bans_stock_phrases():
    system = item_system("psychology", word_min=195, word_max=205)
    assert "Opening hook:" not in system
    assert "4–5 paragraphs" not in system
    assert "natural paragraph breaks" in system
    assert "BANNED stock phrases" in system
    assert "This becomes powerful because" in system  # listed as banned
    assert "Once you understand" in system  # listed as banned
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
