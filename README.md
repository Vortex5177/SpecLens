# Version-Aware AI Code Reviewer

版本敏感的 AI 代码审查与迁移分析工具。针对项目实际使用的技术版本，结合**对应版本**的官方文档与安全规范进行 Code Review；或对比当前版本与目标版本的规范差异，分析升级迁移需要调整的地方。两种模式都会生成可直接交给任意 AI Coding 工具执行的 **Fix Prompt**。

> 本项目只发现问题、给出依据与建议，不自动修改用户代码。

### 项目演示：
https://www.bilibili.com/video/BV1W6tU67EW8/?spm_id_from=333.1387.homepage.video_card.click

## 核心特性

- **版本敏感检索**：官方文档按 `technology + version` 硬性过滤，绝不跨版本返回结果；知识库中没有证据时明确标注 `llm_inference`，不伪造官方依据
- **绝不猜测版本**：只读依赖文件识别版本；精确锁定（`==`、锁文件）直接采用，范围约束（`>=`、`^`、`~`）必须由用户确认后才允许审查；无依赖文件时可手动指定技术与版本，也可不提供任何版本直接审查（降级为安全规范 + 模型自身知识，依据标注 `llm_inference`）
- **Code Review（两阶段管线）**：阶段 1 一次无工具通读代码，产出显式嫌疑清单（文件 / 行号 / 主题 / 严重度预判），由程序校验真实性——文件必须真实存在、技术必须在已确认版本内；阶段 2 由代码 for 循环逐条执行「按主题选择性检索 → 单次确认」，检索次数随清单长度自适应，不再受固定工具调用预算挤压
- **Migration（双向对照）**：文档方向（检索迁移区间内各版本 What's New，找变更依据）与代码方向（枚举代码实际用法点，逐一验证变更依据）互补；合并输出携带置信度三层：`high` = 双侧印证 / `medium` = 仅文档方向（建议人工复核）/ `low` = 仅代码方向（待商榷）。迁移区间为 `(当前版本, 目标版本]`，一次检索放行整个区间
- **证据链完整性**：每条结论携带 evidence + source；`llm_inference` 标注由代码强制校验（不得携带证据文字），非法 severity / confidence 值自动回退——宁可丢证据，不留不可追溯的「证据」
- **Fix Prompt**：每个问题与项目级汇总均由模板确定性生成（不再调 LLM），可直接粘贴给 AI Coding 工具

## 架构

```text
前端（React + Vite，:5173）
    │  REST /api
后端（FastAPI，:8000）
    │  LangGraph 流水线
    ▼
analyze_project ──→ review ──→ generate_result
（确定性：解析项目、      （按模式分发：            （确定性模板：
  校验版本已确认）         两阶段管线 / 双向对照）      生成 Fix Prompt）
                            │
        ┌───────────────────┴─────────────────────────┐
        │ Code Review：两阶段管线（plan-and-execute）    │
        │  阶段1  无工具粗扫 → 嫌疑清单（程序校验真实性）  │
        │  阶段2  for 循环逐条：选择性检索 + 单次确认     │
        │  任一环节异常 → 回退单 Agent 路径（旧护栏全套）  │
        ├──────────────────────────────────────────────┤
        │ Migration：双向对照                            │
        │  文档方向 = 单 Agent（变更清单 → 找代码用法）    │
        │  代码方向 = 管线（枚举用法点 → 验证变更依据）    │
        │  合并 → confidence: high / medium / low       │
        └──────────────────────────────────────────────┘
                            │
                  Qdrant（BGE-M3 本地 Embedding）
              official_docs（按版本过滤） │ security_docs（通用安全规范）
```

职责划分：LangGraph 管流程，**清单管发现、循环管验证**，LLM 只管判断，RAG 管检索，Fix Prompt 由模板确定性生成。检索参数由程序填充，LLM 不再决定「查什么、查几次」——护栏随之部署在决策权所在的位置。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+ / FastAPI / Pydantic v2 |
| AI 编排 | LangChain / LangGraph（两阶段管线 + `create_agent` 结构化输出） |
| RAG | BGE-M3（本地 Embedding）+ Qdrant（向量库，按版本元数据前置过滤） |
| 前端 | React 18 + Vite（开发模式代理 `/api` 到后端） |
| LLM | DeepSeek API（OpenAI 兼容接口） |

## 项目结构

