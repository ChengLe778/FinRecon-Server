@echo off
chcp 936 >nul
title 清空业务数据
cd /d "%~dp0"

echo ============================================
echo   清空业务数据
echo ============================================
echo.
echo   本操作会删除当前数据库里的全部业务数据：
echo     发票、银行流水、往来单位、对账记录
echo.
echo   删除前会自动备份到 backup\ 目录。
echo   程序本身和费用分类规则不受影响。
echo.

set "DB=%~dp0data\app.db"
if not exist "%DB%" (
    echo   当前没有数据库文件，无需清空。
    echo   下次启动时会自动创建一个空库。
    echo.
    pause
    exit /b 0
)

set /p CONFIRM=   确定要清空吗？输入 Y 回车继续，其它键取消：
if /i not "%CONFIRM%"=="Y" (
    echo.
    echo   已取消，数据未改动。
    echo.
    pause
    exit /b 0
)

if not exist "backup" md "backup"
set "STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
copy /Y "%DB%" "backup\app.db.%STAMP%.bak" >nul
echo.
echo   已备份：backup\app.db.%STAMP%.bak

del /F /Q "%DB%" 2>nul
if exist "%DB%" (
    echo.
    echo [错误] 删除失败，可能有服务正在运行。
    echo       请先关闭对账系统窗口，再重试。
    echo.
    pause
    exit /b 1
)
del /F /Q "%DB%-wal" 2>nul
del /F /Q "%DB%-shm" 2>nul

echo   已清空。下次启动会创建一个全新的空数据库。
echo.
pause
