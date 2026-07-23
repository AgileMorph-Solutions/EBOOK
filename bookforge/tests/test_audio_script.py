"""Audio script pipeline unit tests."""
from __future__ import annotations

from bookforge.pipeline.audio_script import (
    body_from_paragraphs,
    format_transcript,
    resolve_item_count,
    resolve_word_band,
    to_sentence_case,
    word_count,
)
from bookforge.pipeline.audio_script_prompts import (
    item_rewrite_user,
    item_system,
    item_user,
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
    # "3 Hours…" would parse as 3; explicit item_limit wins.
    title = "3 Hours of Emotional Control Secrets to Fall Asleep To"
    assert resolve_item_count(title) == 3
    assert resolve_item_count(title, item_limit=10) == 10


def test_resolve_item_count_default():
    assert resolve_item_count("How To Think Clearly") >= 1


def test_resolve_word_band_default_sop():
    word_min, word_max, target = resolve_word_band(None)
    assert (word_min, word_max, target) == (265, 270, 267)


def test_resolve_word_band_custom():
    word_min, word_max, target = resolve_word_band(200)
    assert (word_min, word_max, target) == (195, 205, 200)


def test_to_sentence_case():
    assert to_sentence_case("Foot In The Door Escalation") == "Foot in the door escalation"
    assert to_sentence_case("  identity Protection  ") == "Identity protection"
    assert to_sentence_case("") == ""
    assert to_sentence_case("map-territory distinction") == "Map-territory distinction"


def test_item_system_bans_stock_phrases():
    system = item_system("psychology", word_min=195, word_max=205)
    assert "Opening hook:" not in system
    assert "STRUCTURE (4–5 paragraphs" not in system
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
