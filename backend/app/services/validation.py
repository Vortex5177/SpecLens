"""纯程序校验（V2，无 LLM、无 IO，可离线单测）。

职责边界：
- 候选身份校验：文件必须存在于代码快照、行号必须落在可见范围、维度合法。
- 证据生成：从检索结果 / 文档枚举块构造 Evidence，ID 由程序确定性生成。
- 核实输出校验：判决-证据状态-ID 引用之间的一致性，任何非法都返回错误
  （不自动修复，非法输出的候选计入 failed）。
"""
import hashlib

from pydantic import ValidationError

from app.models.schemas import (
    Evidence,
    EvidenceBundle,
    VerificationOutput,
)


class VerificationError(ValueError):
    """核实输出未通过程序校验（候选计为 failed，不做自动修复）。"""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _make_evidence_id(*parts: str) -> str:
    return "ev-" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def make_evidence(item: dict) -> Evidence | None:
    """从标准检索结果（含 score/content/metadata）构造 Evidence。

    缺 source 或 content 的条目视为无效返回 None（不入证据池）。
    """
    content = item.get("content", "")
    metadata = item.get("metadata", {}) or {}
    source = metadata.get("source", "")
    if not content or not source:
        return None
    return Evidence(
        evidence_id=_make_evidence_id(
            source,
            str(metadata.get("chunk_index", "")),
            _content_hash(content),
        ),
        content=content,
        source=source,
        source_url=metadata.get("source_url", ""),
        technology=str(metadata.get("technology", "") or ""),
        version=str(metadata.get("version", "") or ""),
        document_type=str(metadata.get("document_type", "") or ""),
        chunk_index=metadata.get("chunk_index"),
        content_hash=_content_hash(content),
        retrieval_score=item.get("score"),
    )


def check_candidate_identity(
    raw: dict, snapshot_files: dict
) -> str | None:
    """校验发现阶段候选的身份字段，返回拒绝原因或 None（通过）。

    snapshot_files: {相对路径: {"total_lines": int, ...}}（代码快照，不含省略文件）。
    检查：file 存在、行号在可见范围内、file/description 非空。
    """
    file = str(raw.get("file", "") or "").strip().replace("\\", "/").lstrip("/")
    if not file:
        return "缺少 file"
    if file not in snapshot_files:
        return f"文件不在审查快照内：{file}"
    line = raw.get("line")
    if line is not None:
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            return f"行号非法：{line!r}"
        total = snapshot_files[file].get("total_lines", 0)
        if line > total:
            return f"行号超出可见范围：{line} > {total}（{file}）"
    if not str(raw.get("description", "") or "").strip():
        return "缺少 description"
    return None


def build_bundle(
    candidate_id: str,
    evidences: list[Evidence],
    retrieval_status: str,
    note: str = "",
) -> EvidenceBundle:
    """构造候选的证据包；evidence_ids 与 evidences 保持一致。"""
    evidences = list(evidences)
    return EvidenceBundle(
        candidate_id=candidate_id,
        evidence_ids=[e.evidence_id for e in evidences],
        evidences=evidences,
        retrieval_status=retrieval_status,
        retrieval_note=note,
    )


def parse_verification(
    raw_text: str, bundle: EvidenceBundle, *, require_migration_fields: bool = False
) -> VerificationOutput:
    """解析并校验核实输出；任何非法都抛 VerificationError（调用方计为失败）。

    校验规则（方案四节）：
    - extra=forbid：未知字段即失败（Pydantic 层）。
    - document_supported：evidence_ids 必须非空且全部在 bundle 内。
    - inferred：不得引用证据 ID；confidence 不得为 high。
    - none：不得引用证据 ID；decision 不得为 confirmed。
    - confirmed：title/reason/severity/confidence/suggestion 必填；
      require_migration_fields=True（Migration）另需 current/target/change_reason。
    """
    try:
        output = VerificationOutput.model_validate_json(raw_text)
    except ValidationError as exc:
        raise VerificationError(str(exc)) from exc
    allowed = set(bundle.evidence_ids)
    used = list(output.evidence_ids)

    unknown = [eid for eid in used if eid not in allowed]
    if unknown:
        raise VerificationError(f"引用了未知证据：{', '.join(unknown)}")

    if output.evidence_status == "document_supported" and not used:
        raise VerificationError("document_supported 但未引用任何证据")
    if output.evidence_status == "inferred" and used:
        raise VerificationError("inferred 不允许引用证据 ID")
    if output.evidence_status == "inferred" and output.confidence == "high":
        raise VerificationError("inferred 的 confidence 不得为 high")
    if output.evidence_status == "none":
        if used:
            raise VerificationError("无证据状态不允许引用证据 ID")
        if output.decision == "confirmed":
            raise VerificationError("无证据不得确认问题成立")

    if output.decision == "confirmed":
        missing = [
            name
            for name, value in (
                ("title", output.title),
                ("reason", output.reason),
                ("severity", output.severity),
                ("confidence", output.confidence),
                ("suggestion", output.suggestion),
            )
            if not value
        ]
        if require_migration_fields:
            missing += [
                name
                for name, value in (
                    ("current_behavior", output.current_behavior),
                    ("target_behavior", output.target_behavior),
                    ("change_reason", output.change_reason),
                )
                if not value
            ]
        if missing:
            raise VerificationError(f"confirmed 缺少必填字段：{', '.join(missing)}")
    return output
