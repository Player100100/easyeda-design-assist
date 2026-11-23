"""Agent-facing task packets and frozen handoff artifacts."""
from __future__ import annotations

from pathlib import Path

from .contracts import MODES, validate_task
from .guidance import guide
from .storage import digest, external, initialize, new_id, now, read_json, write_new
from .workflow import INTERACTIONS, context as workflow_context


def create_task(mode: str, goal: str, constraints: list[str], context=None, *,
                interaction="advice", scope="module", signals=(), missing=()) -> Path:
    task = {"schema_version": 1, "id": new_id("task"), "mode": mode,
            "goal": goal, "created_at": now(), "status": "draft",
            "context": context, "constraints": constraints,
            "missing_inputs": list(MODES[mode][1]), "sources": [], "recommendations": [],
            "workflow": workflow_context(interaction, scope, signals, missing)}
    validate_task(task)
    root = external(initialize() / "tasks" / task["id"])
    root.mkdir(exist_ok=False)
    path = write_new(root / "task.json", task)
    # Evidence is separate from the editable task to preserve the original context.
    write_new(root / "initial-context.json", context)
    (root / "brief.md").write_text(
        f"# {MODES[mode][0]}\n\n目标：{goal}\n\n"
        f"交互模式：{INTERACTIONS[task['workflow']['interaction']]}（{task['workflow']['interaction']}）\n\n"
        f"分析范围：{task['workflow']['analysis_scope']}；范围信号：{', '.join(task['workflow']['scope_signals']) or '无'}。\n\n"
        "模式与分析范围不授予 CAD 写入权限；具体方案认可和执行预检仍独立进行。\n\n"
        "请按 docs/workflow.md 补充工况与来源，更新 task.json。初稿不包含分析结论。\n\n"
        "待补输入：\n\n" + "\n".join(f"- {x}" for x in task["missing_inputs"]) +
        "\n\n记录完整性校验：`python -B src/design_assist.py validate-task <task.json>`\n\n" + guide(mode),
        encoding="utf-8")
    return path


def handoff(task_path: Path) -> Path:
    task = read_json(task_path)
    validate_task(task)
    root = external(initialize() / "handoffs" / new_id("handoff"))
    root.mkdir(exist_ok=False)
    frozen = write_new(root / "task.json", task)
    manifest = {"schema_version": 1, "created_at": now(), "task_id": task["id"],
                "scope": "设计意图与建议记录，须由审核流程独立核验",
                "files": [{"path": "task.json", "sha256": digest(frozen)}],
                "limitations": ["未复制 sources 引用的外部手册、快照或计算文件；接收方需确认仍可访问。",
                                "未绑定冻结的 .epro2 版本，不能作为该版本已审核的证明。"]}
    return write_new(root / "manifest.json", manifest)
