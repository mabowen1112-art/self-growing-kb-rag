# rag_kb — 自生长知识库问答工具（V1 底座）

个人知识库 RAG 问答工具：把 `E:\自生长知识库` 的 Markdown 笔记编译成可检索的向量库，用 DeepSeek 回答带来源的问题。

**技术栈**：FastAPI(规划) + SQLite(元数据) + Chroma(向量) + 硅基流动 bge-m3/bge-reranker + DeepSeek + Streamlit

## 架构（V1 卡1 已落地：数据层）

```
E:\ai-learn\rag_kb\
├── config.py       # 集中配置：路径/阈值/API（消灭硬编码）
├── ingest.py       # 增量导入器：SQLite hash 对比 → 只处理新增/变更/删除
├── extractors.py   # 多模态提取器：md / pdf / docx / 图片OCR → 统一切片格式
├── directions.yaml # 生长方向配置（加方向只改这里，不改代码）
├── directions.py   # 方向分类器：关键词加权打分 → 补全 frontmatter（卡4）
├── health_check.py # 体检模块（V2卡1）：失效链接/孤立页/词典一致性/过期
├── api.py          # FastAPI 后端：/api/ask /api/health /api/stats（三层架构中间层）
├── ask_kb.py       # RAG 问答核心：粗筛 → Rerank 精排 → DeepSeek（带缓存）
├── app.py          # Streamlit 前端（只调后端 API，不直连 RAG）
├── kb_db/          # Chroma 向量库（本地持久化）
├── kb_meta.db      # SQLite 元数据库（笔记清单 + hash + 状态）
├── test_ingest.py  # 增量导入测试（幂等/新增/修改/删除）
├── test_multimodal.py  # 多模态导入测试（pdf/docx/图片 全管线）
├── test_classify.py    # 方向分类测试（真实库匹配率≥70% + 裸笔记补全）
├── test_api.py     # 后端 API 测试（TestClient）
├── test_rag.py     # 问答链路测试（检索/来源/防幻觉/无泄漏）
├── test_app.py     # 界面无头测试（AppTest，mock 后端）
├── test_acceptance.py  # V1 总验收（10题8对 + 笔记数 + 归类率）
├── ingest_scheduled.bat  # 定时任务入口（卡5）：静默跑 ingest + 写 ingest.log
├── install_scheduled_task.bat  # 一键注册"每日22:00自动导入"任务计划
└── ingest.log      # 定时任务运行日志（自动生成）
```

## 快速开始

```bash
# 0. 增量导入（知识库更新后重跑，只处理变化的部分；pdf/docx/图片放 raw/ 下）
uv run python ingest.py

# 0.5 定时触发（卡5）：注册"每天 22:00 自动增量导入"，之后完全不用管
双击 install_scheduled_task.bat   # 或运行一次即可；日志看 ingest.log

# 1. 一键启动（后端 API + 网页界面，双击 bat 也行）
uv run uvicorn api:app --port 8100        # 终端1：后端（8100：8000 被 ESP32 服务器占用）
uv run streamlit run app.py               # 终端2：前端 → http://localhost:8501

# 2. 问答（命令行，不需要起服务）
uv run python ask_kb.py "复利是什么？"

# 3. 全量测试（导入 + 多模态 + API + 问答 + 界面，真实调 API，约 30-60 秒）
uv run pytest
```

## 关键设计

- **增量导入**：`kb_meta.db` 记录每篇笔记的 sha256 hash，磁盘对比后只向量化变化的部分——知识库随便更新，不重复烧 embedding 钱
- **多模态导入（卡2）**：md / pdf / docx / 图片统一走 `extractors.py`——PDF 按页提取（短页自动合并，扫描件提示走 OCR）、docx 按标题分块、图片用 RapidOCR **本地识别中文（零 API 费）**；`IMPORT_DIRS` 含 `raw/`（原始资料目录）
- **自动打标签/摘要（卡4）**：新笔记缺 frontmatter 时按 `directions.yaml` 方向规则自动补全 `domain`（1个）+ `tags`（≤3个）+ `summary`——关键词加权打分（strong 词 +2、排除词 -3），低置信/混合文档/平局只补 summary 不写 domain（宁缺毋滥）；**加方向只改 yaml 不改代码**；已有字段绝不覆盖
- **稳定 chunk id**：`sha1(相对路径+标题)`，内容不变 id 不变 → 单篇更新/删除精准命中
- **v4 旧库自动迁移**：检测到旧格式库（无元数据）自动全量重建一次，之后纯增量
- **两段式检索**：bge-m3 粗筛 top-30 → bge-reranker 精排 top-5（知识库 200+ 块规模实测，必须配 rerank）
- **成本管控**：qa_cache.json 相同问题零费用；限流 60 秒 10 次
- **防幻觉**：JSON 结构化输出 + 程序二次校验，资料里没有的拒绝回答

## 配置

所有参数集中在 `config.py`：导入目录（默认 concepts/ + entities/）、切片阈值、检索阈值、API Key 从 `.env` 读（`SILICONFLOW_API_KEY` / `DEEPSEEK_API_KEY`）。
