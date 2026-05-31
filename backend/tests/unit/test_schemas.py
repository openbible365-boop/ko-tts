"""Pydantic schema 校验测试。"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    RecordingCreate,
    SegmentCorrect,
    SegmentReject,
    UserCreate,
    UserUpdate,
)


# ---- UserCreate ----
def test_user_create_ok():
    u = UserCreate(email="a@b.com", password="12345678")
    assert u.email == "a@b.com"


def test_user_create_password_too_short():
    with pytest.raises(ValidationError):
        UserCreate(email="a@b.com", password="short")


def test_user_create_bad_email():
    with pytest.raises(ValidationError):
        UserCreate(email="not-an-email", password="12345678")


# ---- UserUpdate ----
def test_user_update_empty_rejected():
    with pytest.raises(ValidationError):
        UserUpdate()


def test_user_update_role_only_ok():
    u = UserUpdate(role="reviewer")
    assert u.role.value == "reviewer"


def test_user_update_active_only_ok():
    u = UserUpdate(is_active=False)
    assert u.is_active is False


def test_user_update_bad_role():
    with pytest.raises(ValidationError):
        UserUpdate(role="superuser")


# ---- RecordingCreate ----
def test_recording_create_ok():
    r = RecordingCreate(
        content_category="sermon", language="ko", original_filename="x.mp3"
    )
    assert r.content_category.value == "sermon"
    assert r.language.value == "ko"


def test_recording_create_bad_category():
    with pytest.raises(ValidationError):
        RecordingCreate(
            content_category="podcast", language="ko", original_filename="x.mp3"
        )


def test_recording_create_bad_language():
    with pytest.raises(ValidationError):
        RecordingCreate(
            content_category="sermon", language="jp", original_filename="x.mp3"
        )


def test_recording_create_empty_filename():
    with pytest.raises(ValidationError):
        RecordingCreate(
            content_category="sermon", language="ko", original_filename=""
        )


# ---- SegmentCorrect / SegmentReject ----
def test_segment_correct_ok():
    SegmentCorrect(text="hello")


def test_segment_correct_empty():
    with pytest.raises(ValidationError):
        SegmentCorrect(text="")


def test_segment_reject_requires_reason():
    with pytest.raises(ValidationError):
        SegmentReject(rejection_reason="")