```
├── backend/              # FastAPI 后端
│   └── app/
│       ├── api/          # 路由层：project / version / knowledge / review / migration / health
│       ├── graph/        # LangGraph：state、tools、nodes（analyze / review / result / pipeline）
│       ├── models/       # Pydantic 请求/响应/结构化输出模型
│       └── services/     # 上传解压、依赖解析、RAG 检索
├── frontend/             # React + Vite 前端（「分析」板块：上传 → 版本确认 → 审查/迁移；「文档库」板块：知识文档查看 / 上传 / 删除）
├── knowledge/            # 知识库源文件
│   ├── sources/                             # 官方文档 Source 配置（YAML，定义采集 URL）
│   ├── official/{technology}/{version}/   # 官方文档（目录结构即元数据）
│   └── security/                          # 通用安全规范
├── tools/qdrant/         # Qdrant 本地运行：可执行文件 + 启停脚本 + 数据目录
├── scripts/              # 辅助脚本（官方文档采集、测试样例生成、连通性测试等）
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

仓库已内置一份开箱即用的知识库（前端「文档库」板块或 `GET /api/knowledge/catalog` 可查看）：

- **官方文档**（19 份）：Python 3.9~3.13、FastAPI 0.141、LangChain 0.2/0.3/1.0、Pydantic 2.0、Django 5.0/5.2/6.0、NumPy 2.5、pandas 2.3/3.0、SQLAlchemy 2.0
- **安全规范**（6 份）：OWASP Top 10:2025、OWASP ASVS 4.0、CWE Top 25，及认证 / 输入校验 / 密码存储专题

如需其他技术或版本，三种方式补充：

- **官方文档采集（推荐）**：在 `knowledge/sources/` 放置 YAML 配置（定义技术、版本、What's New URL），运行 `python scripts/ingest_official.py` 自动下载 → HTML 清理 → 入库。只采集版本变化部分（What's New / Changelog），稳定基础知识交给 LLM。重复执行幂等（确定性块 ID）
- **手动放文件 + 全量入库**：向 `knowledge/` 放入文档后调用 `POST /api/knowledge/ingest`
- **接口上传**：`POST /api/knowledge/documents`（multipart），指定 `source_type`（official/security），official 还需指定 `technology` 与 `version`，可选 `document_type`（reference / whats_new，缺省按文件名推断）。zip 模式下可开启 `auto_detect_version=true`，按每个文件名识别真实版本号（如 `whatsnew_3.11.txt → 3.11`），识别失败则回退到表单 `version`；适用于 Python 这种「基础文档 + 各版本 What's New 打包在一起」的官方发布结构。文档保存后立即增量入库，无需再调 ingest

Source 配置示例（`knowledge/sources/python.yaml`）：

```yaml
technology: python
documents:
  - version: "3.13"
    document_type: whats_new
    url: "https://docs.python.org/3/whatsnew/3.13.html"
```

采集命令：

```powershell
python scripts/ingest_official.py                        # 处理所有 Source
python scripts/ingest_official.py --tech python           # 只处理 Python
python scripts/ingest_official.py --tech python --version 3.13  # 只处理指定版本
```

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

1. **上传**：两种方式任选——项目 zip（≤50MB），或切换到「直接上传文件」选择多个源码/配置文件（.py、.js、requirements.txt 等，最多 200 个）。页面展示语言 / 依赖文件识别结果与文件树。可用 `python scripts/make_sample.py` 生成测试样例；`scripts/test_legacy_code.py` 是一份包含 Python 3.9 / pandas 2.3 / LangChain 0.2 / Django 5.0 旧版写法的样例，适合演示 Migration
2. **版本确认**：精确锁定的版本自动采用；范围约束标记为「待确认」，需手动填写并确认。所有技术确认前审查按钮保持禁用
3. **选择模式并开始**：
   - Code Review：直接点「开始审查」
   - Migration：从下拉框为待迁移技术选择目标版本（选项来自知识库已入库版本），至少一个后点「开始迁移分析」
4. **查看结果**：严重级别统计 + 问题卡片（文件 / 行号 / 描述 / 可折叠证据 / 建议）；每个问题有 **Copy Fix Prompt**，页面级有 **Copy Project Fix Prompt**；迁移结果额外携带 `confidence` 置信度标注

Code Review 为同步接口，视嫌疑清单规模约 1~3 分钟；Migration 因双跑（文档方向 + 代码方向）约 3~6 分钟。

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
| POST | `/api/knowledge/documents` | 上传知识文档（.md/.txt/.rst 或 .zip 压缩包，指定 technology/version 与文档类型；zip 可开启 auto_detect_version 按文件名自动识别版本）并即时入库，返回 versions_detected |
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
- 证据规则：问题依据必须来自检索结果，无证据时标注 `llm_inference`，禁止伪造官方文档依据；该规则由代码强制执行——`llm_inference` 标注携带的证据文字会被自动清空并压低置信度
- 管线阶段 1 产出的嫌疑由程序校验：引用不存在的文件、涉及未确认技术的条目直接丢弃

## 已知限制

- 内置知识库仅覆盖「知识库入库」一节列出的 8 个技术，其他技术或版本需按该节方式自行采集填充
- BGE-M3 为 CPU 本地推理，满足开发规模；后端重启后首个检索请求会因重新加载模型而变慢
- 审查 / 迁移为同步接口，未实现异步任务队列
- 知识库按固定长度切块，块边界与语义条目不完全对齐，混合内容的分块可能稀释检索精度
- 迁移模式为双跑（Agent + 管线），时长与 API 成本约为单跑两倍；双向对照的合并按「同文件同技术」贪心匹配，同文件同技术的多条变更可能互相印证（结果偏乐观，建议复核 high 条目）
