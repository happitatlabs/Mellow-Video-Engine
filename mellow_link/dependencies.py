"""
Mellow-Link - Shared FastAPI Dependencies

Reusable Depends() functions for admin authentication etc.
"""

import logging
from typing import Optional

from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.orm import Session

from mellow_link.infra import get_db, User, UserRole, get_current_user_optional

logger = logging.getLogger(__name__)


def get_admin_user_required(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    어드민 권한 필수 의존성. /evolution/*, /autonomous/* 등 경로 전용.
    유효하지 않은 토큰 또는 일반 사용자 → SecurityError 로깅 후 HTTP 403.
    """
    if not authorization:
        logger.warning("[AdminDep] SecurityError: No authorization header")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        logger.warning("[AdminDep] SecurityError: Empty token")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
    if token.startswith("guest_"):
        logger.warning("[AdminDep] SecurityError: Guest token rejected")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            logger.warning("[AdminDep] SecurityError: Invalid token payload")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            logger.warning("[AdminDep] SecurityError: User not found")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
        if user.role != UserRole.ADMIN.value:
            logger.warning(f"[AdminDep] SecurityError: Non-admin access attempted by {username}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[AdminDep] SecurityError: Token validation failed: {e}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")


def get_admin_user_for_flow_view(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Flow view 전용: Authorization 헤더로만 Admin 검증 (보안: query access_token 미지원)."""
    token = None
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
    if not token:
        logger.warning("[Monitor] SecurityError: No authorization for flow view")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
    if token.startswith("guest_"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
        user = db.query(User).filter(User.username == username).first()
        if not user or user.role != UserRole.ADMIN.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")
        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[Monitor] Token validation failed: {e}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본 기능은 하우스 관리자(Admin) 전용 구역입니다.")


def resolve_console_viewer(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    콘솔 뷰용 사용자 식별(옵션). Authorization 헤더만 사용 (보안: query access_token 미지원).
    """
    token = None
    if authorization:
        token = authorization.replace("Bearer ", "").strip()

    if not token:
        return {"role": "anonymous", "is_guest": False, "authenticated": False, "username": "anonymous"}

    if token.startswith("guest_"):
        return {"role": "guest", "is_guest": True, "authenticated": True, "username": "guest"}

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return {"role": "anonymous", "is_guest": False, "authenticated": False, "username": "anonymous"}
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return {"role": "anonymous", "is_guest": False, "authenticated": False, "username": "anonymous"}
        return {
            "role": user.role or "user",
            "is_guest": False,
            "authenticated": True,
            "username": username,
        }
    except Exception as e:
        logger.warning(f"[ConsoleViewer] Token validation failed (fallback anonymous): {e}")
        return {"role": "anonymous", "is_guest": False, "authenticated": False, "username": "anonymous"}
