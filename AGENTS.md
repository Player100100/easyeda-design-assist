# 项目执行约定

先阅读 `CLAUDE.md`、`docs/architecture.md`、`docs/workflow.md` 与 `docs/agent-guide.md`；处理绘板问题时再读对应 `docs/playbooks/<模式>.md`。

- 用户明确提出辅助绘制时，按 `docs/autodraw-policy.md` 先生成具体方案；用户认可后允许在方案范围内调用受约束的 CAD 写接口，无需逐步重复确认。超范围或目标改变先更新方案。v2 器件放置已通过单器件实测；v3 保留实验契约，已有历史真实尝试和局部修复，通用端到端实机验收未完成，见 `docs/circuit-executor.md` 与 `docs/live-api-verification.md`。
- 原理图绘制和重排遵循 `docs/playbooks/schematic.md`：多模块用框和功能标题分区，先对齐再压缩走线，避开标题栏并给文字留净距。记录版式取舍，画后分别复核 SVG、完整网表和详细 DRC。
- 非空白页独立增量新增采用 `schematic-incremental-v4`，4.1.100 三电阻闭环 9 步已实测；原电路全部记录、完整网表和详细 DRC 须保持，见 `docs/incremental-editing.md`。用户明确委托自行设计/执行实验时记录真实委托与前瞻方案，在该范围继续，无需重复询问。独立实验清理不接入正常执行器；不得据此开放旧对象删除或正式 Source。
- 已有网络新增支路使用独立 `schematic-branch-v5`，4.1.100 一个电阻接入两条旧网络的实验已通过。目标线组接触其他线组时预检拒绝，目标组允许在线段与样式精确核验下重分配 LINE 几何。完整旧网表和详细 DRC 须保持。v4/v5 有框页使用同源原生 SVG 核对内框与标题栏净距，实测范围及旧基线兼容性见 `docs/branch-editing.md`。
- 首期双模式/分析范围、能力清单、来源追踪与可选多板记录见 `docs/workflow-build.md`。模式不授予写入权限；Source 属性适配器当前关闭，真实隔离验证前不得开放，见 `docs/source-properties.md`。
- 当前实验工程已获用户明确测试授权；独立实验 CLI 默认 `canvas-attribute`，已完成 Name/Value 往返和历史层级恢复，边界与证据见 `docs/source-experiment.md`。后端须显式留档，不在失败后自动换接口；局部实验完成不开放正式 Source 调度。
- 项目为 agent 提供工程思路和只读信息工具；手册按问题查阅，选型/拓扑建议应先查询关联元件和网络。
- 入口为 `python -B src/design_assist.py`；`guide` 返回工作指引，`schematic`/`inspect` 提供原理图连接查询。
- 四层板及两层升四层遵循 `docs/pcb-four-layer.md`：先确定板厂物理叠层、层分工和换层回流，保护指定的连续 GND 层，普通布线不能为解拥塞占用参考面。层数、直流连通及 DRC=0 与工程验收分别记录；具体叠层按工况比较，不固定套用。
- bridge 仅执行源码固定的读取、受限原理图操作及实验性 PCB 操作；新增 API 调用先查本机 `easyeda-api-skill` 的签名与返回类型。PCB 按 `docs/pcb-automation.md` 绑定已完成原理图及完整 PCB 基线，选择整板、部分、建议或布局/关键网络范围；实际写入须具体方案认可、显式实验选择和逐步回读，不能把离线通过当作实机验收。
- `connect` 仅管理本地 bridge 进程，不通过 bridge 执行任意脚本。手册原件保持可访问，搜索只用于定位，不预生成分类摘录或认定参数。
- 核心代码使用 Python 标准库。运行产物写在外部数据目录，原始上下文与交接记录不覆盖。
- 建议引用来源 ID，并保留适用条件、风险、验证动作和用户确认记录；字段完整性不等于工程正确性。
- 修改代码后运行 `python -B -m unittest discover -s src -p 'test_*.py'`。
