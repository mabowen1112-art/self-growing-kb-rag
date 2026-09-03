@echo off
rem growth_scheduled.bat — 每周生长循环定时入口（V2卡5）：静默跑完整循环，输出写 growth.log
cd /d E:\ai-learn\rag_kb
echo [%date% %time%] ==== 定时生长循环开始 ==== >> growth.log
uv run python growth_loop.py >> growth.log 2>&1
echo [%date% %time%] ==== 结束 ==== >> growth.log
