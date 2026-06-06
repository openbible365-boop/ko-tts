"""解析上传的 .docx 范文: 按段落拆行, 跳空行, 两端 trim。

纯函数 parse_docx_lines(bytes) -> list[str], 便于单测。
只取正文段落(document.paragraphs); 表格/图片/页眉页脚忽略。
一个段落(回车) = 一行 = 一个待录切分; 不做朗读时长校验(按需求)。
"""

import io

from docx import Document
from docx.opc.exceptions import PackageNotFoundError


class DocxParseError(ValueError):
    """上传的文件不是合法的 .docx (损坏或非 Word 格式)。"""


def parse_docx_lines(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
    except PackageNotFoundError as e:
        raise DocxParseError("无法解析文件: 不是合法的 .docx 文档") from e
    except Exception as e:  # 损坏的 zip / 底层格式异常, 一律当作不可解析
        raise DocxParseError("无法解析文件: 文档损坏或格式不受支持") from e

    lines: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    return lines
