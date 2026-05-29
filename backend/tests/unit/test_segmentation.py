"""compute_segments 纯函数测试 (不需要 ffmpeg / DB)。"""

from app.segmentation import compute_segments


def test_basic_split():
    # 10s 文件, 两段静音 -> 3 段语音
    out = compute_segments(10.0, [(2, 3), (6, 7)], min_len=0.5, max_len=15)
    assert out == [(0.0, 2.0), (3.0, 6.0), (7.0, 10.0)]


def test_drop_too_short():
    # 中间和末尾的段都太短, 全部丢
    out = compute_segments(10.0, [(0, 0.2), (0.5, 9.8)], min_len=0.5, max_len=15)
    assert out == []


def test_max_split():
    # 35s 一整段语音, max=15 -> 拆 15/15/5
    out = compute_segments(35.0, [], min_len=1, max_len=15)
    assert out == [(0.0, 15.0), (15.0, 30.0), (30.0, 35.0)]


def test_trailing_none_end():
    # silencedetect 在文件结尾的静音可能只有 silence_start 没有 silence_end
    out = compute_segments(10.0, [(8.0, None)], min_len=0.5, max_len=15)
    assert out == [(0.0, 8.0)]


def test_remainder_dropped_when_below_min():
    # 15.5s 切到 15, 剩 0.5 < min(1) -> 丢
    out = compute_segments(15.5, [], min_len=1, max_len=15)
    assert out == [(0.0, 15.0)]


def test_no_audio():
    out = compute_segments(0.0, [], min_len=0.5, max_len=15)
    assert out == []


def test_all_silence():
    # 整段都是静音
    out = compute_segments(5.0, [(0.0, 5.0)], min_len=0.5, max_len=15)
    assert out == []
