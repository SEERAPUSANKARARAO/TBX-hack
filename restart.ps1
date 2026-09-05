param(
    [int]$Port = 8000,
    [switch]$Reload
)

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Restarting server on port $Port..." -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$scriptDir\stop.ps1" -Port $Port

Start-Sleep -Seconds 2

$argsList = @("$scriptDir\run.py", "--port", $Port)
if ($Reload) {
    $argsList += "--reload"
}

Write-Host "`nStarting application: python $($argsList -join ' ')" -ForegroundColor Cyan
python $argsList
