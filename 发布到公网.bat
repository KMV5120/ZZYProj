@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 轻量化语言模型 - 发布到公网

set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" for /f "delims=" %%i in ('where python 2^>nul') do set "PY=%%i"
if not exist "%PY%" (
  echo [错误] 没找到 Python。
  pause
  exit /b 1
)

echo [1/2] 启动本地静态服务 http://127.0.0.1:8231 ...
start "" /b "%PY%" -m http.server 8231 --bind 127.0.0.1 --directory site
timeout /t 2 >nul

echo [2/2] 建立公网隧道，请稍等 5~15 秒 ...
echo.
echo ==========================================================
echo   下面的 https://xxxx.lhr.life 就是你的公网链接
echo   复制发给别人即可（他们不填密钥也能点"演示模式"看）
echo   关掉这个窗口 = 链接失效
echo ==========================================================
echo.

ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=20 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:8231 nokey@localhost.run

echo.
echo 隧道已断开。重新运行本文件会得到一个新链接。
pause