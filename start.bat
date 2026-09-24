@echo off
rem ============================================================
rem  多知识库私有 RAG 问答系统 - 启动脚本(守护模式)
rem  双击即可。将自动:
rem    1. 写入/校验 Ollama 显存保护环境变量(用户级,仅首次)
rem    2. 拉起 Ollama(若未运行/僵死),校验模型
rem    3. 启动后端并持续守护:崩溃自动重启、日志落盘 data\logs\
rem  浏览器将自动打开 http://127.0.0.1:8000
rem  关闭本窗口 = 停止系统(建议按 Ctrl+C)
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
set "RESTART_OLLAMA=0"

if not exist "%PY%" (
  echo [ERROR] 未找到虚拟环境,请先运行 install.bat
  pause
  exit /b 1
)

rem ---- 1) Ollama 显存保护环境变量(用户级,永久生效) ----
reg query "HKCU\Environment" /v OLLAMA_MAX_LOADED_MODELS >nul 2>&1
if errorlevel 1 (
  setx OLLAMA_MAX_LOADED_MODELS "2" >nul 2>&1
  set RESTART_OLLAMA=1
)
reg query "HKCU\Environment" /v OLLAMA_KEEP_ALIVE >nul 2>&1
if errorlevel 1 (
  setx OLLAMA_KEEP_ALIVE "10m" >nul 2>&1
)
rem 当前进程同样注入(守护脚本将继承)
set "OLLAMA_MAX_LOADED_MODELS=2"
set "OLLAMA_KEEP_ALIVE=10m"

rem ---- 2) 首次写入变量时,需重启 Ollama 使限制生效 ----
if "%RESTART_OLLAMA%"=="1" (
  echo [提示] 已写入 Ollama 显存保护设置,正在重启 Ollama 使其生效…
  taskkill /IM "ollama app.exe" /F >nul 2>&1
  taskkill /IM "ollama.exe" /F >nul 2>&1
  timeout /t 2 /nobreak >nul
)

rem ---- 3) 打开浏览器(稍等后端就绪) ----
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://127.0.0.1:8000"

rem ---- 4) 启动守护(后端自愈 + Ollama 唤醒 + 日志) ----
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\run_server.ps1"

echo.
echo 系统已停止。日志位于 data\logs\ 目录。
pause
