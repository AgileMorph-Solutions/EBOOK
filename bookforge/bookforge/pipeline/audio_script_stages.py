"""Named stages for audio-script generation — single source of truth for progress."""
from __future__ import annotations

from typing import TypedDict


class AudioScriptStage:
    QUEUED = "queued"
    BRAINSTORMING = "brainstorming"
    REFINING_LIST = "refining_list"
    WRITING_ITEMS = "writing_items"
    GENERATING_AUDIO = "generating_audio"
    COMPLETE = "complete"
    FAILED = "failed"


class StageMeta(TypedDict):
    id: str
    label: str
    description: str
    weight: float


STAGES: list[StageMeta] = [
    {
        "id": AudioScriptStage.BRAINSTORMING,
        "label": "Brainstorm",
        "description": "Generate N distinct concept titles for the narration title.",
        "weight": 0.12,
    },
    {
        "id": AudioScriptStage.REFINING_LIST,
        "label": "Refine list",
        "description": "Deduplicate and reorder concepts for maximum retention.",
        "weight": 0.08,
    },
    {
        "id": AudioScriptStage.WRITING_ITEMS,
        "label": "Write items",
        "description": (
            "Write each item in the target word band (parallel); rewrite if out of band."
        ),
        "weight": 0.55,
    },
    {
        "id": AudioScriptStage.GENERATING_AUDIO,
        "label": "Generate audio",
        "description": "ElevenLabs TTS per item, then concatenate into one MP3.",
        "weight": 0.20,
    },
    {
        "id": AudioScriptStage.COMPLETE,
        "label": "Complete",
        "description": "Persist result and mark the job done.",
        "weight": 0.05,
    },
]

STAGE_IDS = frozenset(s["id"] for s in STAGES) | {
    AudioScriptStage.QUEUED,
    AudioScriptStage.FAILED,
}


def stages_payload() -> list[dict[str, str]]:
    """Public metadata for GET /audio-scripts/{id} so clients can render a tracker."""
    return [
        {"id": s["id"], "label": s["label"], "description": s["description"]}
        for s in STAGES
    ]
