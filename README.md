# rag_kb — 自生长知识库系统（V2，2026-09-04）

个人知识库的**半自生长引擎**：挂在 `E:\自生长知识库`（Obsidian）上，把笔记编译成可检索向量库 + 自动体检/规划/起草/审核闭环。

**技术栈**：SQLite(元数据+tasks) + Chroma(向量) + DeepSeek(问答/规划/起草) + 硅基流动 bge-m3/bge-reranker + FastAPI + Streamlit

## 架构（V1 底座 + V2 生长引擎）

```
E:\ai-learn\rag_kb\
├── config.py            # 集中配置：路径/阈值/API/草稿目录
├── ingest.py            # 增量导入器：hash 对比 → 只处理变化（幂等）
├── extractors.py        # 多模态提取：md/pdf/docx/图片OCR；frontmatter跳过+摘要块
├── directions.yaml/py   # 生长方向规则分类（V1卡4）
├── ask_kb.py            # RAG 问答核心：粗筛→rerank→DeepSeek→缓存
├── api.py / app.py      # FastAPI 后端 / Streamlit 前端（三层架构）
│
├── health_check.py      # 体检（V2卡1）：失效链接/孤立页/词典一致性/过期；2026-09-15 精度修复
├── planner.py           # 规划器（V2卡2）：5路信号→LLM任务清单→入库去重
├── executor.py          # 执行器（V2卡3）：dry-run安全闸+备份回滚+草稿起草+AI日志
├── review_sync.py       # 拖拽同步器（V2卡4）：草稿去向→自动approved/rejected
├── growth_loop.py       # 生长循环闭环（V2卡5）：①导入②体检③规划④执行⑤同步⑥复核⑦写《生长摘要》
│
├── kb_db/               # Chroma 向量库（重建生成，不入 git）
├── kb_meta.db           # SQLite：notes + tasks 任务表 + task_undo 备份
├── 启动知识库问答.bat    # 问答界面（后端8100+前端8501，自动开浏览器）
├── 手动导入.bat          # 立即增量导入
├── 手动生长循环.bat      # 菜单式一键生长循环（1 预览 / 2 自动高置信）
├── ingest_scheduled.bat / growth_scheduled.bat   # 定时任务入口
├── install_scheduled_task.bat  # 注册 每日导入/每周生长循环（可关）
├── 使用说明.txt          # 用户使用说明（GBK，双击即看）
└── test_*.py (12个)     # 37 测试全绿
```

## 快速开始

```bash
# 1. 一键问答（后端+前端，浏览器自动开 localhost:8501）
双击 启动知识库问答.bat

# 2. 一键生长循环（导入→体检→规划→执行预览→审核同步→总结）
双击 手动生长循环.bat          # 菜单：1 预览 / 2 自动高置信；或 uv run python growth_loop.py
#    自动执行高置信任务（改前备份，可回滚）：
uv run python growth_loop.py --apply

# 3. 单步命令
uv run python ingest.py                    # 增量导入
uv run python health_check.py              # 体检报告
uv run python planner.py                   # 规划器（列任务）
uv run python review_sync.py               # 审核同步（拖拽感知）

# 4. 全量测试
uv run pytest                              # 37 passed
```

## 使用方式（详见 使用说明.txt）

| 你做什么 | 系统自动做什么 |
|---|---|
| Obsidian 写笔记 | 22:00 增量导入+分类（幂等零费用） |
| 双击手动生长循环.bat | 体检→规划→执行→审核同步完整一圈 |
| 拖 raw/_草稿/X.md 到 concepts/ | review_sync 自动标 approved + 清待审核 tag + 写 log |
| 删除 raw/_草稿/X.md | 自动标 rejected（去重记住，不再生成） |
| 每天 22:00（定时任务 rag_kb_growth） | 完整生长循环（含增量导入）→ 更新《生长摘要.md》 |

## 定时任务管理

```bash
schtasks /query /tn rag_kb_growth          # 每日22:00 生长循环（含增量导入；2026-09-06 起每日）
schtasks /query /tn rag_kb_ingest          # 已停用（保留备用：单独跑增量导入；2026-09-06 停）
schtasks /change /tn <任务名> /disable     # 停用（enable 恢复）
schtasks /delete /tn <任务名> /f           # 删除
```

## RAG 问答链路（V1 核心，2026-08-10 课程实测 ✅）

```
笔记/PDF/图片 → ingest 导入
  ① 切片：md 按 ## 标题切块（跳过 frontmatter，<30字符丢弃）；PDF 按页；docx 按标题；图片本地 OCR
  ② 向量化：硅基流动 bge-m3（1024维，批量10条）
  ③ 存储：Chroma PersistentClient（kb_db/，cosine）

提问 → ask_kb.py
  ④ 粗筛：bge-m3 检索 top-30（距离 ≤0.75）
  ⑤ 精排：bge-reranker-v2-m3 重排 top-7（相关度 ≥0.25，2026-09-11 调优）——必须配，单独检索会"检不全"
  ⑥ 生成：DeepSeek 结构化 JSON（response_format=json_object）+ 程序二次校验
  ⑦ 回答分两层：📌资料层（忠实知识库+标来源）/ 💡补充层（AI通用知识，标注区分不混淆）
  ⑧ 缓存：qa_cache.json 相同问题零费用秒回；限流 60秒10次
  ⑨ 防幻觉：没检索到 → 明确提示不编造（测试锁死：埃菲尔铁塔类问题必须拒绝）
```

关键参数集中在 `config.py`：`RETRIEVE_TOP=30 / RETRIEVE_MAX_DISTANCE=0.75 / RERANK_K=7 / RERANK_MIN_SCORE=0.25`（后两个为 2026-09-11 调优值）
（⚠️ 阈值按知识库规模调，勿照抄死值——知识库 139→206 块时实测 top-3 不够调到 top-5）

## 关键设计

- **安全三件套**：默认 dry-run、改前备份（task_undo 可回滚）、detail 模糊拒绝执行
- **草稿隔离**：new_knowledge 起草到 `raw/_草稿/`（ingest 排除，不进向量库，审核转正）
- **幂等**：导入/规划/同步重复跑零副作用（0.2s 零 API 费）
- **AI 自动日志**：执行器/同步器每步操作写知识库 log.md（做了什么/放哪了）
- **防幻觉**：问答两层（📌资料+💡补充标注区分）；起草必须结合素材，硬数据不编造
- **成本管控**：qa_cache 相同问题零费用；一次生长循环约 1 次规划 LLM 调用

## 测试（37 passed）

- V1：ingest 增量 / multimodal 多模态 / classify 分类 / api / rag / app / acceptance 验收
- V2：health 体检 / planner 规划（假LLM注入+真实端到端）/ executor 执行（备份回滚+草稿）/ review_sync 拖拽同步 / growth_loop 闭环冒烟

## 配置

所有参数集中 `config.py`：导入目录/排除目录（raw/_草稿）/阈值/API Key（`.env`：SILICONFLOW_API_KEY / DEEPSEEK_API_KEY）。
⚠️ 后端端口 8100（8000 被 ESP32 录音服务器占用，勿改回）。
