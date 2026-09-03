@echo off
rem install_scheduled_task.bat — 定时任务注册（V1卡5 + V2卡5 扩展）
rem 可注册两个任务（任选，都可单独关）：
rem   A. rag_kb_ingest   每天 22:00  增量导入（新笔记自动入库）
rem   B. rag_kb_growth   每周日 22:30 完整生长循环（导入+体检+规划+执行+审核同步）
chcp 65001 >nul
cd /d E:\ai-learn\rag_kb
echo ============================================
echo   ? 定时任务注册（选要装的，输入对应数字回车）
echo   1. 每日22:00 增量导入（rag_kb_ingest）
echo   2. 每周日22:30 生长循环（rag_kb_growth）
echo   3. 两个都装
echo   0. 跳过
echo ============================================
set /p choice=请输入数字:

if "%choice%"=="1" goto install_ingest
if "%choice%"=="2" goto install_growth
if "%choice%"=="3" goto install_both
if "%choice%"=="0" goto end
echo 无效输入，退出
goto end

:install_ingest
schtasks /create /tn "rag_kb_ingest" /tr "E:\ai-learn\rag_kb\ingest_scheduled.bat" /sc daily /st 22:00 /f
echo [OK] 每日22:00增量导入已注册
goto end

:install_growth
schtasks /create /tn "rag_kb_growth" /tr "E:\ai-learn\rag_kb\growth_scheduled.bat" /sc weekly /d SUN /st 22:30 /f
echo [OK] 每周日22:30生长循环已注册
goto end

:install_both
call :install_ingest
call :install_growth
goto end

:end
echo.
echo 管理命令：
echo   停用:   schtasks /change /tn 任务名 /disable
echo   启用:   schtasks /change /tn 任务名 /enable
echo   删除:   schtasks /delete /tn 任务名 /f
pause
