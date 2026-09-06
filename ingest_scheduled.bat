@echo off
rem ingest_scheduled.bat — 增量导入入口（备用）
rem 2026-09-06 起每日生长循环 growth_loop.py 已内含导入步骤，此任务可停用/删除
rem 保留作单独导入用途；失败重试 3 次 + FAILED 标记文件
cd /d E:\ai-learn\rag_kb

echo [%date% %time%] ==== 定时增量导入开始 ==== >> ingest.log
call :run_with_retry uv run python ingest.py >> ingest.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] FAILED 三次尝试均失败，写入标记文件 >> ingest.log
    echo [%date% %time%] FAILED > ingest_failed.flag
) else (
    del ingest_failed.flag 2>nul
)
echo [%date% %time%] ==== 结束 ==== >> ingest.log
exit /b 0

:run_with_retry
call %*
if not errorlevel 1 exit /b 0
echo [%date% %time%] 失败，30秒后重试(1/3) >> ingest.log
timeout /t 30 /nobreak >nul
call %*
if not errorlevel 1 exit /b 0
echo [%date% %time%] 失败，30秒后重试(2/3) >> ingest.log
timeout /t 30 /nobreak >nul
call %*
exit /b %errorlevel%
