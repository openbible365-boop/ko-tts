"""Cloudflare R2 (S3 兼容) 异步封装。

基于 aioboto3。每次操作开一个 client(简单且正确);后续如需优化可在
应用 lifespan 里维护长连接 client。
"""

import uuid

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

_session = aioboto3.Session()
_config = Config(
    signature_version="s3v4",
    retries={"max_attempts": 3, "mode": "standard"},
)

# head_object/get_object 在对象不存在时返回的错误码
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


def _client():
    return _session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",  # R2 固定用 "auto"
        config=_config,
    )


# ---- 对象键 ----
def recording_key(recording_id: uuid.UUID | str, ext: str = "") -> str:
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return f"recordings/{recording_id}/original{ext}"


def processed_key(recording_id: uuid.UUID | str) -> str:
    return f"recordings/{recording_id}/vocals.wav"


def segment_key(segment_id: uuid.UUID | str) -> str:
    return f"segments/{segment_id}.wav"


def script_docx_key(script_id: uuid.UUID | str) -> str:
    """范文原始 Word 文件 (解析后正文已入库, 这份仅备查)。"""
    return f"scripts/{script_id}/source.docx"


def recording_line_key(segment_id: uuid.UUID | str) -> str:
    """录音样品逐行 clip (浏览器 MediaRecorder, webm/mp4; ffmpeg 按内容识别, 不带扩展名)。"""
    return f"segments/{segment_id}/clip"


# ---- 服务端直传/读取 ----
async def put_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else {}
    async with _client() as s3:
        await s3.put_object(Bucket=settings.r2_bucket, Key=key, Body=data, **extra)


async def get_bytes(key: str) -> bytes:
    async with _client() as s3:
        resp = await s3.get_object(Bucket=settings.r2_bucket, Key=key)
        async with resp["Body"] as body:
            return await body.read()


async def delete_object(key: str) -> None:
    async with _client() as s3:
        await s3.delete_object(Bucket=settings.r2_bucket, Key=key)


async def head_object(key: str) -> dict | None:
    """返回对象元数据;不存在返回 None。"""
    async with _client() as s3:
        try:
            return await s3.head_object(Bucket=settings.r2_bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                return None
            raise


async def object_exists(key: str) -> bool:
    return await head_object(key) is not None


# ---- 预签名 URL (客户端直传/直读, 不经过后端) ----
async def presigned_put_url(
    key: str, *, expires_in: int = 3600, content_type: str | None = None
) -> str:
    params: dict[str, str] = {"Bucket": settings.r2_bucket, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    async with _client() as s3:
        return await s3.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires_in
        )


async def presigned_get_url(key: str, *, expires_in: int = 3600) -> str:
    async with _client() as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=expires_in,
        )


async def check_connection() -> None:
    """对 bucket 做一次 head_bucket;凭据/连通性异常会抛出。"""
    async with _client() as s3:
        await s3.head_bucket(Bucket=settings.r2_bucket)
