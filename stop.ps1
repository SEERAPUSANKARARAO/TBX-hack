param(
    [int]$Port = 8000
)

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Stopping server on port $Port..." -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue

if ($conns) {
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        Write-Host "Killing process PID $procId..." -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Successfully stopped application on port $Port." -ForegroundColor Green
} else {
    Write-Host "No process found listening on port $Port." -ForegroundColor Gray
}
