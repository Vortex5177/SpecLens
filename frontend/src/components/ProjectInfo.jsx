import VersionPanel from "./VersionPanel.jsx";

/**
 * 项目分析结果展示：语言统计、版本检测与确认、依赖文件、文件树。
 * 后续 Phase 会在此追加 Review 模式选择。
 */
function ProjectInfo({ analysis, onVersionsConfirmed }) {
  const languageEntries = Object.entries(analysis.languages);

  return (
    <section className="project-info">
      <h2>项目分析结果</h2>
      <p className="hint">
        项目 ID：<code>{analysis.project_id}</code> ｜ 共 {analysis.file_count} 个文件
      </p>

      <h3>语言</h3>
      {languageEntries.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>语言</th>
              <th>文件数</th>
            </tr>
          </thead>
          <tbody>
            {languageEntries.map(([language, count]) => (
              <tr key={language}>
                <td>{language}</td>
                <td>{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="hint">未识别到已知语言文件</p>
      )}

      <h3>依赖文件</h3>
      {analysis.dependency_files.length > 0 ? (
        <ul>
          {analysis.dependency_files.map((path) => (
            <li key={path}>
              <code>{path}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">未检测到依赖描述文件</p>
      )}

      {analysis.versions && (
        <VersionPanel
          key={analysis.project_id}
          versions={analysis.versions}
          projectId={analysis.project_id}
          onConfirmed={onVersionsConfirmed}
        />
      )}

      <h3>文件树{analysis.tree_truncated && "（已截断）"}</h3>
      <pre className="file-tree">
        {analysis.file_tree.join("\n")}
      </pre>
    </section>
  );
}

export default ProjectInfo;
