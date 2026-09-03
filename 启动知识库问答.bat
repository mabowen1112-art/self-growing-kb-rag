@echo off
chcp 65001 >nul
cd /d E:\ai-learn\rag_kb
echo ============================================
echo   📚 自生长知识库问答 - 三层架构启动
echo   后端 API:  http://localhost:8100
echo   网页界面:  http://localhost:8501
echo   关闭方法:  关掉这两个窗口
echo ============================================
start "rag-kb-api" cmd /k "uv run uvicorn api:app --port 8100"
timeout /t 5 >nul
start "rag-kb-web" cmd /k "uv run streamlit run app.py --server.headless true --server.port 8501"
timeout /t 8 >nul
start "" "http://localhost:8501"
