# Version-Aware AI Code Reviewer

面向开发者的版本敏感 AI 代码审查工具：针对用户指定的技术版本，结合对应版本的官方文档与安全规范进行 Code Review，并生成可直接交给 AI Coding 工具的 Fix Prompt。

> 本项目只发现问题、给出依据与建议，**不自动修改用户代码**。

## 当前进度

- [x] Phase 1：项目初始化（FastAPI + React + Docker Compose 骨架）
- [x] Phase 2：项目上传与结构分析（安全解压 + 语言/依赖文件识别）
- [x] Phase 3：依赖与版本识别（只读依赖文件不猜测，范围约束待用户确认）
- [x] Phase 4：RAG 知识库（BGE-M3 本地 Embedding + Qdrant 入库/分块/元数据）
- [x] Phase 5：Official Retriever（technology + version 硬性过滤检索官方文档）
- [x] Phase 6：Security Retriever（安全规范语义检索，独立 collection 不按版本过滤）
- [x] Phase 7：LangGraph 流程（analyze_project → review → generate_result）
- [x] Phase 8：Review Agent（单 Agent + 4 个只读工具，动态选择审查上下文）
- [x] Phase 9：Structured Output（Pydantic 强约束的 Issue 列表）
- [x] Phase 10：Fix Prompt（单问题 + 项目级，模板确定性生成）
- [ ] Phase 11+：Migration、前端整合、Docker Compose...（待开发）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+ / FastAPI / LangChain / LangGraph / Pydantic |
| RAG | BGE-M3（本地 Embedding）+ Qdrant（Windows 本地原生运行，不用 Docker） |
| 前端 | React + Vite |
| LLM | DeepSeek API（OpenAI 兼容接口） |
| 部署 | 本地开发为主；docker-compose 仅作备用参考 |

## 本地开发运行（不使用 Docker）

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 前端

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 <http://localhost:5173>，看到"后端已连接"即表示骨架运行正常。

也可直接运行根目录 `.\dev.ps1` 一键启动前后端。

### 环境变量

复制 `backend/.env.example` 为 `backend/.env` 并填入真实值（`.env` 不会进入 Git）。

### Qdrant（Windows 本地原生，不用 Docker）

