# rag_kb — 自生长知识库（半自生长引擎）

给个人知识库（Obsidian / Markdown 目录）加一层**引擎**：把笔记编译成可检索向量库，再自动体检、规划、起草、审核——让知识库自己往前走，而不是靠你记得去整理。

> 差异化定位：多数工具做的是「**AI 帮你找知识**」，这个做的是「**AI 帮你长知识**」。它不替换你的笔记软件，是挂在上面的增强层。

- 状态：可自用的 V2（生长循环闭环已跑通），个人项目 + 学习载体，不是 SaaS
- 形态：本地运行，单用户，Windows 优先（一键 bat + 计划任务）
- 技术栈：Python / SQLite / Chroma / bge-m3 + bge-reranker（硅基流动）/ DeepSeek / FastAPI / Streamlit

## 它解决什么问题

知识库的典型死法是"越写越乱"：链接失效、页面孤立、概念定义前后不一致、旧结论过期没人发现。人不会天天去查这些，所以需要一个能定期巡检、并**按你定的方向**补内容的机制。

两个边界决定了它长什么样：

- **不做全自生长**：知识自发涌现需要世界模型，那是大模型团队的事；这里做「半自生长」——方向由人定，循环自动化，判断标准兜底，人工复核兜底。
- **不做替代品**：企业要权限、合规、集成，个人不想换工具。所以它是增强层，挂在已有知识库上干活。

## 生长循环（核心闭环）

```
① ingest 增量导入（hash 对比，只处理变化的文件）
② health_check 体检（失效链接 / 孤立页 / 词典一致性 / 过期）
③ planner 规划器（5 路信号 → LLM 生成任务清单 → 去重入库）
④ executor 执行器（高置信自动执行 / 低置信起草到 raw/_草稿）
⑤ review_sync 审核同步（草稿拖进 concepts/ 即通过，删除即拒绝）
⑥ 矛盾复核 + 写《生长摘要》（今天该做什么，一眼可见）
```

一圈下来约 1 次 LLM 调用（规划），其余是本地计算 + 缓存命中。

## 架构

```
.
├── config.py            # 集中配置：路径/阈值/API/草稿目录（KB_ROOT 可用环境变量覆盖）
├── ingest.py            # 增量导入器：hash 对比 → 只处理变化（幂等）
├── extractors.py        # 多模态提取：md / pdf / docx / 图片 OCR
├── directions.yaml/py   # 生长方向规则分类
├── ask_kb.py            # RAG 问答核心：粗筛 → rerank → DeepSeek → 缓存
├── api.py / app.py      # FastAPI 后端 / Streamlit 前端（端口 8100 / 8501）
│
├── health_check.py      # 体检：失效链接 / 孤立页 / 词典一致性 / 过期
├── planner.py           # 规划器：5 路信号 → LLM 任务清单 → 入库去重
├── executor.py          # 执行器：dry-run 安全闸 + 备份回滚 + 草稿起草 + AI 日志
├── review_sync.py       # 拖拽同步器：草稿去向 → approved/rejected + 转正自动收尾
├── growth_loop.py       # 闭环串起 ①~⑥，并写《生长摘要》
│
├── kb_db/               # Chroma 向量库（重建生成，不入 git）
├── kb_meta.db           # SQLite：notes + tasks + task_undo 备份
└── test_*.py            # 16 个测试文件 / 66 个测试
```

## 快速开始

```bash
git clone https://github.com/mabowen1112-art/self-growing-kb-rag.git
cd self-growing-kb-rag
uv sync

cp .env.example .env      # 填 SILICONFLOW_API_KEY / DEEPSEEK_API_KEY
export KB_ROOT=/path/to/your/markdown/vault   # Windows: set KB_ROOT=D:\你的知识库

uv run python ingest.py             # 首次全量导入（--rebuild 重建向量库）
uv run python ask_kb.py "你的问题"   # 命令行问答
uv run python health_check.py       # 体检
uv run python growth_loop.py        # 走一圈生长循环（默认只预览，不动文件）
uv run pytest                       # 63 passed, 3 deselected
```

Windows 一键入口（作者自用）：`启动知识库问答.bat`（后端 + 前端，自动开浏览器）、`手动生长循环.bat`、`install_scheduled_task.bat`（注册每日导入 / 每周生长循环）。

## RAG 链路

