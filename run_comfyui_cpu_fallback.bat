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

cd /d "%~dp0ComfyUI"

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
