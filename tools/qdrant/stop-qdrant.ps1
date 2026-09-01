# 停止本地运行的 Qdrant。
# 用法：在任意终端执行 .\tools\qdrant\stop-qdrant.ps1

$procs = Get-Process -Name "qdrant" -ErrorAction SilentlyContinue
if (-not $procs) {
    Write-Output "没有正在运行的 qdrant 进程。"
    exit 0
}

$procs | ForEach-Object {
    Write-Output "正在停止 qdrant (PID $($_.Id)) ..."
    Stop-Process -Id $_.Id -Force
}
Write-Output "Qdrant 已停止。"
