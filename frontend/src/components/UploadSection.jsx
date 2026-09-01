import { useState } from "react";

/**
 * 项目上传组件：选择 zip 文件并上传，上传结果（分析数据）通过回调传给父组件。
 */
function UploadSection({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!file) {
      setError("请先选择一个项目 zip 压缩包");
      return;
    }

    setUploading(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch("/api/projects/upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `上传失败（HTTP ${res.status}）`);
      }
      onUploaded(data);
      setFile(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <form className="upload-card" onSubmit={handleSubmit}>
      <h2>上传项目</h2>
      <p className="hint">
        将项目打包为 zip（建议排除 node_modules、.venv 等），最大 50MB
      </p>
      <input
        type="file"
        accept=".zip"
        onChange={(e) => setFile(e.target.files[0] || null)}
      />
      <button type="submit" disabled={uploading}>
        {uploading ? "上传分析中..." : "上传并分析"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default UploadSection;
