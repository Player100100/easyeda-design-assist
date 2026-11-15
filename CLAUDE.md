# EasyEDA 设计助手

## 目标

为 agent 提供辅助绘板的工程思路和嘉立创EDA只读信息工具，弥补其辅助绘板时的方法缺口。绘制者提问后，agent 查询实际元件与网络、按需查阅原手册、推导并给出建议。

## 边界与规则

- 未认可具体绘图方案时 API 访问只读；认可后遵循绘图策略执行。不得通过 bridge 执行任意用户/模型生成的 JavaScript；CLI 仅接入固定操作。
- 自动绘图按 `docs/autodraw-policy.md` 执行：用户明确要求后先生成具体方案，展示对象、差异、目标文档和未决项；用户认可方案后，可在其范围内调用已核验的写入 API。方案外修改须更新方案并再次认可。
- 写入适配器 v2 支持原理图器件放置，单器件实机验证已通过（90° 请求、270° 画布、BOM/PCB 关闭）；v3 实验性支持空白原理图新增器件、网络标号和导线，最终核对全部引脚网表及详细 DRC。v3 尚未实机验证，契约见 `docs/circuit-executor.md`；属性修改、PCB 写入未实现，不能根据模拟成功声称真实 CAD 已写入。
- 区分事实、建议、假设和未知。每条工程建议记录适用条件、证据来源、潜在风险、置信度和验证办法。
- 不根据网名猜测功能，不把常见电路当成设计要求。器件参数引用具体手册版本、页码/章节；缺依据时标为待验证。
- 选型建议同时考虑关键额定值、工作条件、封装/引脚、供货与替代风险；未核实时不声称可直接替换。
- PCB 布线/铺铜建议必须声明依赖的电流、层叠、回流路径、阻抗、板厂规则等条件；缺条件则先询问或给定假设。
- 用户的设计意图、约束和采纳决定须留在任务记录中，不覆盖原始 CAD 文件。
- 任务数据写入项目旁的 `easyeda-设计助手-data/`，不要把实时快照、手册副本或任务档案写入源码目录。
- 新任务开始前阅读 `docs/architecture.md`、`docs/workflow.md`、`docs/agent-guide.md` 和对应模式 playbook；涉及 API 时使用本机 `easyeda-api-skill` 的最新 API 文档。

## 当前能力

- `python -B src/design_assist.py init`：初始化外部数据目录。
- `python -B src/design_assist.py status`：查询 bridge 与连接窗口状态。
- `python -B src/design_assist.py connect`：复用或后台启动已安装的本地 bridge，等待 EDA API Gateway 接入；Windows 可运行 `start-api.cmd`。
- `python -B src/design_assist.py context`：只读获取当前工程、焦点文档、所选图元 ID 和固定类型属性，生成 v2 上下文。
- `python -B src/design_assist.py new-task --mode <模式> --goal <目标>`：创建结构化辅助任务档案。
- `guide --mode selection` 提供选型方法；新任务 brief 自动包含对应指引。
- `schematic --save` 归档只读原理图网表；`inspect <快照> --designator U1` 查询关联元件及网络。
- `import-datasheet <任务.json> <文件>` 保留手册原件和文字索引；`datasheet-search` 按需查询，`datasheet-page` 查看完整页。
- `python -B src/design_assist.py validate-task <任务.json>`：检查建议是否具备证据、假设、风险与验证办法。
- `new-drawing-plan <项目.json> --request <目标>` / `review-drawing-plan <项目.json> <计划.json>`：离线建档与检查绘图方案，尚不执行写入。
- `drawing-snapshot` / `prepare-drawing-execution` / `approve-drawing-plan` / `execute-drawing-plan`：完整源快照、方案预检、真实认可记录和受限放置；细节见 `docs/autodraw-policy.md`。

`connect` 只启动 bridge 并报告客户端状态，不启用/安装扩展，不切换窗口或 CAD 文档。手册按问题查阅，搜索不证明参数事实；不同快照或板卡不得仅按同名网合并。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 架构、工作流、接口契约 |
| `src/` | 固定 bridge 适配、任务与记录工具 |
| `design/` | 项目级静态设计输入，不存实时输出 |
| `notes/` | 决策、调研和经验记录 |
| `sim/` | 按需仿真方案与结果说明 |
