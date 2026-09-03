@echo off
rem ingest_scheduled.bat — 定时任务入口（卡5）：静默跑增量导入，输出写 ingest.log
rem 由 Windows 任务计划每天 22:00 调用；不弹窗（用隐藏窗口方式启动）
cd /d E:\ai-learn\rag_kb
echo [%date% %time%] ==== 定时增量导入开始 ==== >> ingest.log
uv run python ingest.py >> ingest.log 2>&1
echo [%date% %time%] ==== 结束 ==== >> ingest.log
