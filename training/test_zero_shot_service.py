import io
import wave

import pytest

from gpu_backend import zero_shot_service


def _silence_wav(duration_sec: float, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0\0\0\0" * int(duration_sec * sample_rate))
    return buffer.getvalue()


def test_zero_shot_voice_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(zero_shot_service, "ZERO_SHOT_VOICE_DIR", tmp_path)

    result = zero_shot_service.save_zero_shot_voice(
        "kr-f4-零样本",
        _silence_wav(6.0),
        "안녕하세요.",
        "ko",
    )

    assert result["exp"] == "kr-f4-零样本"
    assert result["duration_sec"] == pytest.approx(6.0, abs=0.05)
    reference = tmp_path / "kr-f4-零样本" / "reference.wav"
    with wave.open(str(reference), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
    assert zero_shot_service.get_zero_shot_reference("kr-f4-零样本") == (
        str(reference),
        "안녕하세요.",
        "ko",
    )
    assert zero_shot_service.list_zero_shot_voices() == ["kr-f4-零样本"]
    assert zero_shot_service.delete_zero_shot_voice("kr-f4-零样本") is True
    assert zero_shot_service.list_zero_shot_voices() == []


def test_rejects_reference_shorter_than_three_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(zero_shot_service, "ZERO_SHOT_VOICE_DIR", tmp_path)

    with pytest.raises(ValueError, match="3–9 秒"):
        zero_shot_service.save_zero_shot_voice(
            "too-short-零样本",
            _silence_wav(1.0),
            "짧은 문장",
            "ko",
        )
