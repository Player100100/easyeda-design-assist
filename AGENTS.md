# 项目执行约定

先阅读 `CLAUDE.md`、`docs/architecture.md` 与 `docs/workflow.md`，沿用其项目边界与证据要求。

- 当前只做设计期只读辅助，不调用 CAD 写接口。
- 入口为 `python -B src/design_assist.py`；模式仅表示任务类别，不表示分析器已经实现。
- bridge 仅执行固定读取操作；新增 API 调用先查本机 `easyeda-api-skill` 的签名与返回类型。
- `connect` 仅管理本地 bridge 进程，不通过 bridge 执行任意脚本。手册抽取仅提供带出处的候选，未人工核对不得当成事实。
- 核心代码使用 Python 标准库。运行产物写在外部数据目录，原始上下文与交接记录不覆盖。
- 建议引用来源 ID，并保留适用条件、风险、验证动作和用户确认记录；字段完整性不等于工程正确性。
- 修改代码后运行 `python -B -m unittest discover -s src -p 'test_*.py'`。
