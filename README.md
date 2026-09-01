# Version-Aware AI Code Reviewer

版本敏感的 AI 代码审查与迁移分析工具。针对项目实际使用的技术版本，结合**对应版本**的官方文档与安全规范进行 Code Review；或对比当前版本与目标版本的规范差异，分析升级迁移需要调整的地方。两种模式都会生成可直接交给任意 AI Coding 工具执行的 **Fix Prompt**。

> 本项目只发现问题、给出依据与建议，不自动修改用户代码。

## 核心特性

- **版本敏感检索**：官方文档按 `technology + version` 硬性过滤，绝不跨版本返回结果；知识库中没有证据时明确标注 `llm_inference`，不伪造官方依据
- **绝不猜测版本**：只读依赖文件识别版本；精确锁定（`==`、锁文件）直接采用，范围约束（`>=`、`^`、`~`）必须由用户确认后才允许审查；无依赖文件时可手动指定技术与版本，也可不提供任何版本直接审查（降级为安全规范 + 模型自身知识，依据标注 `llm_inference`）
- **Code Review**：单 Agent 动态调用只读工具收集上下文，输出 Pydantic 强约束的结构化问题列表（文件 / 行号 / 类别 / 严重级别 / 置信度 / 证据 / 建议）
- **Migration**：同一引擎检索迁移区间内各版本的 What's New 变更文档 + 目标版本规范，产出「当前行为 → 目标行为」的迁移调整点（知识库文档按 `document_type` 区分 reference / whats_new）
- **Fix Prompt**：每个问题与项目级汇总均由模板确定性生成（不再调 LLM），可直接粘贴给 AI Coding 工具

## 架构

```text
前端（React + Vite，:5173）
    │  REST /api
后端（FastAPI，:8000）
    │  LangGraph 流水线
    ▼
analyze_project ──→ review ──→ generate_result
（确定性：解析项目、      （单 Agent + 4 个只读工具，    （确定性模板：
  校验版本已确认）         动态检索并生成结构化结果）       生成 Fix Prompt）
                            │
                            ▼
                  Qdrant（BGE-M3 本地 Embedding）
              official_docs（按版本过滤） │ security_docs（通用安全规范）
```

职责划分：LangGraph 管流程，Agent 管上下文选择，LLM 管判断，RAG 管检索，Fix Prompt 由模板确定性生成。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+ / FastAPI / Pydantic v2 |
| AI 编排 | LangChain / LangGraph（`create_agent` + Structured Output） |
| RAG | BGE-M3（本地 Embedding）+ Qdrant（向量库，按版本元数据过滤） |
| 前端 | React 18 + Vite（开发模式代理 `/api` 到后端） |
| LLM | DeepSeek API（OpenAI 兼容接口） |

## 项目结构

```
├── backend/              # FastAPI 后端
│   └── app/
│       ├── api/          # 路由层：project / version / knowledge / review / migration / health
│       ├── graph/        # LangGraph：state、tools、nodes（analyze / review / result）
│       ├── models/       # Pydantic 请求/响应/结构化输出模型
│       └── services/     # 上传解压、依赖解析、RAG 检索
├── frontend/             # React + Vite 前端（单页：上传 → 版本确认 → 审查/迁移）
├── knowledge/            # 知识库源文件
│   ├── official/{technology}/{version}/   # 官方文档（目录结构即元数据）
│   └── security/                          # 通用安全规范
├── tools/qdrant/         # Qdrant 本地运行：可执行文件 + 启停脚本 + 数据目录
├── scripts/              # 辅助脚本（生成测试样例、连通性测试等）
└── uploads/              # 上传项目与审查结果（运行时产物，不入版本库）
```

## 快速上手

### 前置条件

