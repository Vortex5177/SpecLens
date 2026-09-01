@echo off
rem ============================================================
rem Qdrant 本地启动脚本（Windows 原生，无需 Docker/WSL）
rem 双击即可启动；数据持久化在 tools\qdrant\storage\
rem 停止：关闭本窗口，或在窗口内按 Ctrl+C
rem ============================================================
cd /d "%~dp0"

if not exist "qdrant.exe" (
    echo [错误] 未找到 qdrant.exe！
    echo 请先按 README.md 的步骤下载并解压 qdrant-x86_64-pc-windows-msvc.zip，
    echo 把解压出来的 qdrant.exe 放到本目录（tools\qdrant\）下。
    echo.
    pause
    exit /b 1
)

echo 正在启动 Qdrant...
echo   REST API : http://localhost:6333   （Web UI 同地址）
echo   gRPC     : localhost:6334
echo   存储目录 : %~dp0storage\
echo.

rem 通过环境变量指定存储路径，强制持久化到本地目录，不使用内存模式（其余配置用内置默认值）
set "QDRANT__STORAGE__STORAGE_PATH=%~dp0storage"
qdrant.exe

rem 若进程退出（双击运行时保留窗口便于看错误信息）
pause
