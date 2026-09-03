@echo off
rem 手动生长循环.bat — 一键跑完整生长循环（V2卡5）
rem 随时双击：①增量导入→②体检→③规划器→④执行器(预览)→⑤审核同步→⑥矛盾复核
rem 想自动执行高置信任务：把下面命令改成  uv run python growth_loop.py --apply
chcp 65001 >nul
cd /d E:\ai-learn\rag_kb
echo ============================================
echo   ? 自生长知识库 - 手动生长循环
echo   跑完整一圈：导入→体检→规划→执行→审核同步
echo ============================================
uv run python growth_loop.py
echo.
echo ? 生长循环完成（上方为总结报告）
echo    想自动执行任务请运行: uv run python growth_loop.py --apply
pause
