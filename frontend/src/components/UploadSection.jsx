import { useState } from "react";

/**
 * 项目上传组件：支持两种方式
 * - zip：整个项目打包上传（默认，适合完整项目）
 * - files：直接选择多个源码/配置文件上传（无需打包，适合少量文件）
 * 上传结果（分析数据）通过回调传给父组件。
 */
function UploadSection({ onUploaded }) {
  const [mode, setMode] = useState("zip");
  const [file, setFile] = useState(null);
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  async function upload(url, formData, okMessage) {
    setUploading(true);
    setError("");
    try {
      const res = await fetch(url, { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || okMessage);
      }
      onUploaded(data);
      setFile(null);
      setFiles([]);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (mode === "zip") {
      if (!file) {
        setError("请先选择一个项目 zip 压缩包");
        return;
      }
      const formData = new FormData();
      formData.append("file", file);
      await upload("/api/projects/upload", formData, "上传失败");
      return;
    }

    if (files.length === 0) {
      setError("请先选择至少一个源码或配置文件");
      return;
    }
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    await upload("/api/projects/upload-files", formData, `上传失败（HTTP）`);
  }

  return (
    <form className="upload-card" onSubmit={handleSubmit}>
      <h2>上传项目</h2>
      <div className="mode-options">
        <label>
          <input
            type="radio"
            name="upload-mode"
            checked={mode === "zip"}
            onChange={() => setMode("zip")}
            disabled={uploading}
          />
          项目 zip
        </label>
        <label>
          <input
            type="radio"
            name="upload-mode"
            checked={mode === "files"}
            onChange={() => setMode("files")}
            disabled={uploading}
          />
          直接上传文件
        </label>
      </div>

      {mode === "zip" ? (
        <>
          <p className="hint">
            将项目打包为 zip（建议排除 node_modules、.venv 等），最大 50MB
          </p>
          <input
            type="file"
            accept=".zip"
            onChange={(e) => setFile(e.target.files[0] || null)}
          />
        </>
      ) : (
        <>
          <p className="hint">
            选择源码与依赖/配置文件（.py、.js、.ts、requirements.txt 等），最多 200 个；
            建议同时选中依赖文件以便识别版本
          </p>
          <input
            type="file"
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
          />
          {files.length > 0 && (
            <p className="hint">已选择 {files.length} 个文件</p>
          )}
        </>
      )}

      <button type="submit" disabled={uploading}>
        {uploading ? "上传分析中..." : "上传并分析"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default UploadSection;
