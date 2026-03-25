# PowerShell script to check TTFT debug logs
# Usage: .\scripts\check_ttft_logs.ps1

Write-Host "Checking for TTFT debug logs..." -ForegroundColor Cyan

# Check console output (if server is running)
Write-Host "`n=== Recent TTFT_DEBUG logs ===" -ForegroundColor Yellow
Get-Content -Path ".\mellow_link\logs\*.log" -ErrorAction SilentlyContinue | 
    Select-String -Pattern "TTFT_DEBUG|SSE done metadata|Computed effective_mode" | 
    Select-Object -Last 20

Write-Host "`n=== Recent ChatAsk logs ===" -ForegroundColor Yellow
Get-Content -Path ".\mellow_link\logs\*.log" -ErrorAction SilentlyContinue | 
    Select-String -Pattern "ChatAsk.*effective_mode|ChatAsk.*selected_mode" | 
    Select-Object -Last 20

Write-Host "`n=== Checking for log files ===" -ForegroundColor Yellow
Get-ChildItem -Path ".\mellow_link\logs\" -ErrorAction SilentlyContinue | 
    Select-Object Name, Length, LastWriteTime | Format-Table
