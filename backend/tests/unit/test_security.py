"""bcrypt + JWT 单元测试。"""

import jwt
import pytest

from app.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_bcrypt_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_bcrypt_each_hash_is_unique():
    # 加盐: 同一密码两次哈希应不同
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
    assert verify_password("same", h1) and verify_password("same", h2)


def test_jwt_roundtrip():
    tok = create_access_token("user-123")
    payload = decode_token(tok)
    assert payload["sub"] == "user-123"
    # 默认 7 天 = 10080 分钟
    assert round((payload["exp"] - payload["iat"]) / 60) == 7 * 24 * 60


def test_jwt_custom_expiry():
    tok = create_access_token("u", expires_minutes=15)
    payload = decode_token(tok)
    assert round((payload["exp"] - payload["iat"]) / 60) == 15


def test_jwt_expired():
    tok = create_access_token("u", expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(tok)


def test_jwt_bad_secret():
    tok = create_access_token("u")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(tok, "wrong-secret-0000000000000000000000000000000000000000000000000000000000",
                   algorithms=["HS256"])
