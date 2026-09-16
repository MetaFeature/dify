"""Measure an uploaded voice clip so the Campus duration limit can be enforced.

The browser records compressed audio, so ``mutagen`` (pure Python, installed in
the Campus image) is the primary reader and the standard-library ``wave`` reader
is the fallback for uncompressed recordings. Anything the platform cannot parse
returns ``None``: the limit refuses what it can measure instead of blocking a
legitimate upload it cannot understand, and the client-side check still covers
the student experience.
"""

from io import BytesIO
from typing import Final

from configs import dify_config
from services.campus.errors import CampusAudioDurationExceededError

AUDIO_EXTENSIONS: Final = frozenset(
    {".mp3", ".m4a", ".mp4", ".wav", ".webm", ".ogg", ".oga", ".opus", ".flac", ".aac", ".amr"}
)
WAV_MAGIC: Final = b"RIFF"


def probe_audio_duration_seconds(content: bytes, *, filename: str | None = None) -> float | None:
    """Return the clip length in seconds, or ``None`` when it cannot be read."""
    if not content:
        return None

    length = _probe_with_mutagen(content)
    if length is not None:
        return length
    if _looks_like_wav(content, filename):
        return _probe_wav(content)
    return None


def ensure_audio_duration_within_limit(
    *, content: bytes, filename: str | None = None, mime_type: str | None = None
) -> None:
    """Reject a voice upload that is longer than the configured limit.

    A no-op unless Campus is enabled and the limit is positive, and a no-op for
    anything that is not audio, so the administrator workspace keeps upstream
    behaviour.
    """
    limit = dify_config.CAMPUS_AUDIO_MAX_DURATION_SECONDS
    if not dify_config.CAMPUS_ENABLED or limit <= 0:
        return
    if not _is_audio(filename, mime_type):
        return

    duration = probe_audio_duration_seconds(content, filename=filename)
    if duration is not None and duration > limit:
        raise CampusAudioDurationExceededError(
            f"语音时长不能超过 {limit} 秒（当前约 {round(duration)} 秒），请上传更短的语音。"
        )


def _is_audio(filename: str | None, mime_type: str | None) -> bool:
    if mime_type and mime_type.lower().startswith("audio/"):
        return True
    name = (filename or "").lower()
    return any(name.endswith(extension) for extension in AUDIO_EXTENSIONS)


def _looks_like_wav(content: bytes, filename: str | None) -> bool:
    return content[:4] == WAV_MAGIC or (filename or "").lower().endswith(".wav")


def _probe_with_mutagen(content: bytes) -> float | None:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return None
    try:
        parsed = MutagenFile(BytesIO(content))
    except Exception:
        # Parsing untrusted media: any failure just means "unknown length".
        return None
    length = getattr(getattr(parsed, "info", None), "length", None)
    if isinstance(length, (int, float)) and length > 0:
        return float(length)
    return None


def _probe_wav(content: bytes) -> float | None:
    import wave

    try:
        with wave.open(BytesIO(content), "rb") as handle:
            frame_rate = handle.getframerate() or 0
            if frame_rate <= 0:
                return None
            return handle.getnframes() / float(frame_rate)
    except Exception:
        return None