1. 从 <https://github.com/qdrant/qdrant/releases/latest> 下载 `qdrant-x86_64-pc-windows-msvc.zip`（当前 v1.19.0）
2. 解压后把 `qdrant.exe` 放入 `tools\qdrant\`
3. 双击 `tools\qdrant\start-qdrant.bat` 启动（数据持久化在 `tools\qdrant\storage\`）
4. 验证：`Invoke-RestMethod http://localhost:6333/healthz`
5. 连通性测试：`.\backend\.venv\Scripts\python.exe scripts\test_qdrant_connection.py`
6. （可选）Web UI：另下载 qdrant-web-ui 的 `dist-qdrant.zip`，解压出 `static\` 放到 `tools\qdrant\` 后重启，访问 <http://localhost:6333/dashboard>

详见 [tools/qdrant/README.md](tools/qdrant/README.md)。

## Docker 运行（备用，不作为主要方式）

> 本机 Docker Desktop 存在稳定性问题（引擎崩溃/内存泄漏），日常开发不使用；文件保留仅供容器化部署参考。

```powershell
docker compose up --build
```

- 前端：<http://localhost:5173>
- 后端：<http://localhost:8000/api/health>
- Qdrant：<http://localhost:6333>

## API（当前可用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 健康检查 |
| GET | /api/version | 应用版本 |
| POST | /api/projects/upload | 上传项目 zip（≤50MB），返回结构分析与版本识别 |
| GET | /api/projects/{project_id} | 查询项目分析结果 |
| POST | /api/projects/{project_id}/versions | 确认/覆盖技术版本 |
| POST | /api/knowledge/ingest | 扫描 knowledge/official 与 knowledge/security 并入库 Qdrant（首次需加载 BGE-M3） |
| GET | /api/knowledge/search?technology=&version=&query= | Official Retriever：版本敏感检索（technology 与 version 必填，绝不跨版本返回） |
| GET | /api/knowledge/search/security?query= | Security Retriever：安全规范语义检索（与技术版本无关） |
| POST | /api/reviews | 创建并同步执行 Code Review（要求项目所有技术版本已确认） |
| GET | /api/reviews/{review_id} | 查询 Review 结果（V1 中 review_id 即 project_id，含项目级 Fix Prompt） |

版本识别规则（规格第 9 节）：精确锁定（`==`、锁文件）直接采用；范围约束（`>=`、`^`、`~`）标记为待确认，**绝不猜测**，由用户指定。支持 requirements.txt、pyproject.toml、package.json、package-lock.json、poetry.lock、uv.lock；yarn.lock / pnpm-lock.yaml 会被检测到但 V1 不解析。

上传限制与忽略规则（路径穿越防护、防 zip 炸弹、跳过 `.env`/`node_modules` 等）集中在 `backend/app/config.py`。

可用 `python scripts/make_sample.py` 生成测试样例项目（位于 test_sample/，已排除在 Git 之外）。

## RAG 知识库（Phase 4~6）

- 知识目录结构即元数据：`knowledge/official/{technology}/{version}/...` → 向量的 `technology`/`version` payload；每块携带规格要求的完整元数据（technology / version / source_type / document_type / topic）
- 两个 collection：`official_docs`（按版本过滤）与 `security_docs`（平铺目录，固定 technology=general、version=latest，不按版本过滤）
- 入库：`POST /api/knowledge/ingest`（确定性块 ID，重复入库幂等）
- Official Retriever：`GET /api/knowledge/search`，Qdrant metadata filter 硬性隔离版本（已验证：用 0.120 概念查 0.110 不会泄漏 0.120 文档）
- Security Retriever：`GET /api/knowledge/search/security`（已验证：密码存储问题命中 password 规范，注入问题命中输入验证规范）
- Embedding：BGE-M3 本地运行（首次从 HuggingFace 下载 2.3GB，国内建议设 `HF_ENDPOINT=https://hf-mirror.com`）
- 验证脚本：`scripts\test_bge_m3.py`（模型加载+维度）、`scripts\test_qdrant_connection.py`（向量库四步测试）

## Code Review 流程（Phase 7~10）

```text
POST /api/reviews
    ↓
LangGraph：analyze_project → review → generate_result
    │              │                  │
    │              │                  └─ 确定性模板生成单问题/项目级 Fix Prompt（不调 LLM）
    │              └─ 单 Agent（create_agent + response_format=ReviewResult）
    │                 工具：list_files / read_file / search_official_docs / search_security_rules
    └─ 确定性：读 meta、校验版本已全部确认、选取源码文件构建上下文
```

- 职责划分（规格原则 3）：LangGraph 管流程、Agent 管上下文选择、LLM 管判断、RAG 管检索
- 证据规则（规格第 21 节）：依据必须来自检索结果；知识库无证据时 `source=llm_inference`，禁止伪造官方依据
- 安全边界：Agent 工具全部只读且限制在项目目录内（防路径穿越）；官方文档检索强制使用已确认版本，传入其他版本直接拒绝
- LLM：DeepSeek（OpenAI 兼容接口），经 `init_chat_model` 统一入口，配置在 `backend/.env` 的 `DEEPSEEK_API_KEY`

## 目录结构

```
├── backend/      # FastAPI 后端（app/ 内按 api 路由分层）
├── frontend/     # React + Vite 前端
├── knowledge/    # 预置知识库：official/（官方文档）、security/（安全规范）
├── tools/qdrant/ # Qdrant 本地运行：可执行文件（手动下载）+ 启停脚本 + 数据目录
├── docker-compose.yml
└── dev.ps1       # Windows 开发环境一键启动
```

## 已知限制与决策

- Qdrant 采用 Windows 本地原生可执行程序运行（双击 `tools\qdrant\start-qdrant.bat`）；因本机 Docker Desktop 不稳定，已弃用 Docker 方式（compose 文件保留作参考）。
- BGE-M3 模型首次加载需下载（已缓存于 `~/.cache/huggingface`）；CPU 推理，入库/检索速度满足开发需求。
- 当前知识库仅含 fastapi 0.110/0.120 官方样例文档与 3 份通用安全规范样例（密码、输入验证、认证授权），真实文档在后续按需填充。
