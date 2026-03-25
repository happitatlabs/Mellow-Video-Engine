# Ollama GPU 모드 재시작 스크립트
# RTX 5070 Ti 및 최신 GPU 지원

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Ollama GPU 모드 재시작" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 현재 실행 중인 Ollama 프로세스 종료
Write-Host "[1/4] 실행 중인 Ollama 프로세스 종료 중..." -ForegroundColor Yellow
$ollamaProcesses = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($ollamaProcesses) {
    Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "✓ Ollama 프로세스 종료 완료" -ForegroundColor Green
} else {
    Write-Host "✓ 실행 중인 Ollama 프로세스 없음" -ForegroundColor Green
}

# 2. GPU 환경변수 설정
Write-Host ""
Write-Host "[2/4] GPU 환경변수 설정 중..." -ForegroundColor Yellow
$env:OLLAMA_GPU_LAYERS = "99"
$env:OLLAMA_NUM_GPU = "1"
Write-Host "✓ OLLAMA_GPU_LAYERS = 99" -ForegroundColor Green
Write-Host "✓ OLLAMA_NUM_GPU = 1" -ForegroundColor Green

# 3. CUDA 경로 확인 (선택사항)
Write-Host ""
Write-Host "[3/4] CUDA 환경 확인 중..." -ForegroundColor Yellow
$cudaPath = $env:CUDA_PATH
if ($cudaPath) {
    Write-Host "✓ CUDA_PATH = $cudaPath" -ForegroundColor Green
} else {
    Write-Host "⚠ CUDA_PATH가 설정되지 않음 (선택사항)" -ForegroundColor Yellow
}

# 4. Ollama 서버 시작
Write-Host ""
Write-Host "[4/4] Ollama 서버 시작 중..." -ForegroundColor Yellow
Write-Host ""

# 백그라운드에서 Ollama 서버 시작
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden

# 서버가 시작될 때까지 대기
Start-Sleep -Seconds 3

# 서버 상태 확인
Write-Host ""
Write-Host "Ollama 서버 상태 확인 중..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -Method GET -TimeoutSec 5 -ErrorAction Stop
    Write-Host "✓ Ollama 서버가 정상적으로 실행 중입니다!" -ForegroundColor Green
} catch {
    Write-Host "⚠ Ollama 서버 연결 실패. 수동으로 확인해주세요." -ForegroundColor Red
    Write-Host "  명령어: ollama serve" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "다음 단계:" -ForegroundColor Cyan
Write-Host "1. 새 PowerShell 창에서 다음 명령어 실행:" -ForegroundColor White
Write-Host "   ollama run qwen2.5:7b --verbose" -ForegroundColor Yellow
Write-Host ""
Write-Host "2. GPU 사용 여부 확인:" -ForegroundColor White
Write-Host "   - VRAM 사용량이 0GB가 아닌지 확인" -ForegroundColor Yellow
Write-Host "   - nvidia-smi로 GPU 사용률 확인" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
