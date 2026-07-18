from gpu_backend.text_chunking import split_tts_text


def test_short_text_is_unchanged():
    assert split_tts_text("짧은 문장입니다.") == ["짧은 문장입니다."]


def test_long_korean_without_punctuation_splits_on_words_and_round_trips():
    text = (
        "손이 모르게 하여 네 구제함이 은밀하게 하라 은밀한 중에 보시는 "
        "너의 아버지가 갚으시리라 또 너희가 기도할 때에 외식하는 자와 같이 되지 말라"
    )
    chunks = split_tts_text(text, max_chars=36)

    assert len(chunks) > 1
    assert all(len(chunk) <= 36 for chunk in chunks)
    assert " ".join(chunks) == text


def test_sentence_endings_are_preferred():
    text = "첫째 문장입니다. 둘째 문장입니다! 셋째 문장입니다?"
    assert split_tts_text(text, max_chars=20) == [
        "첫째 문장입니다.",
        "둘째 문장입니다!",
        "셋째 문장입니다?",
    ]


def test_long_unbroken_text_is_not_dropped():
    text = "가" * 205
    chunks = split_tts_text(text, max_chars=80)

    assert [len(chunk) for chunk in chunks] == [80, 80, 45]
    assert "".join(chunks) == text
