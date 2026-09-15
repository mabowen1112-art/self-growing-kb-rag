@echo off
rem ============================================================
rem Manual growth loop (menu). Rewritten 2026-09-15:
rem   (1) fixed garbled header -> UTF-8 + "chcp 65001" on line 2
rem   (2) added menu (previously preview-only, no options)
rem Encoding: this file = UTF-8 + CRLF. Rewrite it via Python;
rem   never re-save with Notepad (it changes the encoding).
rem NOTE: keep all rem/command lines ASCII-only -- cmd in a UTF-8
rem   bat mis-parses CJK symbols (circle numbers etc.) on rem
rem   lines and tries to run them as commands (bug found 09-15).
rem ============================================================
chcp 65001 >nul
cd /d E:\ai-learn\rag_kb
echo ============================================================
echo   自生长知识库 · 手动生长循环
echo ------------------------------------------------------------
echo   1 = 预览（安全：只报告要做什么，不改任何文件）     回车默认
echo   2 = 自动执行高置信任务（起草新页 / 修链接；低置信不动）
echo   3 = 退出
echo ------------------------------------------------------------
echo   提示：过程刷屏很长，最后看【本轮生长总结】那一段就行
echo ============================================================
set "sel="
set /p sel= 请选择 [1/2/3]： 
if "%sel%"=="2" goto auto_safe
if "%sel%"=="3" goto fin
echo.
echo [运行] 预览模式（不改任何文件）...
uv run python growth_loop.py
goto done
:auto_safe
echo.
echo [运行] 自动模式 --auto-safe：高置信任务自动执行，低置信挂起等你...
uv run python growth_loop.py --auto-safe
:done
echo.
echo ------------------------------------------------------------
echo 完成。草稿都在 raw\_草稿\ ：拖进 concepts\ = 通过；删除 = 拒绝。
echo ------------------------------------------------------------
:fin
pause
