@echo off
rem ingest_scheduled.bat — 每日22:00 增量导入入口（V2 稳定性补丁：失败重试+标记）
rem 网络瞬时故障（Clash 节点抽风）是定时失败主因 → 失败等 30s 重试，最多 3 次
rem 仍失败 → 写 FAILED 标记文件（手动跑生长循环时会提示）
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
