@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 检查服务是否已在运行
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8765/index.html 2>nul | findstr "200" >nul
if %errorlevel% equ 0 (
    echo [TedLib] server already running
    start "" http://127.0.0.1:8765/index.html
    exit /b 0
)

:: 首次启动
echo [TedLib] starting via uv ...
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

:: 同步依赖 (幂等)
uv sync >nul 2>&1

:: 后台启动 (uv run 会自动用 .venv 里的 python)
start "TED-Server" /min cmd /c "uv run ted_server.py > server.log 2>&1"

:: 等待就绪
:wait
timeout /t 1 /nobreak >nul
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8765/index.html 2>nul | findstr "200" >nul
if %errorlevel% neq 0 goto wait

echo [TedLib] ready at http://127.0.0.1:8765
start "" http://127.0.0.1:8765/index.html
exit /b 0
