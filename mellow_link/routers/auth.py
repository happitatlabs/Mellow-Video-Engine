"""
Mellow-Link - Authentication Router

Endpoints: /auth/register, /auth/token, /auth/guest-login, /auth/me
"""

import logging
import os
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from mellow_link import app_state
from mellow_link.config import get_settings
from mellow_link.core import (
    bootstrap_admin_account, is_admin_user,
)
from mellow_link.infra import (
    get_db, User, UserRole, AgentFolder, ChatSession,
    create_default_folders_for_user, ensure_user_has_folders, get_or_create_default_session,
    verify_password, get_password_hash, create_access_token, get_current_user,
    check_guest_limit, increment_guest_usage,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from mellow_link.utils import (
    launch_avatar_service, get_avatar_status,
    is_port_active, DEFAULT_AVATAR_WS_PORT,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Auth"])


# =============================================================================
# Custom Exceptions
# =============================================================================

class AuthenticationInterruptedError(Exception):
    """Raised when authentication process is cancelled or aborted."""
    pass


class AuthenticationCancelledError(AuthenticationInterruptedError):
    """Raised when authentication is cancelled (e.g., timeout, client disconnect)."""
    pass


# =============================================================================
# Request/Response Models
# =============================================================================

class RegisterRequest(BaseModel):
    """User registration request model."""
    username: str = Field(..., min_length=2, max_length=50, description="Username")
    password: str = Field(..., min_length=4, max_length=72, description="Password (max 72 chars for bcrypt)")


class GuestLoginRequest(BaseModel):
    """Guest login request model."""
    access_code: str = Field(..., description="Guest access code")


class GuestLoginResponse(BaseModel):
    """Guest login response model."""
    access_token: str
    token_type: str = "bearer"
    user_id: str = "guest"
    role: str = "guest"
    expires_in: int


# =============================================================================
# Avatar Launch Helper (triggered on admin login)
# =============================================================================

def _launch_avatar_on_admin_login(admin_username: str) -> None:
    """
    Background task to launch Electron avatar app when admin logs in.

    서버가 이미 실행 중이면 Electron 앱만 실행합니다.
    서버가 실행 중이 아니면 서버를 먼저 실행한 후 Electron 앱을 실행합니다.
    """
    try:
        avatar_port = app_state.settings.avatar_ws_port if app_state.settings else DEFAULT_AVATAR_WS_PORT

        logger.info(f"[Avatar] Triggered by admin login: {admin_username}")
        logger.info(f"[Avatar] Target port: {avatar_port}")

        # 1. 서버가 실행 중인지 확인
        server_running = is_port_active(avatar_port)
        if server_running:
            logger.info(f"[Avatar] Server already active on port {avatar_port}")
        else:
            logger.info(f"[Avatar] Server not running, launching...")
            success = launch_avatar_service(port=avatar_port)
            if not success:
                logger.warning("[Avatar] Failed to launch avatar server")
                return

        # 2. Electron 앱 실행 (Pet Mode) — 경로는 설정/환경변수 사용
        exe_path_str = (app_state.settings and getattr(app_state.settings, "avatar_electron_exe", None)) or os.environ.get("MELLOW_AVATAR_ELECTRON_EXE", "")
        target_exe = Path(exe_path_str) if exe_path_str else None

        if not target_exe or not target_exe.exists():
            logger.warning(f"[Avatar] Electron app not found: {target_exe}")
            return

        logger.info(f"[Avatar] Launching Electron app: {target_exe}")

        # 좀비 프로세스 정리
        exe_name = "open-llm-vtuber-electron.exe"
        try:
            kill_result = subprocess.run(
                ["taskkill", "/F", "/IM", exe_name],
                capture_output=True,
                timeout=5
            )
            if kill_result.returncode == 0:
                logger.info(f"[Avatar] Killed existing Electron process")
                time.sleep(1.0)
        except Exception:
            pass

        # Batch 파일을 통한 완전 독립 실행
        electron_working_dir = str(target_exe.parent.absolute())
        electron_exe_name = target_exe.name

        bat_content = f'''@echo off
cd /d "{electron_working_dir}"
start "" "{electron_exe_name}"
exit
'''
        temp_dir = tempfile.gettempdir()
        bat_path = os.path.join(temp_dir, "launch_electron_avatar.bat")

        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)

        logger.info(f"[Avatar] Batch file created: {bat_path}")

        cmd_command = f'start /b cmd /c "{bat_path}"'
        exit_code = os.system(cmd_command)

        if exit_code == 0:
            logger.info(f"[Avatar] Electron app launched successfully")
        else:
            logger.warning(f"[Avatar] Electron launch exit_code: {exit_code}")

    except Exception as e:
        logger.error(f"[Avatar] Error launching avatar: {e}")
        import traceback
        traceback.print_exc()


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/auth/register")
def register(user_data: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user with 'user' role."""
    try:
        # 1. Username validation
        username = user_data.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="사용자명을 입력해주세요")

        # 2. Password validation - prevent double hashing
        plain_password = user_data.password

        # Check if password looks like an already-hashed value (bcrypt starts with $2)
        if plain_password.startswith('$2') and len(plain_password) > 50:
            raise HTTPException(
                status_code=400,
                detail="잘못된 비밀번호 형식입니다. 일반 비밀번호를 입력해주세요."
            )

        # Check raw byte length (bcrypt 72-byte limit)
        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            raise HTTPException(
                status_code=400,
                detail=f"비밀번호가 너무 깁니다 ({len(password_bytes)} bytes, 최대 72 bytes)"
            )

        # 3. Check if user exists
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            raise HTTPException(status_code=409, detail="이미 존재하는 사용자명입니다")

        # 4. Create user with role=USER (admin is created via bootstrap)
        hashed = get_password_hash(plain_password)

        new_user = User(
            username=username,
            hashed_password=hashed,
            role=UserRole.USER.value
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # 5. Create default folders for new user
        create_default_folders_for_user(db, new_user.id, role=UserRole.USER.value)

        logger.info(f"[Auth] New user registered: {username} (ID: {new_user.id})")

        # 6. Auto-login: generate token
        access_token = create_access_token(
            data={"sub": new_user.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
            role=new_user.role
        )

        return {
            "message": f"회원가입 완료: {username}",
            "user_id": new_user.id,
            "role": new_user.role,
            "access_token": access_token,
            "token_type": "bearer"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Auth] Registration error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.post("/auth/token")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None
):
    """
    Login and get access token (includes role in response).

    [Security] Cancel/Abort 시 인증되지 않은 내부 로직 진입 방지
    [Feature] Admin login triggers avatar service launch in background
    """
    try:
        if not form.username or not form.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username and password are required"
            )

        user = db.query(User).filter(User.username == form.username).first()

        # Validate password length before verification (bcrypt 72-byte limit)
        if user:
            password_bytes = form.password.encode('utf-8')
            if len(password_bytes) > 72:
                logger.warning(f"[Auth] Password too long ({len(password_bytes)} bytes, max 72)")
                raise HTTPException(
                    status_code=400,
                    detail="Password cannot be longer than 72 bytes"
                )

            if form.password.startswith('$2') and len(form.password) > 50:
                logger.warning(f"[Auth] Password appears to be already hashed")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid password format"
                )

        if not user or not verify_password(form.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Bad credentials")

        access_token = create_access_token(
            data={"sub": user.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
            role=user.role
        )

        if not access_token:
            raise AuthenticationCancelledError("Token creation failed")

        # [Admin Hook] Launch avatar service on admin login only when VTuber integration is enabled.
        vtuber_enabled = bool(app_state.settings and getattr(app_state.settings, "vtuber_relay_enabled", 0) == 1)
        avatar_auto_launch_enabled = bool(app_state.settings and getattr(app_state.settings, "avatar_auto_launch_enabled", True))
        if is_admin_user(user) and vtuber_enabled and avatar_auto_launch_enabled:
            logger.info(f"[Auth] Admin login detected: {user.username}")
            if background_tasks:
                background_tasks.add_task(_launch_avatar_on_admin_login, user.username)
            else:
                _launch_avatar_on_admin_login(user.username)

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "role": user.role
        }

    except (KeyboardInterrupt, SystemExit):
        logger.warning("[Auth] Login aborted by user")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login aborted"
        )

    except SQLAlchemyError as e:
        logger.error(f"[Auth] DB error during login: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable"
        )

    except AuthenticationInterruptedError as e:
        logger.warning(f"[Auth] Login interrupted: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login was interrupted"
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"[Auth] Unexpected login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed unexpectedly"
        )


def _client_ip(request: Request) -> str:
    """Get client IP from request (X-Forwarded-For or direct)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


@router.post("/auth/guest-login", response_model=GuestLoginResponse)
async def guest_login(
    body: GuestLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Guest login with access code (limited access).

    Validates guest code, checks usage limits, and returns a temporary token.
    Guest tokens expire in 60 minutes and have limited chat access.
    """
    settings = get_settings()
    expected_code = settings.guest_access_code

    if not expected_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="게스트 로그인이 비활성화되어 있습니다."
        )

    if body.access_code != expected_code:
        logger.warning(f"[Auth] Invalid guest access code attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="잘못된 접속 코드입니다."
        )

    ip_address = _client_ip(request)
    limit = settings.limit_guest

    # Rate limiting: check guest usage limit per day
    allowed, _cnt = check_guest_limit(db, ip_address, limit)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="오늘의 게스트 접속 제한에 도달했습니다. 내일 다시 시도해 주세요."
        )

    # Create guest token (expires in 60 minutes, no DB user)
    guest_token = f"guest_{os.urandom(16).hex()}"

    # Increment usage counter
    increment_guest_usage(db, ip_address)

    expires_in = 60 * 60  # 60 minutes in seconds

    return GuestLoginResponse(
        access_token=guest_token,
        token_type="bearer",
        user_id="guest",
        role="guest",
        expires_in=expires_in
    )


@router.get("/auth/me")
async def get_current_user_info(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get current user information from token."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization.replace("Bearer ", "").strip()

    # Guest token
    if token.startswith("guest_"):
        return {
            "id": 0,
            "username": "Guest",
            "role": "guest",
            "is_guest": True
        }

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        if not username:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_guest": False
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Auth] Token validation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
