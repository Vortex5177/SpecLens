# AI Code Reviewer 开发环境一键启动脚本（PowerShell）
# 用法：在项目根目录执行 .\dev.ps1
# 说明：同时启动后端（8000）与前端（5173），按 Ctrl+C 终止

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== 启动后端 (FastAPI, :8000) ===" -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; .\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000"

Write-Host "=== 启动前端 (Vite, :5173) ===" -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev"

Write-Host "两个服务窗口已打开，前端访问地址：http://localhost:5173" -ForegroundColor Green
