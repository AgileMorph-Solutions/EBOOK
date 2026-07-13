"""Stage 0 — ingest & normalize.

Accepts pasted text or a YouTube URL. For YouTube it pulls captions first
(cheap, exact) and falls back to ASR only when no transcript exists. The output
is a SourceDoc with stable, chunked passages that everything downstream cites.
"""
from __future__ import annotations

import os
import re

import structlog

from bookforge.config import get_settings
from bookforge.errors import IngestError
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


def _cookies_file() -> str | None:
    """Path to a Netscape-format cookies.txt for an authenticated YouTube
    session, used to work around YouTube IP-blocking this server's requests.
    Returns None (unauthenticated) if unset, missing, or empty (placeholder)."""
    path = get_settings().yt_cookies_file
    if path and os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    if path:
        log.warning("yt_cookies_file_missing_or_empty", path=path)
    return None


def _authenticated_session():
    """A requests.Session pre-loaded with cookies from YT_COOKIES_FILE, or
    None if no cookies file is configured (falls back to anonymous requests)."""
    path = _cookies_file()
    if not path:
        return None
    import http.cookiejar

    import requests

    jar = http.cookiejar.MozillaCookieJar(path)
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = jar
    return session


def _fetch_youtube(url: str) -> str:
    vid = _YT_RE.search(url)
    if not vid:
        raise ValueError(f"Not a recognizable YouTube URL: {url}")
    video_id = vid.group(1)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi(http_client=_authenticated_session())
        # v1.x: instance method fetch(); v0.x: class method get_transcript()
        try:
            parts = list(api.fetch(video_id))
        except (AttributeError, TypeError):
            parts = YouTubeTranscriptApi.get_transcript(video_id)  # type: ignore[attr-defined]
        return " ".join(p["text"] if isinstance(p, dict) else p.text for p in parts)
    except Exception as exc:  # no captions -> ASR fallback
        log.warning("no_captions_falling_back_to_asr", err=str(exc))
        return _asr_youtube(video_id)


_BOT_BLOCK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "http error 429",
    "http error 403",
)


def _is_bot_block(err_text: str) -> bool:
    lower = err_text.lower()
    return any(m in lower for m in _BOT_BLOCK_MARKERS)


def _asr_youtube(video_id: str) -> str:
    """Download audio with yt-dlp and transcribe with faster-whisper. Wrapped so
    a blocked/geo-restricted video or a missing dependency surfaces as a clear
    IngestError instead of an unhandled traceback. YouTube bot-blocks are tagged
    reason="youtube_blocked" so the user gets an actionable message ("paste the
    text instead") rather than a generic failure."""
    import tempfile

    try:
        import yt_dlp
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise IngestError(
            f"Audio transcription is unavailable on this server: {str(exc)[:200]}"
        ) from exc

    cookies_path = _cookies_file()
    url = f"https://www.youtube.com/watch?v={video_id}"
    # YouTube's bot checks differ per player client — datacenter IPs that the
    # default web client blocks often pass via the android/ios clients.
    client_variants: list[list[str] | None] = [None, ["android"], ["ios", "web_safari"]]
    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/audio.%(ext)s"
        last_exc: Exception | None = None
        for clients in client_variants:
            opts: dict = {"format": "bestaudio", "outtmpl": out, "quiet": True,
                          "postprocessors": [{"key": "FFmpegExtractAudio",
                                              "preferredcodec": "mp3"}]}
            if cookies_path:
                opts["cookiefile"] = cookies_path
            if clients:
                opts["extractor_args"] = {"youtube": {"player_client": clients}}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 — try the next client
                last_exc = exc
                log.warning("yt_dlp_client_failed",
                            clients=clients or "default", err=str(exc)[:160])
        if last_exc is not None:
            msg = str(last_exc)[:300]
            raise IngestError(
                f"Could not download this video's audio: {msg}",
                reason="youtube_blocked" if _is_bot_block(msg) else "",
            ) from last_exc
        try:
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(f"{tmp}/audio.mp3")
            return " ".join(s.text for s in segments)
        except Exception as exc:
            raise IngestError(
                f"Could not get a transcript for this video (no captions and "
                f"audio transcription failed): {str(exc)[:200]}"
            ) from exc


def ingest(topic: str, *, text: str | None = None, youtube_url: str | None = None) -> SourceDoc:
    if youtube_url:
        raw = _fetch_youtube(youtube_url)
        origin, ref = "youtube", youtube_url
    elif text:
        raw, origin, ref = text, "text", None
    else:
        raise ValueError("Provide either text or youtube_url")

    raw = re.sub(r"[ \t]+", " ", raw).strip()
    if not raw:
        raise IngestError("Source has no readable text content to build a book from")
    passages = _chunk(raw)
    log.info("ingested", topic=topic, origin=origin, passages=len(passages),
             chars=len(raw))
    return SourceDoc(topic=topic, origin=origin, origin_ref=ref, text=raw,
                     passages=passages)
