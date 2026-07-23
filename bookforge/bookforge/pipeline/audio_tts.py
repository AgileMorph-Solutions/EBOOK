"""ElevenLabs TTS for audio scripts: per-item speech + ffmpeg concat."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import structlog

from bookforge.clients import elevenlabs
from bookforge.config import settings
from bookforge.pipeline.pools import ctx_pool
from bookforge.schemas import AudioScriptItem, AudioScriptResult

log = structlog.get_logger(__name__)

ProgressFn = Callable[[str, int, int], None]


def speakable_text(item: AudioScriptItem) -> str:
    """Flatten one item into a single TTS utterance."""
    body = " ".join(item.body.split())
    title = item.title.strip()
    if not title:
        return body
    return f"{title}. {body}"


def _concat_mp3(parts: list[Path], dest: Path) -> None:
    """Concatenate MP3 files with ffmpeg stream copy."""
    if not parts:
        raise RuntimeError("No audio parts to concatenate")
    if len(parts) == 1:
        dest.write_bytes(parts[0].read_bytes())
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to merge narration audio")

    list_file = dest.parent / "concat.txt"
    lines = []
    for p in parts:
        # ffmpeg concat demuxer needs escaped single quotes in paths
        escaped = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n")

    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 100:
        raise RuntimeError(
            f"ffmpeg concat failed: {(proc.stderr or proc.stdout or '')[:400]}"
        )


def generate_narration_audio(
    result: AudioScriptResult,
    *,
    voice_id: str | None = None,
    model_id: str | None = None,
    voice_settings: dict | None = None,
    on_progress: ProgressFn | None = None,
    stage: str = "generating_audio",
) -> bytes:
    """Synthesize each item then return a single concatenated MP3."""
    items = sorted(result.items, key=lambda x: x.number)
    if not items:
        raise ValueError("No items to narrate")

    total = len(items)
    if on_progress:
        on_progress(stage, 0, total)

    done_lock = threading.Lock()
    done_count = 0
    chunks: list[bytes | None] = [None] * total

    def _one(idx: int, item: AudioScriptItem) -> None:
        nonlocal done_count
        text = speakable_text(item)
        audio = elevenlabs.text_to_speech(
            text,
            voice_id=voice_id,
            model_id=model_id,
            voice_settings=voice_settings,
        )
        chunks[idx] = audio
        with done_lock:
            done_count += 1
            if on_progress:
                on_progress(stage, done_count, total)

    with ctx_pool(settings.elevenlabs_tts_workers, "tts") as pool:
        futures = [pool.submit(_one, i, items[i]) for i in range(total)]
        for fut in futures:
            fut.result()

    with tempfile.TemporaryDirectory(prefix="audio-script-") as tmp:
        tmp_path = Path(tmp)
        part_paths: list[Path] = []
        for i, data in enumerate(chunks):
            if not data:
                raise RuntimeError(f"Missing audio for item {i + 1}")
            part = tmp_path / f"item-{i + 1:03d}.mp3"
            part.write_bytes(data)
            part_paths.append(part)
        out = tmp_path / "narration.mp3"
        _concat_mp3(part_paths, out)
        audio_bytes = out.read_bytes()

    log.info("narration_audio_done", items=total, bytes=len(audio_bytes))
    return audio_bytes
