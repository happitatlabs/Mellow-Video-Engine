# Ollama GPU 사용 상태 확인 스크립트

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Ollama GPU 상태 확인" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Ollama 서버 연결 확인
Write-Host "[1/3] Ollama 서버 연결 확인 중..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method GET -TimeoutSec 5 -ErrorAction Stop
    Write-Host "✓ Ollama 서버 연결 성공" -ForegroundColor Green
} catch {
    Write-Host "✗ Ollama 서버에 연결할 수 없습니다." -ForegroundColor Red
    Write-Host "  서버가 실행 중인지 확인해주세요." -ForegroundColor Yellow
    exit 1
}

# 2. 모델 정보 확인
Write-Host ""
Write-Host "[2/3] 모델 정보 확인 중..." -ForegroundColor Yellow
try {
    $models = $response.models
    if ($models.Count -eq 0) {
        Write-Host "⚠ 설치된 모델이 없습니다." -ForegroundColor Yellow
    } else {
        Write-Host "✓ 설치된 모델:" -ForegroundColor Green
        foreach ($model in $models) {
            Write-Host "  - $($model.name)" -ForegroundColor White
        }
    }
} catch {
    Write-Host "⚠ 모델 정보를 가져올 수 없습니다." -ForegroundColor Yellow
}

# 3. GPU 환경변수 확인
Write-Host ""
Write-Host "[3/3] GPU 환경변수 확인 중..." -ForegroundColor Yellow
$gpuLayers = $env:OLLAMA_GPU_LAYERS
$numGpu = $env:OLLAMA_NUM_GPU

if ($gpuLayers) {
    Write-Host "✓ OLLAMA_GPU_LAYERS = $gpuLayers" -ForegroundColor Green
} else {
    Write-Host "⚠ OLLAMA_GPU_LAYERS가 설정되지 않음" -ForegroundColor Yellow
    Write-Host "  .env 파일에서 OLLAMA_GPU_LAYERS=99 설정 확인" -ForegroundColor Yellow
}

if ($numGpu) {
    Write-Host "✓ OLLAMA_NUM_GPU = $numGpu" -ForegroundColor Green
} else {
    Write-Host "⚠ OLLAMA_NUM_GPU가 설정되지 않음" -ForegroundColor Yellow
}

# 4. NVIDIA GPU 확인 (선택사항)
Write-Host ""
Write-Host "[추가] NVIDIA GPU 확인 중..." -ForegroundColor Yellow
try {
    $nvidiaSmi = nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ NVIDIA GPU 정보:" -ForegroundColor Green
        Write-Host "  $nvidiaSmi" -ForegroundColor White
    } else {
        Write-Host "⚠ nvidia-smi를 실행할 수 없습니다." -ForegroundColor Yellow
        Write-Host "  NVIDIA 드라이버가 설치되어 있는지 확인해주세요." -ForegroundColor Yellow
    }
} catch {
    Write-Host "⚠ GPU 정보를 확인할 수 없습니다." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "GPU 사용 여부를 확인하려면:" -ForegroundColor Cyan
Write-Host "1. 모델 실행: ollama run qwen2.5:7b" -ForegroundColor White
Write-Host "2. 다른 터미널에서: nvidia-smi" -ForegroundColor White
Write-Host "3. GPU 메모리 사용량이 증가하는지 확인" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
