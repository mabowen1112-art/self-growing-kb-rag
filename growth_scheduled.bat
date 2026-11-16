@echo off
rem growth_scheduled.bat — 每日22:00 生长循环入口（2026-09-06 改：每天跑 + auto-safe）
rem auto-safe = 只自动执行高置信任务（起草/修链接），低置信挂起等人工审核
rem 失败重试 3 次 + FAILED 标记文件
cd /d E:\ai-learn\rag_kb

echo [%date% %time%] ==== 定时生长循环开始 ==== >> growth.log
call :run_with_retry uv run python growth_loop.py --auto-safe >> growth.log 2>&1
set "run_result=%errorlevel%"
if not "%run_result%"=="0" (
    echo [%date% %time%] FAILED 三次尝试均失败，写入标记文件 >> growth.log
    echo [%date% %time%] FAILED > growth_failed.flag
) else (
    del growth_failed.flag 2>nul
)
echo [%date% %time%] ==== 结束 ==== >> growth.log
exit /b %run_result%

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
