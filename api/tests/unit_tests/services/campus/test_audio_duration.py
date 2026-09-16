import io
import wave

import pytest

from configs import dify_config
from services.campus.audio_duration import (
    ensure_audio_duration_within_limit,
    probe_audio_duration_seconds,
)
from services.campus.errors import CampusAudioDurationExceededError


def _wav(seconds: float, *, frame_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(frame_rate)
        handle.writeframes(b"\x00\x00" * int(frame_rate * seconds))
    return buffer.getvalue()


@pytest.fixture
def campus_enabled(monkeypatch):
    monkeypatch.setattr(dify_config, "CAMPUS_ENABLED", True)
    monkeypatch.setattr(dify_config, "CAMPUS_AUDIO_MAX_DURATION_SECONDS", 60)


def test_probe_reads_the_length_of_a_wav_clip():
    assert probe_audio_duration_seconds(_wav(3), filename="clip.wav") == pytest.approx(3, abs=0.1)
    assert probe_audio_duration_seconds(_wav(90), filename="clip.wav") == pytest.approx(90, abs=0.1)


def test_probe_returns_none_for_unreadable_audio():
    assert probe_audio_duration_seconds(b"", filename="clip.wav") is None
    assert probe_audio_duration_seconds(b"not audio at all", filename="clip.mp3") is None


def test_limit_rejects_a_clip_longer_than_a_minute(campus_enabled):
    ensure_audio_duration_within_limit(content=_wav(30), filename="ok.wav", mime_type="audio/wav")

    with pytest.raises(CampusAudioDurationExceededError, match="不能超过 60 秒"):
        ensure_audio_duration_within_limit(content=_wav(90), filename="long.wav", mime_type="audio/wav")


def test_limit_ignores_non_audio_and_disabled_campus(campus_enabled, monkeypatch):
    # A long recording is still just a file when it is not declared as audio.
    ensure_audio_duration_within_limit(content=_wav(90), filename="clip.bin", mime_type="application/octet-stream")

    monkeypatch.setattr(dify_config, "CAMPUS_ENABLED", False)
    ensure_audio_duration_within_limit(content=_wav(90), filename="long.wav", mime_type="audio/wav")


def test_limit_can_be_switched_off(campus_enabled, monkeypatch):
    monkeypatch.setattr(dify_config, "CAMPUS_AUDIO_MAX_DURATION_SECONDS", 0)

    ensure_audio_duration_within_limit(content=_wav(600), filename="long.wav", mime_type="audio/wav")


def test_unreadable_audio_falls_through_to_the_client_check(campus_enabled):
    # The server only refuses what it can measure; a format it cannot parse is
    # left to the upload path rather than blocking a legitimate recording.
    ensure_audio_duration_within_limit(content=b"\x00\x01\x02", filename="clip.opus", mime_type="audio/opus")
