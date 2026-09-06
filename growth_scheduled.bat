@echo off
rem growth_scheduled.bat — 每周日22:30 生长循环入口（V2 稳定性补丁：失败重试+标记）
cd /d E:\ai-learn\rag_kb

echo [%date% %time%] ==== 定时生长循环开始 ==== >> growth.log
call :run_with_retry uv run python growth_loop.py >> growth.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] FAILED 三次尝试均失败，写入标记文件 >> growth.log
    echo [%date% %time%] FAILED > growth_failed.flag
) else (
    del growth_failed.flag 2>nul
)
echo [%date% %time%] ==== 结束 ==== >> growth.log
exit /b 0

:run_with_retry
call %*
if not errorlevel 1 exit /b 0
echo [%date% %time%] 失败，30秒后重试(1/3) >> growth.log
timeout /t 30 /nobreak >nul
call %*
if not errorlevel 1 exit /b 0
echo [%date% %time%] 失败，30秒后重试(2/3) >> growth.log
timeout /t 30 /nobreak >nul
call %*
exit /b %errorlevel%
