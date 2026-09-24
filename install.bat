@echo off
rem ============================================================
rem  多知识库私有 RAG 问答系统 - 一键安装(仅首次)
rem  自动探测本机 Python(3.10+):
rem    1) 环境变量 PYTHON_EXE(可手动指定)
rem    2) py -3 启动器
rem    3) python 命令
rem  然后创建 .venv 并安装依赖。
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0backend"

set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"

rem ---- 定位系统 Python ----
set "BASE_PY="
if defined PYTHON_EXE (
  set "BASE_PY=%PYTHON_EXE%"
) else (
  py -3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "BASE_PY=py -3"
)
if not defined BASE_PY (
  python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "BASE_PY=python"
)
if not defined BASE_PY (
  echo [ERROR] 未找到 Python 3.10+。
  echo   请安装 Python 3.12(勾选 Add to PATH),或设置环境变量 PYTHON_EXE 指向 python.exe。
  pause
  exit /b 1
)

rem ---- 创建虚拟环境(若不存在) ----
if not exist "%PY%" (
  echo [1/3] 创建 Python 虚拟环境…
  %BASE_PY% -m venv .venv
  if errorlevel 1 goto :err
)

echo [2/3] 升级 pip…
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :err

echo [3/3] 安装依赖(可能需要几分钟,网络慢可换国内镜像)…
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo 安装完成!请双击 start.bat 启动系统。
pause
exit /b 0

:err
echo [ERROR] 安装失败,请检查网络或 Python 环境后重试。
pause
exit /b 1
