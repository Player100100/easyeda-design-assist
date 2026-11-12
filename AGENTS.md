# 项目执行约定

先阅读 `CLAUDE.md`、`docs/architecture.md`、`docs/workflow.md` 与 `docs/agent-guide.md`；处理绘板问题时再读对应 `docs/playbooks/<模式>.md`。

- 当前只做设计期只读辅助，不调用 CAD 写接口。
- 项目为 agent 提供工程思路和只读信息工具；手册按问题查阅，选型/拓扑建议应先查询关联元件和网络。
- 入口为 `python -B src/design_assist.py`；`guide` 返回工作指引，`schematic`/`inspect` 提供原理图连接查询。
- bridge 仅执行固定读取操作；新增 API 调用先查本机 `easyeda-api-skill` 的签名与返回类型。
- `connect` 仅管理本地 bridge 进程，不通过 bridge 执行任意脚本。手册原件保持可访问，搜索只用于定位，不预生成分类摘录或认定参数。
- 核心代码使用 Python 标准库。运行产物写在外部数据目录，原始上下文与交接记录不覆盖。
- 建议引用来源 ID，并保留适用条件、风险、验证动作和用户确认记录；字段完整性不等于工程正确性。
- 修改代码后运行 `python -B -m unittest discover -s src -p 'test_*.py'`。
