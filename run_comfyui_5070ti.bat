@echo off
REM ============================================================================
REM ComfyUI Launch Script for RTX 5070 Ti (Blackwell Architecture - sm_120)
REM ============================================================================
REM
REM PyTorch currently does not have native kernel support for sm_120 (Blackwell).
REM These flags help work around compatibility issues:
REM   --force-fp16       : Forces FP16 computation which may have better fallback support
REM   --upcast-sampling  : Upcasts sampling for better compatibility
REM   --cpu-vae          : Optional - runs VAE on CPU if GPU fails
REM
REM If you still encounter "no kernel image" errors, try:
REM   1. Wait for PyTorch to officially support Blackwell (sm_120)
REM   2. Use --cpu flag to run entirely on CPU (very slow)
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

REM Launch ComfyUI with 5070 Ti compatibility flags
python main.py --force-fp16 --lowvram

echo.
echo ============================================================================
echo If you see "no kernel image" errors, the current PyTorch version does not
echo support RTX 5070 Ti (sm_120 / Blackwell architecture) yet.
echo.
echo Options:
echo   1. Wait for official PyTorch Blackwell support
echo   2. Try running with --cpu flag (very slow)
echo   3. Check https://pytorch.org/get-started/locally/ for updates
echo ============================================================================
pause
