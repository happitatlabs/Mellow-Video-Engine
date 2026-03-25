"""
Security Module for Mellow-Link

Provides admin account bootstrapping and security utilities.
"""

import hashlib
import base64
import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

from mellow_link.infra.database import (
    User, UserRole, SessionLocal,
    get_password_hash, create_default_folders_for_user
)

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Admin credentials: 환경 변수로만 설정. 기본 비밀번호 없음 (핫픽: 미설정 시 부트스트랩 실패)
DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
# ADMIN_PASSWORD 미설정 시 초기 관리자 생성하지 않음 (보안 권장)
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()


# =============================================================================
# Safe Password Hashing (bcrypt 72-byte limit handling)
# =============================================================================

def _safe_password_for_bcrypt(password: str) -> str:
    """
    Ensure password is safe for bcrypt (within 72-byte limit).

    If password exceeds 72 bytes, apply SHA-256 pre-hashing.
    This is a standard industry practice used by Django, passlib-extras, etc.

    Args:
        password: Plain text password (any length)

    Returns:
        Password string safe for bcrypt (always <= 72 bytes)
    """
    if not isinstance(password, str):
        password = str(password)

    # Check byte length (bcrypt's limit is on bytes, not characters)
    password_bytes = password.encode('utf-8')

    if len(password_bytes) <= 72:
        return password

    # Password exceeds 72 bytes - apply SHA-256 pre-hashing
    # SHA-256 produces 32 bytes, base64 encodes to 44 characters (always safe)
    logger.info(
        f"[Security] Password exceeds 72 bytes ({len(password_bytes)} bytes). "
        f"Applying SHA-256 pre-hashing for bcrypt compatibility."
    )
    sha256_hash = hashlib.sha256(password_bytes).digest()
    return base64.b64encode(sha256_hash).decode('ascii')


def safe_get_password_hash(password: str) -> str:
    """
    Safely hash a password, handling bcrypt's 72-byte limit.

    This is a wrapper around database.get_password_hash that ensures
    the password is safe for bcrypt before hashing.

    Args:
        password: Plain text password (any length supported)

    Returns:
        Bcrypt hashed password string
    """
    # First, ensure password is safe for bcrypt
    safe_password = _safe_password_for_bcrypt(password)

    # Then use the standard hashing function from database.py
    return get_password_hash(safe_password)


# =============================================================================
# Admin Bootstrapping
# =============================================================================

def check_admin_exists(db: Session) -> bool:
    """
    Check if any admin account exists in the database.

    Args:
        db: SQLAlchemy database session

    Returns:
        True if at least one admin user exists, False otherwise.
    """
    admin_user = db.query(User).filter(User.role == UserRole.ADMIN.value).first()
    return admin_user is not None


def get_admin_user(db: Session, username: Optional[str] = None) -> Optional[User]:
    """
    Get an admin user from the database.

    Args:
        db: SQLAlchemy database session
        username: Optional specific username to look for. If None, returns any admin.

    Returns:
        User object if found, None otherwise.
    """
    query = db.query(User).filter(User.role == UserRole.ADMIN.value)

    if username:
        query = query.filter(User.username == username)

    return query.first()


def create_admin_user(
    db: Session,
    username: str = DEFAULT_ADMIN_USERNAME,
    password: str = DEFAULT_ADMIN_PASSWORD,
    create_folders: bool = True
) -> Optional[User]:
    """
    Create a new admin user in the database.

    This function handles passwords of ANY length gracefully using SHA-256
    pre-hashing when the password exceeds bcrypt's 72-byte limit.
    The server will NEVER crash due to password length issues.

    Args:
        db: SQLAlchemy database session
        username: Admin username
        password: Plain text password (will be hashed with bcrypt)
        create_folders: Whether to create default folders for the admin

    Returns:
        Created User object, or None if creation failed.
    """
    try:
        # Check if username already exists
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            logger.warning(f"[Security] User '{username}' already exists")

            # If existing user is not admin, upgrade to admin
            if existing_user.role != UserRole.ADMIN.value:
                logger.info(f"[Security] Upgrading user '{username}' to admin role")
                existing_user.role = UserRole.ADMIN.value
                db.commit()
                db.refresh(existing_user)
                return existing_user

            return existing_user

        # =================================================================
        # Double Hashing Prevention
        # =================================================================
        if password.startswith('$2') and len(password) >= 50:
            logger.warning(
                f"[Security] Password appears to be already hashed. "
                f"Using as-is (not recommended for admin creation)."
            )
            hashed_password = password
        else:
            # =================================================================
            # Safe Password Hashing (handles any length gracefully)
            # Uses safe_get_password_hash which applies SHA-256 pre-hashing
            # for passwords exceeding bcrypt's 72-byte limit
            # =================================================================
            hashed_password = safe_get_password_hash(password)
            logger.debug(f"[Security] Password hashed successfully (length: {len(hashed_password)})")

        # Create admin user
        admin_user = User(
            username=username,
            hashed_password=hashed_password,
            role=UserRole.ADMIN.value
        )

        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)

        logger.info(f"[Security] Admin user '{username}' created successfully (ID: {admin_user.id})")

        # Create default folders for admin
        if create_folders:
            try:
                create_default_folders_for_user(db, admin_user.id)
                logger.info(f"[Security] Default folders created for admin user")
            except Exception as e:
                logger.warning(f"[Security] Failed to create default folders: {e}")

        return admin_user

    except Exception as e:
        logger.error(f"[Security] Failed to create admin user: {e}")
        import traceback
        logger.error(f"[Security] Traceback: {traceback.format_exc()}")
        db.rollback()
        return None


