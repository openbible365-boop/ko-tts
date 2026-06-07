from app.textcompare import is_match, normalize_words


def test_normalize_strips_punctuation_and_collapses_space():
    assert normalize_words("  하늘과  땅을, 창조하셨다.  ") == ["하늘과", "땅을", "창조하셨다"]


def test_normalize_empty_and_none():
    assert normalize_words("") == []
    assert normalize_words(None) == []
    assert normalize_words("   ") == []


def test_match_exact():
    assert is_match("시초에 하나님이 하늘과 땅을 창조하셨다", "시초에 하나님이 하늘과 땅을 창조하셨다")


def test_match_ignores_punctuation_and_whitespace():
    assert is_match("하나님이 그 빛을 좋게 보셨다.", "하나님이 그 빛을 좋게 보셨다")
    assert is_match("빛이 생기라  하고", "빛이 생기라 하고")


def test_match_case_insensitive_for_latin():
    assert is_match("In the Beginning", "in the beginning")


def test_mismatch_on_word_difference():
    assert not is_match("하나님이 하늘과 땅을 창조하셨다", "하나님이 하늘과 바다를 창조하셨다")


def test_mismatch_on_missing_word():
    assert not is_match("저녁이 되고 아침이 되니", "저녁이 되고 아침이")
