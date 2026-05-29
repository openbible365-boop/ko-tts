"""compute_segments 纯函数测试 (不需要 ffmpeg / DB)。

新策略: 合并语音块到 ~target, 在静音(句末停顿)处下刀; 仅整段无停顿时硬切。
"""

from app.segmentation import compute_segments


def test_merge_until_target_break_at_silence():
    # 8 个 ~5s 语音块(块间 0.5s 停顿), target=13 -> 在停顿处合并成 ~15/14.5/9.5 三段
    silences = [(5, 5.5), (10, 10.5), (15, 15.5), (20, 20.5),
                (25, 25.5), (30, 30.5), (35, 35.5)]
    out = compute_segments(40.0, silences, min_len=3, target_len=13, max_len=18)
    assert out == [(0.0, 15.0), (15.5, 30.0), (30.5, 40.0)]
    # 每刀都落在某个停顿的起点(句末), 不切在句中
    for _, end in out:
        assert end == 40.0 or any(abs(end - s) < 1e-6 for s, _ in silences)


def test_short_sentences_merge_up():
    # 很多短句(2s 语音 + 0.3s 停顿)不该碎成一堆 —— 8 个语音块合并成 2 段,
    # 第一段到 target 后在停顿(13.5)断, 余下并到末尾。
    silences = [(t, t + 0.3) for t in [2, 4.3, 6.6, 8.9, 11.2, 13.5, 15.8]]
    out = compute_segments(18.0, silences, min_len=3, target_len=13, max_len=18)
    assert out == [(0.0, 13.5), (13.8, 18.0)]


def test_hard_split_only_when_no_pause():
    # 整段连续语音无停顿, 超过 max 才兜底硬切
    out = compute_segments(40.0, [], min_len=3, target_len=13, max_len=18)
    assert out == [(0.0, 18.0), (18.0, 36.0), (36.0, 40.0)]


def test_break_prefers_pause_over_hard_max():
    # 12.9s 语音 -> 0.5s 停顿 -> 长块; 应在 12.9 的停顿断, 而非 18 的硬上限
    out = compute_segments(30.0, [(12.9, 13.4)], min_len=3, target_len=13, max_len=18)
    assert out[0] == (0.0, 12.9)
    assert out[1][0] == 13.4


def test_trailing_none_end():
    # 结尾静音可能只有 silence_start 没有 silence_end
    out = compute_segments(10.0, [(8.0, None)], min_len=3, target_len=13, max_len=18)
    assert out == [(0.0, 8.0)]


def test_drop_short_tail():
    # 末段太短 (< min) 丢弃: 0..18 一段; 18.3..20.5=2.2s < 3 丢
    out = compute_segments(20.5, [(18.0, 18.3)], min_len=3, target_len=13, max_len=18)
    assert out == [(0.0, 18.0)]


def test_no_audio():
    out = compute_segments(0.0, [], min_len=3, target_len=13, max_len=18)
    assert out == []


def test_all_silence():
    out = compute_segments(5.0, [(0.0, 5.0)], min_len=3, target_len=13, max_len=18)
    assert out == []
