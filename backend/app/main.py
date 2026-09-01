"""AI Code Reviewer 后端入口。

职责：加载 .env 环境变量、创建 FastAPI 应用、配置 CORS、注册各路由分组。
具体接口实现放在 app/api/ 下的各路由模块中。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 必须在导入 app.* 之前加载 .env（config 在导入时读取环境变量）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.project import router as project_router
from app.api.review import router as review_router
from app.api.version import router as version_router

# 允许访问前端来源，多个来源用逗号分隔（默认本地 Vite 开发服务器）
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app = FastAPI(title="Version-Aware AI Code Reviewer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由分组注册：后续 Phase 会在此追加 migration 等路由
app.include_router(health_router, prefix="/api")
app.include_router(version_router, prefix="/api")
app.include_router(project_router, prefix="/api")
app.include_router(review_router, prefix="/api")
# knowledge 路由自带 /api/knowledge 前缀，此处不再叠加
app.include_router(knowledge_router)
