Param(
  # 기본: 현재 PATH의 python
  [string]$PythonExe = "python",

  # 옵션: ComfyUI 폴더를 지정하면, 그 안의 venv python을 우선 사용
  # 예) .\scripts\install_torch_nightly_cu128.ps1 -ComfyUiDir "D:\ComfyUI"
  [string]$ComfyUiDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $ComfyUiDir) {
  $ComfyUiDir = $env:MELLOW_COMFYUI_DIR
}

if ($ComfyUiDir) {
  $venvPy = Join-Path $ComfyUiDir "venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPy) {
    $PythonExe = $venvPy
  }
}

Write-Host "=== Installing PyTorch Nightly (cu128) for Blackwell (sm_120) ==="
Write-Host "Python: $PythonExe"

& $PythonExe -V

Write-Host ""
Write-Host "1) Uninstall existing torch packages (ignore errors)"
try { & $PythonExe -m pip uninstall -y torch torchvision torchaudio } catch { }

Write-Host ""
Write-Host "2) Upgrade pip"
& $PythonExe -m pip install --upgrade pip

Write-Host ""
Write-Host "3) Install nightly CUDA 12.8 build"
& $PythonExe -m pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128

Write-Host ""
Write-Host "4) Sanity check"
& $PythonExe -c @"
import torch
print('torch', torch.__version__)
print('torch.cuda.is_available', torch.cuda.is_available())
print('torch.version.cuda', torch.version.cuda)
if torch.cuda.is_available():
    print('device', torch.cuda.get_device_name(0))
    print('capability', torch.cuda.get_device_capability(0))
    x = torch.randn((1024,1024), device='cuda')
    y = x @ x.t()
    print('matmul ok', y.shape)
"@

Write-Host ""
Write-Host "=== Done ==="