- Python 3.12+、Node.js 18+
- Qdrant：从 [官方 releases](https://github.com/qdrant/qdrant/releases/latest) 下载可执行文件（Windows / Linux / macOS 均支持），或使用 `docker run -p 6333:6333 qdrant/qdrant`，默认监听 `http://localhost:6333`
- DeepSeek API Key（或其他兼容 OpenAI 接口的服务）

### 1. 配置后端环境

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env             # 然后填入 DEEPSEEK_API_KEY
```

`.env` 关键配置：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | LLM API Key（必填） |
| `QDRANT_URL` | 默认 `http://localhost:6333` |
| `ALLOWED_ORIGINS` | 前端来源，默认 `http://localhost:5173` |
| `HF_ENDPOINT` | 可选，国内建议 `https://hf-mirror.com` 加速下载 BGE-M3 |

### 2. 知识库入库

两种方式：

- **手动放文件 + 全量入库**：向 `knowledge/` 放入文档后调用 `POST /api/knowledge/ingest`
- **接口上传**：`POST /api/knowledge/documents`（multipart），指定 `source_type`（official/security），official 还需指定 `technology` 与 `version`，可选 `document_type`（reference / whats_new，缺省按文件名推断）；文档保存后立即增量入库，无需再调 ingest

目录结构即元数据：`knowledge/official/fastapi/0.120/xxx.md` 的每个分块会携带 `technology=fastapi, version=0.120`。重复入库幂等（确定性块 ID）。首次调用需加载 BGE-M3 模型（约 2.3GB，首次从 HuggingFace 下载）。

### 3. 启动

```powershell
# 终端 1：后端
cd backend
uvicorn app.main:app --reload --port 8000

# 终端 2：前端
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173>，看到「后端已连接」即可使用。

## 使用流程

1. **上传**：两种方式任选——项目 zip（≤50MB），或切换到「直接上传文件」选择多个源码/配置文件（.py、.js、requirements.txt 等，最多 200 个）。页面展示语言 / 依赖文件识别结果与文件树。可用 `python scripts/make_sample.py` 生成测试样例
2. **版本确认**：精确锁定的版本自动采用；范围约束标记为「待确认」，需手动填写并确认。所有技术确认前审查按钮保持禁用
3. **选择模式并开始**：
   - Code Review：直接点「开始审查」
   - Migration：填写至少一个目标版本后点「开始迁移分析」
4. **查看结果**：严重级别统计 + 问题卡片（文件 / 行号 / 描述 / 可折叠证据 / 建议）；每个问题有 **Copy Fix Prompt**，页面级有 **Copy Project Fix Prompt**

审查为同步接口，视项目规模约 30~120 秒。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/version` | 应用版本 |
| POST | `/api/projects/upload` | 上传项目 zip，返回结构分析与版本识别 |
| POST | `/api/projects/upload-files` | 直接上传多个源码/配置文件（无需打包，白名单扩展名） |
| GET | `/api/projects/{project_id}` | 查询项目分析结果 |
| POST | `/api/projects/{project_id}/versions` | 确认 / 覆盖技术版本 |
| POST | `/api/knowledge/ingest` | 扫描 knowledge/ 并全量入库 Qdrant |
| POST | `/api/knowledge/documents` | 上传知识文档（.md/.txt/.rst 或 .zip 压缩包，指定 technology/version 与文档类型）并即时入库 |
| DELETE | `/api/knowledge/documents` | 删除知识文档（同时移除本地文件与 Qdrant 分块） |
| GET | `/api/knowledge/catalog` | 查看本地已有规范文档清单（按技术/版本分组，附入库分块数与文档类型） |
| GET | `/api/knowledge/search` | 版本敏感的官方文档检索（technology + version 必填） |
| GET | `/api/knowledge/search/migration` | 迁移检索：区间内 What's New 变更文档 + 目标版本规范 |
| GET | `/api/knowledge/search/security` | 安全规范语义检索 |
| POST | `/api/reviews` | 创建并同步执行 Code Review |
| GET | `/api/reviews/{review_id}` | 查询 Review 结果（含项目级 Fix Prompt） |
| POST | `/api/migrations` | 创建并同步执行 Migration（含目标版本列表） |
| GET | `/api/migrations/{migration_id}` | 查询 Migration 结果（含项目级迁移 Fix Prompt） |

交互式文档：启动后端后访问 <http://localhost:8000/docs>。

## 安全边界

- 上传：路径穿越防护、防 zip 炸弹、大小限制，自动跳过 `.env` / `node_modules` 等；直接上传文件模式用扩展名白名单拒收二进制文件
- Agent 工具全部只读，且限制在项目目录内；官方文档检索强制使用已确认版本，传入未确认版本直接拒绝
- 证据规则：问题依据必须来自检索结果，无证据时标注 `llm_inference`，禁止伪造官方文档依据

## 已知限制

- 仓库内置的 `knowledge/` 仅为样例文档（fastapi 0.110/0.120 + 3 份通用安全规范），实际使用请自行填充目标技术与版本的官方文档
- BGE-M3 为 CPU 本地推理，满足开发规模；后端重启后首个检索请求会因重新加载模型而变慢
- 审查 / 迁移为同步接口，未实现异步任务队列
