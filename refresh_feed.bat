@echo off
REM ============================================================
REM  潮汐 · 一键刷新资讯（财联社快讯 + 雪球股民跟踪）
REM  用法：双击本文件，或在终端执行 refresh_feed.bat
REM ============================================================
cd /d "%~dp0"

echo [1/2] 抓取财联社 + 雪球 ...
"C:\Users\zhouy\.workbuddy\binaries\python\versions\3.13.12\python.exe" fetch_feed.py
if errorlevel 1 goto :err

echo [2/2] 合并为 feed_data.json ...
"C:\Users\zhouy\.workbuddy\binaries\python\versions\3.13.12\python.exe" build_feed.py
if errorlevel 1 goto :err

echo.
echo 完成。刷新浏览器即可看到最新资讯。
pause
exit /b 0

:err
echo.
echo 抓取或合并失败，请检查网络。
pause
exit /b 1
