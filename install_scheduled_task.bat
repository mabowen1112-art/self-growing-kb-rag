@echo off
rem install_scheduled_task.bat — 一键注册"每日22:00自动增量导入"任务计划（卡5）
rem 运行一次即可；改时间改下面的 /st 参数（24小时制，如 22:00 / 08:30）
chcp 65001 >nul
cd /d E:\ai-learn\rag_kb

echo ============================================
echo    注册定时任务：每天 22:00 自动增量导入
echo   触发内容: 新笔记自动分类入库 / 变更替换 / 删除移除
echo   日志位置: E:\ai-learn\rag_kb\ingest.log
echo ============================================

schtasks /create /tn "rag_kb_ingest" /tr "E:\ai-learn\rag_kb\ingest_scheduled.bat" /sc daily /st 22:00 /f

if %errorlevel%==0 (
    echo.
    echo [OK] 定时任务已注册：每天 22:00 自动运行（后台静默，不弹窗）
    echo    验证: schtasks /query /tn rag_kb_ingest
    echo    手动触发测试: schtasks /run /tn rag_kb_ingest
    echo    删除任务: schtasks /delete /tn rag_kb_ingest /f
) else (
    echo.
    echo [FAIL] 注册失败，请检查权限（管理员或当前用户权限）
)
pause
