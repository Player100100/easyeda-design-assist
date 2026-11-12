# 任务档案格式

`new-task` 创建 JSON 初稿。结论应在交互分析后补入 `recommendations` 和 `sources`，然后运行 `validate-task`。

每条 recommendation 至少包括：

| 字段 | 含义 |
|---|---|
| `id` | 本任务内稳定 ID |
| `status` | `suggested`、`needs_input` 或 `verified` |
| `locator` | 工程对象定位；无对象时写任务范围 |
| `statement` | 明确建议或待确认事项 |
| `rationale` | 与工况/要求的联系和必要推导 |
| `evidence` | 可追溯来源；不可只写“经验” |
| `applicability` | 非空数组，说明建议适用的对象、工况和边界 |
| `assumptions` | 尚未验证的输入假设；没有则为空数组 |
| `risks` | 采用建议的风险和未覆盖项 |
| `confidence` | `low`、`medium` 或 `high`，描述证据强弱 |
| `validation` | 下一步核验动作/测量/计算 |
| `decision` | `pending`、`accepted`、`rejected` 或 `deferred` |
| `decision_record` | 决定不是 pending 时必填，记录用户明确确认及时间 |

`validate-task` 是记录完整性校验，不验证电气/物理正确性。

`sources` 每项字段：`id`（唯一）、`kind`（如 datasheet、cad、calculation、project-requirement）、`reference`（文件或 URL）、`locator`（页码/章节/JSON 字段/对象定位）、`description`（支持的事实，包含型号与文档修订信息）。建议的 `evidence` 存来源 ID；不能引用未登记来源。

任务初稿包含 `context`（可以为 null）、`constraints`、`missing_inputs`、`sources`、`recommendations`。上下文契约支持 v1（仅选中 ID）和 v2（额外含所选图元属性）。v2 的 `selection_details_status` 为 `captured`、`unavailable`、`id_mismatch` 或 `truncated`；每个已采集对象带 `supported`、`read_errors` 和 `missing_fields`。`captured` 表示对象身份与数量匹配，不表示每个字段都成功读取；属性键保持 API 原名。最多读取 100 个对象详情，截断时仍保留全部 ID。单位为原始 API 值，未完成实板校准；不从局部对象推断完整连接关系。

手册导入派生新的任务 ID，并通过 `parent_task` 保留原任务 ID、路径和哈希；不覆盖父任务。手册页码/行号摘录可以登记为 `sources`，字段 `review_status` 为 `candidate` 或 `reviewed`。未核对候选不能用于 `verified` 建议；人工核对后 `reviewed` 必须有非空 `review_record`。手册原文、逐页文本、完整型号与修订声明、文件 SHA256 保存在外部 data 目录，任务中记录可追踪路径。`handoff` 目前只冻结 task JSON，不会自动复制原件。

初始缺项是分析提示，由 agent 根据实际证据补齐后更新，不会仅凭已输入约束自动清除。`ready_for_user`/`accepted` 不允许残留关键缺项；需要带假设讨论时使用 `analyzing` 或 `needs_input`。`verified` 仅记录这条建议的核验状态，不等于整板审核通过。

每个任务目录包含可编辑的 `task.json`、初始上下文 `initial-context.json` 和包含通用/模式指引的 `brief.md`。后续重新采集使用新任务，不覆盖初始上下文。`handoff` 每次创建新目录，保存这次记录和校验和，未归档外部证据的限制随清单列明。

原理图快照可登记为 `kind: schematic-connectivity` 来源，reference 为快照路径，locator 说明位号/网表元件键/引脚/网络与扩展范围，附原始网表哈希。不同快照单独登记；范围未核实时不写成全板证据。新导入手册只保留访问清单与索引，不生成分类摘录，旧摘录来源仍兼容。
