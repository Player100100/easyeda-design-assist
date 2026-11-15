# 项目级设计框架

项目档案用于组织项目级需求、架构、设计、集成、验证和交付工作。项目状态用于组织工作，不自动判定阶段通过或替代用户决定。原有 task.json 继续记录单项设计建议；项目 tasks 可通过 task_path 指向它。当前不增加 CAD 写接口。

## 用户如何配合

先选一个现有小板或一个范围有限的新板作为试点。提供项目名称/路径、用途、已有资料位置，以及必须保留的连接、器件、尺寸或功能。电源、负载、接口、环境、成本和希望先完成的阶段按已知情况说明；未知项写待定。用户负责确认用途、约束、方案取舍和接受结果。agent 负责查资料、计算、组织记录、拆解依赖和提出验证方案；实板测量由用户或有条件的工程人员配合。

暂未选试点时，可先完善通用档案和校验；不得擅自把已有工程当作已选试点，或填入未经确认的工程参数。

## 通用框架使用

```powershell
python -B src/design_assist.py new-project --name '试点名称' --goal '板卡用途与期望结果'
python -B src/design_assist.py project-guide --stage framing
python -B src/design_assist.py project-review '<project.json>'
python -B src/design_assist.py validate-project '<project.json>'
```

档案在项目旁 `easyeda-设计助手-data/projects/` 下生成。创建时各列表为空，不包含工程结论。目录内 README 给出填写顺序；由 agent 更新档案，用户无需直接编辑 JSON。新项目不调用 CAD 或要求其连接。`project-review` 报告阶段记录缺项、任务阻塞、外部任务关联状态和待复核项；它不签发阶段通过结论。

## 设计推进

1. 界定用途、范围、正常/启动/故障工况和交付目标；将结果性要求编号并安排验收方法。
2. 建立系统架构、电源树、数据/控制路径、模块接口和功耗/热/精度等预算。外部设备也可作为模块表达接口边界。
3. 对可能推翻架构的风险先查证、计算或安排原型试验；保留候选方案、选择理由和依据。
4. 按功能和依赖拆解模块任务，写明输入、产物和验收条件，再细化器件、引脚、连接和局部布局约束。
5. 集成时检查接口、启动、故障和共享资源；PCB 先考虑机械、层叠、制造规则和关键回路，再布局布线。
6. 冻结设计后独立审核制造文件和版本；实板验证回到原需求，失败进入变更记录。

以上步骤可以往返。需求改变后，先分析受影响接口、模块、预算、决策和测试，再修改设计。范围应按项目规模裁剪。设计依据、实际 CAD 连接、制造文件和实板测试分层记录，不能互相代替。

## 项目修改和复核

先在同一目录复制当前 `project.json` 到工作副本，再编辑工作副本。运行 `project-impact <旧档案> <编辑副本>` 查看显式依赖影响；确认变更原因后运行 `project-revise <旧档案> <编辑副本> --reason '<原因>'`。命令在外部 data 目录生成归档，保留输入文件原始字节、修订后的档案、影响报告和逐文件 SHA256 manifest。归档时会把相对 `task_path` 解析为基于编辑副本目录的绝对路径，避免移动归档后指向错误位置。

修订档案的 `pending_reviews` 包括受影响对象，即使对象已删除也保留删除对象的复核项。审阅后，把 `project-review` 返回的 `review_targets` 摘要填入 `review_records` 对应对象的 `reviewed_against`，并记录审阅结论与依据。摘要绑定整个设计快照；相关设计后续变化时，旧复核会重新打开。此机制负责追踪，不证明审阅充分。

`project-impact` 沿需求分配、接口/预算到模块、任务依赖、决策引用和验证引用传播，并同时比较变更前后的关系。未登记的电气、机械、固件、物理或工况关联无法被发现。全局目标/范围/假设/待决问题变化会标记全部对象复核。

## v1 字段约定