def bootstrap_admin_account() -> bool:
    """
    Bootstrap the admin account on application startup.

    This function NEVER crashes the server. It handles passwords of any length
    gracefully using SHA-256 pre-hashing when needed (via safe_get_password_hash).

    This function:
    1. Checks if any admin account exists
    2. If not, creates a default admin account with:
       - Username: "admin" (or from ADMIN_USERNAME env var)
       - Password: "mellow1234" (or from ADMIN_PASSWORD env var)
       - Role: superuser (admin)

    Returns:
        True if admin exists or was created successfully, False on error.
        NOTE: Even if this returns False, the server should NOT crash.
    """
    db = None
    try:
        db = SessionLocal()

        # Ensure DB is initialized
        try:
            from mellow_link.infra.database import init_db
            init_db()
            logger.debug("[Security] Database initialized")
        except Exception as db_err:
            logger.warning(f"[Security] DB init check failed (may already be initialized): {db_err}")

        # Check if admin already exists
        if check_admin_exists(db):
            admin = get_admin_user(db)
            if admin:
                logger.info(f"[Security] Admin account already exists: '{admin.username}' (ID: {admin.id})")
            return True

        # No admin exists - ADMIN_PASSWORD 미설정 시 기동 실패 (핫픽: 기본 비밀번호 제거)
        if not DEFAULT_ADMIN_PASSWORD:
            logger.error(
                "[Security] ADMIN_PASSWORD가 설정되지 않았습니다. "
                "초기 관리자 계정을 만들려면 .env 또는 환경 변수에 ADMIN_PASSWORD를 설정한 뒤 서버를 재시작하세요."
            )
            raise RuntimeError(
                "ADMIN_PASSWORD 미설정. 관리자 계정이 없고 기본 비밀번호를 사용하지 않습니다. "
                "환경 변수 ADMIN_PASSWORD를 설정 후 재시작하세요."
            )

        logger.info("[Security] No admin account found. Creating default admin...")
        logger.info(f"[Security]   Username: {DEFAULT_ADMIN_USERNAME}")

        # Log password length info
        password_bytes = DEFAULT_ADMIN_PASSWORD.encode('utf-8')
        password_byte_length = len(password_bytes)
        if password_byte_length > 72:
            logger.info(
                f"[Security]   Password length: {password_byte_length} bytes (exceeds 72-byte limit, "
                f"SHA-256 pre-hashing will be applied automatically)"
            )
        else:
            logger.info(f"[Security]   Password length: {password_byte_length} bytes (OK)")

        # Create admin using safe_get_password_hash (handles any password length)
        admin = create_admin_user(
            db,
            username=DEFAULT_ADMIN_USERNAME,
            password=DEFAULT_ADMIN_PASSWORD,
            create_folders=True
        )

        if admin:
            logger.info(f"[Security] Default admin account created successfully")
            logger.info(f"[Security]   Username: {DEFAULT_ADMIN_USERNAME}")
            logger.info(f"[Security]   User ID: {admin.id}")
            logger.warning("[Security]   ** Please change the default password after first login! **")
            return True
        else:
            logger.error("[Security] Failed to create default admin account")
            return False

    except Exception as e:
        logger.error(f"[Security] Error during admin bootstrapping: {e}")
        import traceback
        logger.error(f"[Security] Traceback: {traceback.format_exc()}")
        # Return False but DON'T crash the server
        return False

    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def is_admin_user(user: User) -> bool:
    """
    Check if a user has admin privileges.

    Args:
        user: User object to check

    Returns:
        True if user is an admin, False otherwise.
    """
    return user.role == UserRole.ADMIN.value


def is_superuser(user: User) -> bool:
    """
    Alias for is_admin_user for compatibility.

    Args:
        user: User object to check

    Returns:
        True if user is a superuser (admin), False otherwise.
    """
    return is_admin_user(user)
