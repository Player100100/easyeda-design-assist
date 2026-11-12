# EasyEDA 设计助手

## 目标

在用户绘制嘉立创EDA设计时，结合当前工程上下文、器件资料和明确约束，提供可追溯的选型、拓扑、原理图和 PCB 建议。现阶段以只读建议为边界。

## 边界与规则

- 项目 API 访问默认只读。不得通过 bridge 执行任意用户/模型生成的 JavaScript；只调用 `src/design_assist.py` 内固定的读取操作。
- 不调用创建、修改、删除、保存、自动布线、铺铜或切换工程等 API。未来写入能力须单独设计，并由用户对具体变更明确授权。
- 区分事实、建议、假设和未知。每条工程建议记录适用条件、证据来源、潜在风险、置信度和验证办法。
- 不根据网名猜测功能，不把常见电路当成设计要求。器件参数引用具体手册版本、页码/章节；缺依据时标为待验证。
- 选型建议同时考虑关键额定值、工作条件、封装/引脚、供货与替代风险；未核实时不声称可直接替换。
- PCB 布线/铺铜建议必须声明依赖的电流、层叠、回流路径、阻抗、板厂规则等条件；缺条件则先询问或给定假设。
- 用户的设计意图、约束和采纳决定须留在任务记录中，不覆盖原始 CAD 文件。
- 任务数据写入项目旁的 `easyeda-设计助手-data/`，不要把实时快照、手册副本或任务档案写入源码目录。
- 新任务开始前阅读 `docs/architecture.md` 与 `docs/workflow.md`；涉及 API 时使用本机 `easyeda-api-skill` 的最新 API 文档。

## 当前能力

- `python -B src/design_assist.py init`：初始化外部数据目录。
- `python -B src/design_assist.py status`：查询 bridge 与连接窗口状态。
- `python -B src/design_assist.py context`：只读获取当前工程、焦点文档、所选图元 ID 和固定类型属性，生成 v2 上下文。
- `python -B src/design_assist.py new-task --mode <模式> --goal <目标>`：创建结构化辅助任务档案。
- `python -B src/design_assist.py validate-task <任务.json>`：检查建议是否具备证据、假设、风险与验证办法。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 架构、工作流、接口契约 |
| `src/` | 只读 bridge、任务与记录工具 |
| `design/` | 项目级静态设计输入，不存实时输出 |
| `notes/` | 决策、调研和经验记录 |
| `sim/` | 按需仿真方案与结果说明 |
