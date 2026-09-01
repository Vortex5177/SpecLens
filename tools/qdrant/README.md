# Qdrant 本地工具目录（Windows 原生运行，不依赖 Docker/WSL/Hyper-V）

本目录存放 Qdrant 官方 Windows 可执行程序及其数据。

## 布局

```
tools/qdrant/
├── qdrant.exe            # 官方可执行文件（需手动下载，见下方步骤；不提交 Git）
├── static/               # Web UI 静态文件（可选，单独下载；不提交 Git）
├── storage/              # 向量数据持久化目录（自动生成，不提交 Git）
├── start-qdrant.bat      # 启动脚本（提交 Git）
└── stop-qdrant.ps1       # 停止脚本（提交 Git）
```

## 下载步骤（一次性）

### 1. 服务端可执行文件（必需）

1. 打开官方发布页 <https://github.com/qdrant/qdrant/releases/latest>（当前最新为 **v1.19.0**）
2. 下载 Windows 资产：**`qdrant-x86_64-pc-windows-msvc.zip`**（约 29MB）
   直链：<https://github.com/qdrant/qdrant/releases/download/v1.19.0/qdrant-x86_64-pc-windows-msvc.zip>
3. 解压 zip，把里面的 `qdrant.exe` 复制到本目录（`tools\qdrant\`）
4. 双击 `start-qdrant.bat` 启动

> 注：v1.19 的 zip 不再附带 `config\` 文件夹，脚本已改为只用内置默认配置；
> 存储路径通过环境变量 `QDRANT__STORAGE__STORAGE_PATH` 指定（bat 中已设置）。

### 2. Web UI 静态文件（可选，不加则 /dashboard 返回 404）

官方 Windows 包不含 Web UI 前端，需单独下载（约 7MB）：

1. 下载 <https://github.com/qdrant/qdrant-web-ui/releases/download/v0.2.16/dist-qdrant.zip>
2. 解压，把里面的 `static` 文件夹放到本目录（与 `qdrant.exe` 同级）
3. 重启 Qdrant，浏览器打开 <http://localhost:6333/dashboard>

## 停止

在启动窗口按 `Ctrl+C`、直接关闭窗口，或运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\qdrant\stop-qdrant.ps1
```

## 验证是否运行

```powershell
Invoke-RestMethod http://localhost:6333/healthz        # 应返回 healthz check passed
Invoke-RestMethod http://localhost:6333/collections     # 列出所有 collection
```