```
笔记/PDF/图片 → ingest
  ① 切片：md 按 ## 切块（跳过 frontmatter，<30 字符丢弃）；PDF 按页；docx 按标题；图片本地 OCR
  ② 向量化：bge-m3（1024 维，批量 10 条）
  ③ 存储：Chroma PersistentClient（cosine）

提问 → ask_kb
  ④ 粗筛：bge-m3 检索 top-30（距离 ≤ 0.75）
  ⑤ 精排：bge-reranker-v2-m3 重排 top-7（相关度 ≥ 0.25）—— 必须配，单独检索会检不全
  ⑥ 生成：DeepSeek 结构化 JSON（json_object）+ 程序二次校验
  ⑦ 回答两层：📌 资料层（只讲知识库内容 + 标来源）/ 💡 补充层（模型通用知识，明确区分）
  ⑧ 缓存：相同问题且命中资料未变时复用；限流 60 秒 10 次
  ⑨ 防幻觉：没检索到就明确说"知识库里没有"，不编
  ⑩ 前端两段式：先亮资料块（约 1s）→ 再生成答案（复用检索结果）
```

关键参数在 `config.py`：`RETRIEVE_TOP=30 / RETRIEVE_MAX_DISTANCE=0.75 / RERANK_K=7 / RERANK_MIN_SCORE=0.25`。

⚠️ **阈值要按你的知识库规模调，别照抄。** 实测库从 139 块涨到 206 块时，原 top-3 就不够用了。

## 隐私设计（这个项目最当真的部分）

知识库里必然有不想被检索的内容（个人画像、家庭与情绪记录、语音转录）。所以隐私不是在提示词里写"别引用"，而是在**数据入口**做隔离：

- `PRIVATE_DIRS` / `PRIVATE_FILES`（`config.py`）：命中的路径**根本不进向量库**，自动规划也不碰
- 增量导入时移除已转为私密的旧向量；旧问答缓存不再加载
- 问答界面不提供"切换到私密库"的开关——没有这个能力，就没有误用的可能
- 测试锁死：`test_privacy.py` 验证私密页不会出现在检索结果里

## 关键设计取舍

- **安全三件套**：默认 dry-run、改前备份（`task_undo` 可回滚）、细节模糊时拒绝执行
- **草稿隔离**：AI 起草只写 `raw/_草稿/`，不进向量库，人拖进 `concepts/` 才算通过
- **积压刹车**：待审草稿达到上限（10）时规划器暂停新增任务，先消化存量
- **幂等**：导入 / 规划 / 同步重复跑零副作用、零 API 费用
- **AI 自动日志**：每一步操作写进知识库 `log.md`（做了什么、放哪了）

## 测试

```bash
uv run pytest                 # 63 passed, 3 deselected
uv run pytest -m integration  # 3 个端到端验收（需要本地知识库 + API key）
```

当前基线（2026-11-16，作者本机知识库）：

| 指标 | 结果 |
|---|---|
| 连问 10 题带正确来源 | 10/10（阈值 ≥8） |
| 自动归类准确率 | 90.4%（75/83，阈值 ≥70%） |
| 测试 | 63 单元/集成通过 + 3 端到端通过 |

`test_acceptance.py` 需要真实知识库和 API key，默认被 `pytest.ini` 跳过（加 `-m integration` 才跑）。

## 当前局限（诚实版）

- **单用户、无权限体系**：不能多人共用，没有鉴权
- **阈值靠手调**：检索 / rerank 阈值随库规模变化，没有自适应
- **分类是规则版**：`directions.yaml` 关键词匹配，不是学习模型；换主题要改规则
- **评测集只有 10 题**：够做回归，不够做统计意义上的评测
- **强依赖外部 API**：DeepSeek + 硅基流动，断网不可用，无本地模型兜底
- **Windows 优先**：bat / 计划任务是 Windows 写法，Linux / macOS 需自行替换
- **前端是单机 Streamlit**：没有部署方案，也没有多端同步

## 下一步

1. 检索阈值自适应（按库规模 + 命中率自动调 top-k）
2. 评测集扩容 + CI 跑离线回归（不依赖 API 的部分）
3. 跨平台：把 bat 换成交叉平台的调度脚本
4. V3 才做产品化（多用户 / 权限 / 增量评测），现在刻意不做

## License

MIT，见 [LICENSE](LICENSE)。

作者：[@mabowen1112-art](https://github.com/mabowen1112-art) · 信息管理与信息系统在读，做这个是为了给自己搭一套能长期用的知识基建。
