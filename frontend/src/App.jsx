import { useEffect, useState } from "react";
import ProjectInfo from "./components/ProjectInfo.jsx";
import ReviewPanel from "./components/ReviewPanel.jsx";
import ReviewResult from "./components/ReviewResult.jsx";
import UploadSection from "./components/UploadSection.jsx";

/**
 * 首页：后端连通状态 + 项目上传 + 版本确认 + 审查触发与结果展示。
 */
function App() {
  // 后端连接状态：checking / connected / failed
  const [status, setStatus] = useState("checking");
  // 上传成功后的分析结果（UploadResponse）
  const [uploadResult, setUploadResult] = useState(null);
  // 审查/迁移完成后的结果：{ mode, data }（data 含 result 与 project_fix_prompt）
  const [review, setReview] = useState(null);

  function handleUploaded(result) {
    setUploadResult(result);
    // 新项目上传后，旧审查结果失效
    setReview(null);
  }

  // 版本确认后，后端返回更新后的完整分析结果，直接替换；版本变更需重新审查
  function handleVersionsConfirmed(updatedAnalysis) {
    setUploadResult((prev) =>
      prev ? { ...prev, analysis: updatedAnalysis } : prev
    );
    setReview(null);
  }

  // 是否所有待确认版本都已确认（规格第 9 节：未确认不得开始审查）
  const versions = uploadResult?.analysis.versions || [];
  const reviewEnabled = versions.every(
    (v) => v.status === "exact" || v.confirmed
  );

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
          <UploadSection onUploaded={handleUploaded} />
          {uploadResult && (
            <ProjectInfo
              analysis={uploadResult.analysis}
              onVersionsConfirmed={handleVersionsConfirmed}
            />
          )}
          {uploadResult && (
            <ReviewPanel
              key={uploadResult.project_id}
              projectId={uploadResult.project_id}
              versions={versions}
              reviewEnabled={reviewEnabled}
              onCompleted={setReview}
            />
          )}
          {review && <ReviewResult mode={review.mode} data={review.data} />}
        </>
      )}
    </div>
  );
}

export default App;
