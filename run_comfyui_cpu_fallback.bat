@echo off
REM ============================================================================
REM ComfyUI CPU Fallback for RTX 5070 Ti Compatibility Issues
REM ============================================================================
REM
REM Use this script if GPU execution fails with "no kernel image" errors.
REM WARNING: CPU mode is MUCH slower than GPU. This is a temporary workaround
REM until PyTorch officially supports Blackwell (sm_120) architecture.
REM
REM Recommended: Check for PyTorch updates regularly.
REM ============================================================================

REM Prefer external ComfyUI location (junction/clone)
REM - Set MELLOW_COMFYUI_DIR to an absolute path of ComfyUI root.
REM - If not set, fall back to ./ComfyUI next to this script.
if defined MELLOW_COMFYUI_DIR (
    cd /d "%MELLOW_COMFYUI_DIR%"
) else (
    cd /d "%~dp0ComfyUI"
)

if not exist "main.py" (
    echo.
    echo [ERROR] ComfyUI not found.
    echo - Set MELLOW_COMFYUI_DIR to your external ComfyUI folder, or
    echo - Create a junction ./ComfyUI pointing to it.
    echo Current dir: %CD%
    echo.
    pause
    exit /b 1
)

REM Activate the ComfyUI venv if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Launch ComfyUI in CPU mode
python main.py --cpu

echo.
echo ============================================================================
echo ComfyUI ran in CPU mode. This is very slow but works as a fallback.
echo Check https://pytorch.org/ for Blackwell (sm_120) support updates.
echo ============================================================================
pause
