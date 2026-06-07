"""录音样品: ASR 识别文字与范文行的"按词 + 规整后"比对。

规整 = 转小写 + 去标点(替为空格) + 折叠空白 + 按空格分词。
用于 worker 判定 录音是否"通过"(词序列完全一致 → approved, 否则 → pending_review)。
前端做逐词标红时应沿用同样的规整规则, 以保持一致。纯函数, 便于单测。
"""

import re

# 去掉标点/符号(\w 在 re.UNICODE 下含韩文音节与字母数字, 故只去非词非空白字符)
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_words(text: str | None) -> list[str]:
    if not text:
        return []
    cleaned = _NON_WORD.sub(" ", text.lower())
    return cleaned.split()


def is_match(expected: str | None, actual: str | None) -> bool:
    """规整后词序列完全一致才算通过。"""
    return normalize_words(expected) == normalize_words(actual)
