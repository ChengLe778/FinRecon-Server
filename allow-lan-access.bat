@echo off
rem 一键：放行 8000 端口供局域网访问（需右键以管理员身份运行）: allow inbound TCP port 8000 for LAN sharing (requires admin)
netsh advfirewall firewall add rule name="FinRecon-8000" dir=in action=allow protocol=TCP localport=8000
if errorlevel 1 (
    echo.
    echo [失败] 请右键本文件，选择「以管理员身份运行」。
) else (
    echo.
    echo [成功] 防火墙规则已添加，局域网同事现在可以访问：
    echo        http://%COMPUTERNAME%:8000  或  http://本机IP:8000
)
echo.
pause
