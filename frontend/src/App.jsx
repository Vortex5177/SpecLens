import { useEffect, useState } from "react";
import ProjectInfo from "./components/ProjectInfo.jsx";
import UploadSection from "./components/UploadSection.jsx";

/**
 * 首页：后端连通状态 + 项目上传 + 分析结果与版本确认。
 */
function App() {
  // 后端连接状态：checking / connected / failed
  const [status, setStatus] = useState("checking");
  // 上传成功后的分析结果（UploadResponse）
  const [uploadResult, setUploadResult] = useState(null);

  // 版本确认后，后端返回更新后的完整分析结果，直接替换
  function handleVersionsConfirmed(updatedAnalysis) {
    setUploadResult((prev) =>
      prev ? { ...prev, analysis: updatedAnalysis } : prev
    );
  }

  useEffect(() => {
    fetch("/api/health")
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setStatus(data.status === "ok" ? "connected" : "failed");
      })
      .catch(() => setStatus("failed"));
  }, []);

  return (
    <div className="container">
      <h1>Version-Aware AI Code Reviewer</h1>
      <p className="subtitle">
        基于指定技术版本的官方文档与安全规范，对项目进行 AI Code Review 并生成 Fix Prompt
      </p>

      <div className={`status status-${status}`}>
        {status === "checking" && "正在检测后端连接..."}
        {status === "connected" && "后端已连接"}
        {status === "failed" && "后端未连接，请确认后端服务已启动（端口 8000）"}
      </div>

      {status === "connected" && (
        <>
          <UploadSection onUploaded={setUploadResult} />
          {uploadResult && (
            <ProjectInfo
              analysis={uploadResult.analysis}
              onVersionsConfirmed={handleVersionsConfirmed}
            />
          )}
        </>
      )}
    </div>
  );
}

export default App;
