import shutil
import subprocess
import wave

import pytest

from normalize_cosyvoice_audio import audio_paths, normalize_audio


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is required")
def test_normalizes_mislabeled_webm_to_pcm_wav(tmp_path):
    clip = tmp_path / "clip.wav"
    subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            str(clip),
        ],
        check=True,
    )

    normalize_audio(clip, 24000, FFMPEG)

    with wave.open(str(clip), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() > 0


def test_audio_paths_deduplicates_entries(tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"placeholder")
    train_list = tmp_path / "train.list"
    train_list.write_text(
        f"{clip}|speaker|ko|one\n{clip}|speaker|ko|two\n",
        encoding="utf-8",
    )

    assert audio_paths(train_list) == [clip.resolve()]