| 字段 | 填写内容 |
|---|---|
| scope / assumptions / open_questions | 范围、假设、待解决问题；列表为空不表示相关问题已排除 |
| requirements | id、statement、source、priority（must/should/could）、verification_method（analysis/inspection/demonstration/test/tbd）、status（open/allocated/verified/accepted/deferred）；建议另写 acceptance_criteria |
| modules | id、name、responsibility、requirement_ids；另记输入输出；外部系统可标 external |
| interfaces | id、modules（两个模块 ID）、contract（含信号方向、连接定义、电压/电流/时序/机械约束及未知项） |
| budgets | id、modules（模块 ID 列表）、analysis（单位、总约束、各项分配、余量和计算依据）；无适用预算时用顶层 budget_scope_record 说明 |
| decisions | id、status（open/selected/rejected/revisit）、requirement_ids、module_ids、rationale；另记候选、证据与用户决定记录 |
| tasks | id、status（blocked/ready/in_progress/done/deferred）、depends_on（任务 ID 列表）、requirement_ids、module_ids、goal、deliverable、acceptance_criteria；另记输入；task_path、task_id、task_sha256 须同时提供才能关联现有任务档案 |
| verifications | id、requirement_ids、method（同上）、result（planned/pass/fail/blocked）、conditions、acceptance_criteria；pass/fail 时补 evidence、tested_version、execution_record |
| sources | id、reference、locator、description；另记资料修订、时间、文件哈希及适用条件 |
| risks | id、statement、status（open/mitigating/closed/accepted）；关闭/接受时补 disposition_record |
| change_log / pending_reviews / review_records | 变更原因和影响报告 / 待复核对象键 / object、record、reviewed_against；例：object 为 tasks/T1，reviewed_against 从报告 review_targets 获取 |
| integration_record / validation_record / release_record | 顶层记录：集成版本和范围、真实使用情境确认、制造包版本和发布决定。分别由 integration / verification / released 阶段记录审查提示 |

各对象可用 `requirement_ids`、`module_ids`、`interface_ids`、`budget_ids`、`decision_ids`、`task_ids`、`verification_ids`、`source_ids`、`risk_ids` 登记依赖。列表元素为目标类别中的对象 ID；仅说明文字中提及 ID 不会建立可计算的依赖。任务创建命令仍独立工作，关联字段由 agent 写入项目档案，不会自动修改项目。

顶层 status：framing / architecture / design / integration / verification / released。v1 校验基本身份、需求枚举、对象 ID、跨对象引用和任务循环依赖；模块技术内容、数值预算、证据真实性、状态迁移和阶段过关仍由 agent/工程人员检查。外部证据有效性需要人工核对。修订归档是记录快照，不是 CAD 基线冻结，也不等同项目交接。

## 后续实施与试点验收

- 用真实试点完成需求表、模块接口表、关键预算和任务依赖；检查记录是否足以支持具体设计决策。
- 用真实任务检验变更影响传播遗漏，并评估是否需要 CAD 基线绑定和专门的任务关联命令。
- 项目规划验证后，另行设计有限绘图计划、执行与回读。按具体变更授权，不因流程批准而自动修改 CAD。

通用框架软件验收：无 EDA 可创建和审查档案；拒绝不存在的跨对象引用和任务循环依赖；保留原 task 流程兼容。真实工程验收需等待试点输入，软件测试不代表电路验证。

## 方法来源

- [NASA System Design Processes](https://www.nasa.gov/reference/4-0-system-design-processes/)：需求、架构、递归分解、方案比较与迭代。
- [NASA Product Realization](https://www.nasa.gov/reference/5-0-product-realization/)：实现、集成、验证与用户预期确认。
- [KiCad PCB Design Workflow](https://docs.kicad.org/9.0/en/getting_started_in_kicad/getting_started_in_kicad.html#pcb_design_workflow)：原理图/PCB 一致性、规则检查与制造输出。
- [JLCPCB Manufacturing Capabilities](https://jlcpcb.com/capabilities/pcb-capabilities)：制造约束的输入来源，实际下单时重新核对。

以上作为方法参考并按单板规模裁剪；不宣称本项目已通过 NASA 或行业流程认证。
