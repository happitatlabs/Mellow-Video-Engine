"""
Mellow-Link - Admin Router

Endpoints: /admin/launch_avatar
"""

import ctypes
import logging
import os
import subprocess
import sys
import time
import requests
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from mellow_link import app_state
from mellow_link.infra import get_db, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


@router.post("/admin/launch_avatar")
async def launch_avatar(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Admin 전용 아바타 런칭 시스템.

    VTuber 백엔드 서버를 실행하고, Electron 앱을 시작하며, 첫 인사를 전송합니다.
    """
    # 1. 권한 체크
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")

    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", UserRole.USER.value)

        if not username:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        if user.role != UserRole.ADMIN.value:
            raise HTTPException(status_code=403, detail="Admin access required")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # 2. 중복 실행 방지
    if app_state.vtuber_proc is not None and app_state.vtuber_proc.poll() is None:
        return {
            "success": False,
            "message": "VTuber 서버가 이미 실행 중입니다.",
            "pid": app_state.vtuber_proc.pid
        }

    try:
        response = requests.get("http://localhost:12393", timeout=2)
        if response.status_code == 200:
            return {
                "success": False,
                "message": "VTuber 서버가 이미 실행 중입니다 (포트 12393 활성)."
            }
    except requests.RequestException:
        pass

    # 3. 백엔드 실행
    project_root = os.environ.get("MELLOW_LINK_PROJECT_ROOT")
    if not project_root:
        raise HTTPException(status_code=500, detail="MELLOW_LINK_PROJECT_ROOT 환경 변수가 설정되지 않았습니다.")

    project_root_path = Path(project_root)
    vtuber_dir = project_root_path / "Open-LLM-VTuber"

    if not vtuber_dir.exists():
        raise HTTPException(status_code=404, detail=f"Open-LLM-VTuber 디렉토리를 찾을 수 없습니다: {vtuber_dir}")

    vtuber_cwd = str(vtuber_dir.absolute())
    vtuber_script_path = vtuber_dir / "run_server.py"

    if not vtuber_script_path.exists():
        raise HTTPException(status_code=404, detail=f"run_server.py를 찾을 수 없습니다: {vtuber_script_path}")

    try:
        env = os.environ.copy()
        python_exe = sys.executable

        logger.info(f"[Admin] VTuber 백엔드 실행 시작: {python_exe} {vtuber_script_path}")
        logger.info(f"[Admin] Working directory: {vtuber_cwd}")

        app_state.vtuber_proc = subprocess.Popen(
            [python_exe, "run_server.py"],
            cwd=vtuber_cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        logger.info(f"[Admin] VTuber 프로세스 시작됨 (PID: {app_state.vtuber_proc.pid})")

        time.sleep(1.0)
        if app_state.vtuber_proc.poll() is not None:
            stdout, _ = app_state.vtuber_proc.communicate(timeout=2)
            error_msg = stdout[-500:] if stdout else "Unknown error"
            logger.error(f"[Admin] VTuber 프로세스가 즉시 종료됨: {error_msg}")
            app_state.vtuber_proc = None
            raise HTTPException(
                status_code=500,
                detail=f"VTuber 서버 시작 실패: {error_msg[-200:]}"
            )

    except Exception as e:
        app_state.vtuber_proc = None
        logger.error(f"[Admin] VTuber 서버 시작 중 오류: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"VTuber 서버 시작 실패: {str(e)}")

    # 4. Health Check (최대 15초 대기)
    logger.info("[Admin] VTuber 서버 Health Check 시작...")
    max_wait_time = 15.0
    check_interval = 0.5
    start_time = time.time()
    server_ready = False

    while time.time() - start_time < max_wait_time:
        try:
            response = requests.get("http://localhost:12393", timeout=2)
            if response.status_code == 200:
                server_ready = True
                logger.info(f"[Admin] VTuber 서버 준비 완료 (대기 시간: {time.time() - start_time:.1f}초)")
                break
        except requests.RequestException:
            pass
        time.sleep(check_interval)

    if not server_ready:
        logger.warning("[Admin] VTuber 서버 Health Check 타임아웃 (15초)")

    # 5. Electron 앱 실행 (경로는 설정/환경변수 사용)
    from mellow_link.config import get_settings
    _st = get_settings()
    exe_path_str = getattr(_st, "avatar_electron_exe", None) or os.environ.get("MELLOW_AVATAR_ELECTRON_EXE", "")
    electron_path = Path(exe_path_str) if exe_path_str else None
    electron_launched = False

    if electron_path and electron_path.exists():
        logger.info(f"[Admin] 🎯 타겟 확인됨: {electron_path}")

    if electron_path:
        try:
            exe_name = "open-llm-vtuber-electron.exe"
            logger.info(f"[Admin] 좀비 프로세스 정리 시작: {exe_name}")

            kill_result = subprocess.run(
                ["taskkill", "/F", "/IM", exe_name],
                capture_output=True,
                timeout=10
            )

            if kill_result.returncode == 0:
                logger.info(f"[Admin] 기존 Electron 프로세스 종료됨")
                time.sleep(1.0)
            else:
                logger.info("[Admin] 종료할 Electron 프로세스 없음 (정상)")
        except subprocess.TimeoutExpired:
            logger.warning("[Admin] taskkill 타임아웃 - 계속 진행")
        except Exception as e:
            logger.warning(f"[Admin] 좀비 프로세스 정리 중 오류 (무시): {e}")

    if electron_path and electron_path.exists():
        try:
            logger.info(f"[Admin] 🧹 좀비 프로세스 정리 중...")
            subprocess.run("taskkill /F /IM open-llm-vtuber-electron.exe /T",
                         shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            logger.info(f"[Admin] 🚀 앱 실행 시도 (Clean Env): {electron_path}")

            clean_env = os.environ.copy()
            pop_keys = ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ELECTRON_RUN_AS_NODE"]
            for key in pop_keys:
                if key in clean_env:
                    clean_env.pop(key)
                    logger.debug(f"   - 환경 변수 제거: {key}")

            electron_working_dir = os.path.dirname(electron_path)
            DETACHED_PROCESS = 0x00000008

            subprocess.Popen(
                [str(electron_path)],
                cwd=electron_working_dir,
                env=clean_env,
                creationflags=DETACHED_PROCESS | subprocess.CREATE_NEW_CONSOLE,
                shell=False
            )

            electron_launched = True
            logger.info("[Admin] 실행 명령 전송 완료 (독립 프로세스)!")

        except Exception as e:
            logger.error(f"[Admin] ❌ 실행 중 에러 발생: {e}")
            import traceback
            traceback.print_exc()
    else:
        logger.warning("[Admin] Electron 경로를 찾지 못했습니다.")

    # 6. 첫 인사(TTS)
    if server_ready:
        try:
            tts_text = "판돈은 준비됐나? 내가 왔어, 친구."
            logger.info(f"[Admin] 첫 인사 TTS 전송: {tts_text}")

            tts_response = requests.post(
                "http://localhost:12393/api/speak",
                json={"text": tts_text},
                timeout=5
            )

            if tts_response.status_code == 200:
                logger.info("[Admin] 첫 인사 TTS 전송 성공")
            else:
                logger.warning(f"[Admin] 첫 인사 TTS 전송 실패: {tts_response.status_code}")
        except Exception as e:
            logger.warning(f"[Admin] 첫 인사 TTS 전송 중 오류: {e}")

    return {
        "success": True,
        "message": "VTuber 아바타가 성공적으로 실행되었습니다.",
        "pid": app_state.vtuber_proc.pid,
        "server_ready": server_ready,
        "electron_launched": electron_launched
    }
