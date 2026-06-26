"""Stage 0 — ingest & normalize.

Accepts pasted text or a YouTube URL. For YouTube it pulls captions first
(cheap, exact) and falls back to ASR only when no transcript exists. The output
is a SourceDoc with stable, chunked passages that everything downstream cites.
"""
from __future__ import annotations

import re

import structlog

from bookforge.schemas import Passage, SourceDoc

log = structlog.get_logger(__name__)

_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})")


def _chunk(text: str, target_chars: int = 1200) -> list[Passage]:
    """Split into passages on paragraph/sentence boundaries with stable ids."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    passages: list[Passage] = []
    buf = ""
    for para in paras:
        if len(buf) + len(para) > target_chars and buf:
            passages.append(Passage(id=f"p{len(passages):04d}", text=buf.strip()))
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        passages.append(Passage(id=f"p{len(passages):04d}", text=buf.strip()))
    return passages


def _fetch_youtube(url: str) -> str:
    vid = _YT_RE.search(url)
    if not vid:
        raise ValueError(f"Not a recognizable YouTube URL: {url}")
    video_id = vid.group(1)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        parts = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(p["text"] for p in parts)
    except Exception as exc:  # no captions -> ASR fallback
        log.warning("no_captions_falling_back_to_asr", err=str(exc))
        return _asr_youtube(video_id)


def _asr_youtube(video_id: str) -> str:
    """Download audio with yt-dlp and transcribe with faster-whisper."""
    import tempfile

    import yt_dlp
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/audio.%(ext)s"
        opts = {"format": "bestaudio", "outtmpl": out, "quiet": True,
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3"}]}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(f"{tmp}/audio.mp3")
        return " ".join(s.text for s in segments)


def ingest(topic: str, *, text: str | None = None, youtube_url: str | None = None) -> SourceDoc:
    if youtube_url:
        raw = _fetch_youtube(youtube_url)
        origin, ref = "youtube", youtube_url
    elif text:
        raw, origin, ref = text, "text", None
    else:
        raise ValueError("Provide either text or youtube_url")

    raw = re.sub(r"[ \t]+", " ", raw).strip()
    passages = _chunk(raw)
    log.info("ingested", topic=topic, origin=origin, passages=len(passages),
             chars=len(raw))
    return SourceDoc(topic=topic, origin=origin, origin_ref=ref, text=raw,
                     passages=passages)
