@echo off
chcp 936 >nul
title 应收应付对账系统（SQLite 版）
cd /d "%~dp0"
if not exist "logs" md "logs"

echo ============================================
echo   应收应付对账系统（SQLite 版）
echo ============================================
echo.

rem ---------- 1. 自动探测 Python ----------
set "PY="
if exist "%~dp0runtime\python\python.exe" set "PY=%~dp0runtime\python\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY (
    echo [错误] 没有找到 Python 环境。
    echo.
    echo 任选一种方式解决：
    echo   1. 安装 Python 3.10+（安装时勾选 Add to PATH）
    echo   2. 或改用同目录下的「启动服务.exe」，无需安装 Python
    echo.
    pause
    exit /b 1
)
echo   Python : %PY%

rem ---------- 2. 释放 8000 端口 ----------
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   提示   : 端口 8000 已被占用，正在释放旧服务...
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
    ping -n 3 127.0.0.1 >nul
)

rem ---------- 3. 检查依赖 ----------
echo.
echo [1/3] 检查依赖 ...
"%PY%" -c "import uvicorn, fastapi, sqlalchemy, openpyxl, pandas" >nul 2>&1
if errorlevel 1 (
    echo   正在安装依赖，请稍候（首次启动需要几分钟）...
    "%PY%" -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请手动执行下面这条命令看具体报错：
        echo   "%PY%" -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)
echo   依赖就绪

rem ---------- 4. 初始化数据库 ----------
echo [2/3] 初始化数据库 ...
"%PY%" -c "import sys; sys.path.insert(0,'.'); from app.database import init_db; init_db()" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 数据库初始化失败，请手动执行下面这条命令看具体报错：
    echo   "%PY%" -c "from app.database import init_db; init_db()"
    echo.
    pause
    exit /b 1
)
echo   数据库就绪

rem ---------- 5. 后台启动服务 ----------
echo [3/3] 启动服务 ...
start "FinReconService" /B "%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning > "logs\server.log" 2>&1

rem ---------- 6. 等服务真正起来了，再打开浏览器 ----------
set /a TRY=0
:WAIT
ping -n 2 127.0.0.1 >nul
set /a TRY+=1
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto READY
if %TRY% GEQ 40 (
    echo.
    echo [错误] 服务启动超时，请查看 logs\server.log
    echo.
    pause
    exit /b 1
)
goto WAIT

:READY
ping -n 3 127.0.0.1 >nul
echo.
echo ============================================
echo   服务已启动，浏览器即将打开
echo.
echo   本机访问   : http://localhost:8000
echo   局域网访问 : http://%COMPUTERNAME%:8000
echo.
echo   使用期间请勿关闭本窗口。
echo   用完后按任意键即可停止服务。
echo ============================================
echo.
start "" http://localhost:8000

pause >nul
echo.
echo 正在停止服务 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
ping -n 2 127.0.0.1 >nul
echo 服务已停止。
ping -n 3 127.0.0.1 >nul
