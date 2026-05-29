import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.deps import SessionDep, require_role
from app.models import User, UserRole
from app.schemas import UserRead, UserUpdate

router = APIRouter(prefix="/admin/users", tags=["admin"])

AdminOnly = Annotated[User, Depends(require_role(UserRole.admin))]


@router.get("", response_model=list[UserRead])
async def list_users(
    me: AdminOnly,
    session: SessionDep,
    role: Annotated[UserRole | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    email_like: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[User]:
    stmt = select(User).order_by(User.created_at).limit(limit).offset(offset)
    if role is not None:
        stmt = stmt.where(User.role == role.value)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if email_like:
        stmt = stmt.where(User.email.ilike(f"%{email_like}%"))
    return list(await session.scalars(stmt))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID, me: AdminOnly, session: SessionDep
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    me: AdminOnly,
    session: SessionDep,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # 自我保护: 防止当前 admin 把自己降级或停用 -> 系统可能没有任何活跃 admin
    if user.id == me.id:
        if data.role is not None and data.role != UserRole.admin:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot demote yourself; have another admin do it",
            )
        if data.is_active is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Cannot deactivate yourself"
            )

    if data.role is not None:
        user.role = data.role.value
    if data.is_active is not None:
        user.is_active = data.is_active
    await session.commit()
    await session.refresh(user)
    return user
