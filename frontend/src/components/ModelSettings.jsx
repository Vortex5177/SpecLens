import { Fragment, useState } from "react";

/**
 * 模型设置页（Chatbox 式多模型管理）：
 * - 列出已配置模型（名称 / 模型名 / Base URL / 打码 Key / 激活徽标）
 * - 操作：激活、连通测试、编辑（行内）、删除（二次确认）
 * - 顶部：添加新模型表单（仅支持 OpenAI 兼容接口）
 * - 检测：按 Base URL + API Key 探测端点可用模型列表，点击候选即填入
 */

/** 检测结果区：候选模型芯片（点击填入模型名）或错误提示，添加/编辑表单共用。 */
function ProbeResult({ state, onPick }) {
  if (state.status === "ok") {
    return (
      <div className="model-probe-chips">
        <span className="hint">检测到 {state.models.length} 个模型，点击填入：</span>
        {state.models.map((m) => (
          <button type="button" key={m} onClick={() => onPick(m)}>
            {m}
          </button>
        ))}
      </div>
    );
  }
  if (state.status === "fail") {
    return <p className="error model-test-result">{state.error}</p>;
  }
  return null;
}

function ModelSettings({ models: data, onChanged }) {
  // 新增表单
  const [form, setForm] = useState({ name: "", base_url: "", api_key: "", model: "" });
  // 编辑中：{ id, form }
  const [editing, setEditing] = useState(null);
  // 连通测试结果：id -> { status: "running" | "ok" | "fail", message }
  const [tests, setTests] = useState({});
  // 待二次确认删除的模型 id
  const [confirming, setConfirming] = useState(null);
  // 检测结果：添加表单与编辑表单各自维护
  const [probeAdd, setProbeAdd] = useState({ status: "idle", models: [], error: "" });
  const [probeEdit, setProbeEdit] = useState({ status: "idle", models: [], error: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const models = data?.models || [];

  function updateForm(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
    if ((field === "base_url" || field === "api_key") && probeAdd.status !== "idle") {
      setProbeAdd({ status: "idle", models: [], error: "" });
    }
  }

  function updateEditForm(field, value) {
    setEditing((prev) => ({ ...prev, form: { ...prev.form, [field]: value } }));
    if ((field === "base_url" || field === "api_key") && probeEdit.status !== "idle") {
      setProbeEdit({ status: "idle", models: [], error: "" });
    }
  }

  async function handleAdd(e) {
    e.preventDefault();
    setError("");
    if (!form.name.trim() || !form.base_url.trim() || !form.api_key.trim() || !form.model.trim()) {
      setError("名称、Base URL、API Key、模型名均为必填");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/llm/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || `添加失败（HTTP ${res.status}）`);
      }
      setForm({ name: "", base_url: "", api_key: "", model: "" });
      setProbeAdd({ status: "idle", models: [], error: "" });
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  /** 探测端点可用模型列表（kind: "add" | "edit"）。 */
  async function handleProbe(kind) {
    const isEdit = kind === "edit";
    const source = isEdit ? editing?.form : form;
    const apply = isEdit ? setProbeEdit : setProbeAdd;
    if (!source || !source.base_url.trim()) {
      setError("请先填写 Base URL");
      return;
    }
    setError("");
    apply({ status: "running", models: [], error: "" });
    try {
      const res = await fetch("/api/llm/models/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: source.base_url.trim(),
          api_key: source.api_key.trim(),
          model_id: isEdit ? editing.id : null,
        }),
      });
      const body = await res.json();
      apply(
        body.ok
          ? { status: "ok", models: body.models, error: "" }
          : { status: "fail", models: [], error: body.error || "检测失败" }
      );
    } catch (err) {
      apply({ status: "fail", models: [], error: err.message });
    }
  }

  async function handleActivate(id) {
    setError("");
    const res = await fetch(`/api/llm/models/${id}/activate`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail || `切换失败（HTTP ${res.status}）`);
      return;
    }
    onChanged();
  }

  async function handleTest(id) {
    setTests((prev) => ({ ...prev, [id]: { status: "running", message: "测试中..." } }));
    try {
      const res = await fetch(`/api/llm/models/${id}/test`, { method: "POST" });
      const body = await res.json();
      setTests((prev) => ({
        ...prev,
        [id]: body.ok
          ? { status: "ok", message: `通过（${(body.latency_ms / 1000).toFixed(1)}s）` }
          : { status: "fail", message: body.error || "失败" },
      }));
    } catch (err) {
      setTests((prev) => ({ ...prev, [id]: { status: "fail", message: err.message } }));
    }
  }

  function startEdit(m) {
    setError("");
    setProbeEdit({ status: "idle", models: [], error: "" });
    setEditing({
      id: m.id,
      form: { name: m.name, base_url: m.base_url, api_key: "", model: m.model },
    });
  }

  async function handleSaveEdit() {
    if (!editing) {
      return;
    }
    const f = editing.form;
    if (!f.name.trim() || !f.base_url.trim() || !f.model.trim()) {
      setError("名称、Base URL、模型名不能为空");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/llm/models/${editing.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(f),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || `保存失败（HTTP ${res.status}）`);
      }
      setEditing(null);
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id) {
    setError("");
    const res = await fetch(`/api/llm/models/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail || `删除失败（HTTP ${res.status}）`);
      return;
    }
    setConfirming(null);
    onChanged();
  }

  return (
    <section className="model-settings">
      <div className="model-add">
        <h3>添加模型</h3>
        <p className="hint">
          兼容任意 OpenAI 接口端点（DeepSeek、本地 vLLM、Ollama、OpenAI、Moonshot 等）；
          Base URL 通常以 /v1 结尾
        </p>
        <form onSubmit={handleAdd}>
          <div className="model-add-fields">
            <input
              type="text"
              placeholder="名称（如：本地 Qwen3-4B）"
              value={form.name}
              onChange={(e) => updateForm("name", e.target.value)}
              disabled={busy}
            />
            <input
              type="text"
              placeholder="Base URL（如：http://localhost:8200/v1）"
              value={form.base_url}
              onChange={(e) => updateForm("base_url", e.target.value)}
              disabled={busy}
            />
            <input
              type="text"
              placeholder="API Key（本地服务可填任意值）"
              value={form.api_key}
              onChange={(e) => updateForm("api_key", e.target.value)}
              disabled={busy}
            />
            <input
              type="text"
              placeholder="模型名（也可点“检测模型”从列表选择）"
              value={form.model}
              onChange={(e) => updateForm("model", e.target.value)}
              disabled={busy}
            />
            <button
              type="button"
              onClick={() => handleProbe("add")}
              disabled={busy || probeAdd.status === "running"}
            >
              {probeAdd.status === "running" ? "检测中..." : "检测模型"}
            </button>
            <button type="submit" disabled={busy}>
              添加
            </button>
          </div>
          <ProbeResult state={probeAdd} onPick={(m) => updateForm("model", m)} />
        </form>
      </div>

      <h3>已配置模型</h3>
      {models.length === 0 && (
        <p className="hint">
          暂无模型：请在上方添加，或在 backend/.env 中配置 DEEPSEEK_API_KEY 后重启后端
        </p>
      )}
      {models.length > 0 && (
        <table className="version-table model-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>模型名</th>
              <th>Base URL</th>
              <th>API Key</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <Fragment key={m.id}>
                <tr>
                  <td>
                    {m.name}
                    {m.is_active && <span className="badge badge-ok model-active-badge">当前</span>}
                  </td>
                  <td>
                    <code>{m.model}</code>
                  </td>
                  <td>
                    <code>{m.base_url}</code>
                  </td>
                  <td>
                    <code>{m.api_key_masked}</code>
                  </td>
                  <td>
                    <div className="model-actions">
                      {!m.is_active && (
                        <button type="button" onClick={() => handleActivate(m.id)}>
                          激活
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleTest(m.id)}
                        disabled={tests[m.id]?.status === "running"}
                      >
                        测试
                      </button>
                      <button type="button" onClick={() => startEdit(m)}>
                        编辑
                      </button>
                      {confirming === m.id ? (
                        <>
                          <button type="button" className="delete-btn" onClick={() => handleDelete(m.id)}>
                            确认删除
                          </button>
                          <button type="button" onClick={() => setConfirming(null)}>
                            取消
                          </button>
                        </>
                      ) : (
                        <button type="button" className="delete-btn" onClick={() => setConfirming(m.id)}>
                          删除
                        </button>
                      )}
                    </div>
                    {tests[m.id] && tests[m.id].status !== "running" && (
                      <p className={tests[m.id].status === "ok" ? "ok model-test-result" : "error model-test-result"}>
                        {tests[m.id].message}
                      </p>
                    )}
                  </td>
                </tr>
                {editing?.id === m.id && (
                  <tr className="model-edit-row">
                    <td colSpan={5}>
                      <div className="model-add-fields">
                        <input
                          type="text"
                          placeholder="名称"
                          value={editing.form.name}
                          onChange={(e) => updateEditForm("name", e.target.value)}
                          disabled={busy}
                        />
                        <input
                          type="text"
                          placeholder="Base URL"
                          value={editing.form.base_url}
                          onChange={(e) => updateEditForm("base_url", e.target.value)}
                          disabled={busy}
                        />
                        <input
                          type="text"
                          placeholder="API Key（留空表示不修改）"
                          value={editing.form.api_key}
                          onChange={(e) => updateEditForm("api_key", e.target.value)}
                          disabled={busy}
                        />
                        <input
                          type="text"
                          placeholder="模型名"
                          value={editing.form.model}
                          onChange={(e) => updateEditForm("model", e.target.value)}
                          disabled={busy}
                        />
                        <button
                          type="button"
                          onClick={() => handleProbe("edit")}
                          disabled={busy || probeEdit.status === "running"}
                        >
                          {probeEdit.status === "running" ? "检测中..." : "检测模型"}
                        </button>
                        <button type="button" onClick={handleSaveEdit} disabled={busy}>
                          保存
                        </button>
                        <button type="button" onClick={() => setEditing(null)} disabled={busy}>
                          取消
                        </button>
                      </div>
                      <ProbeResult state={probeEdit} onPick={(m) => updateEditForm("model", m)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

export default ModelSettings;
