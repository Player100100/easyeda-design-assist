# 双模式与决策执行链路：首期构建

2026-11-18 完成已有网络支路 v5 的 3 步实机复测。能力目录标明版本和实测范围，预检拒绝重叠线组，以及进入已核验 A4 图框标题栏保留区的新增支路。[支路及图框避让记录](branch-editing.md)

2026-11-17。首期核心代码使用 Python 标准库，不增加服务或运行依赖。CAD 查询及执行继续依赖现有客户端、API Gateway 和 Bridge。

## 交互模式、工程问题与分析尺度

三者分别记录，不互相代替：

| 维度 | 字段与取值 | 用途 |
|---|---|---|
| 交互模式 | `workflow.interaction`：`advice` / `collaborative` | 默认辅助绘制；用户明确要求协助绘制后准备协同方案 |
| 工程问题 | 任务的 `mode`：原有六类任务 | 选择手册、选型、拓扑、原理图、PCB 或局部检查指引 |
| 分析尺度 | `workflow.analysis_scope`：`component` / `module` / `board` / `product` | 采用能闭合问题的最小范围，保留上一级接口上下文 |

任务默认 `advice`、`module`，项目默认 `advice`、`board`。`workflow` 是新增的可选字段，旧任务与旧项目无需补齐即可读取。`workflow` 命令只返回指引，不连接 CAD，也不创建档案：

```powershell
python -B src/design_assist.py workflow --scope component --missing ratings
python -B src/design_assist.py workflow --scope component --signal budget_conflict
python -B src/design_assist.py workflow --interaction collaborative --signal cross_board
python -B src/design_assist.py new-task --mode topology --goal '比较接口方案' --scope component --signal shared_power
```

`shared_power`、`shared_timing`、`budget_conflict`、`unclear_module_boundary` 将范围至少提升为 `board`，`cross_board` 至少提升为 `product`。这些信号由 Agent 根据证据登记；代码只确定性地执行提升规则，不自动发现电气耦合。`--missing` 支持 `identity`、`geometry`、`connectivity`、`ratings`、`capability`、`use_case`，输出下一步查证提示，不会自动补齐事实。

个人研发采用“目标与边界 → 方案与电路 → PCB 与投板 → 上电与联调 → 验证与改版”五步循环。它是交互指引，不新增审批门，也不替换旧项目六阶段状态；局部问题只走相关步骤。交互模式和分析尺度均不授予 CAD 写入权限。

## 操作目录与固定查询

`operations.py` 是策略操作名、适配器分配与能力说明的共同来源。策略允许、代码实现、客户端版本匹配、真实验证范围分别表达：

```powershell
python -B src/design_assist.py capabilities --editor-version 4.1.100
python -B src/design_assist.py query --kind context
python -B src/design_assist.py query --kind snapshot --save
python -B src/design_assist.py query --kind source-component --primitive-id '<画布器件ID>' --save
```

`query --kind` 支持 `context`、`snapshot`、`components`、`pins`、`connectivity`、`source-component`、`source-groups`，后续迭代增加 `audit`；`pins` 也要求 `--primitive-id`。多窗口时指定 `--window-id`。旧查询命令继续可用。统一返回保留原始数据、目标、时间、方法、可取得的编辑器版本和缺项；Source 固定查询另存脚本 SHA256，`--save` 写入外部数据目录 `queries/`。

Source 查询先核对当前页 UUID 与工程、原理图名称和页名称的映射，再检查读取前后的完整源与身份。功能分组是派生线索，不能当作已确认功能模块。查询之间独立采集，不能视为原子快照，也不能跨板或快照按同名网合并。网表查询继续走原有路径，不依赖尚待验证的 Source Netlist/DRC。

`available` 只有在已开放调度且版本精确匹配时为真，仍须查看 `write_validation` 与 `limitations`。原有 v2/v3 保持 `3.2.149.88089769` 限制，固定脚本执行前再次检查实时版本。Source 适配器面向 `4.1.100`，当前 `dispatch_enabled=false`。2026-11-18 该版本另行开放[非空白页独立增量 v4](incremental-editing.md)，三电阻闭环 9 步已实测；支持已有电路旁新增独立模块，原电路及详细 DRC 必须保持。离线测试不能扩展真实参数的验证范围。

## 建议、决策、计划与结果

新建绘图计划为 `schema_version=2`，使用 `interaction=collaborative` 和 `origin`。可把具体任务建议及项目决策绑定到计划：

```powershell
python -B src/design_assist.py new-drawing-plan '<项目.json>' --request '具体绘图请求' --task '<任务.json>' --recommendation-id R1 --decision-id D1
python -B src/design_assist.py review-drawing-plan '<项目.json>' '<计划.json>'
python -B src/design_assist.py prepare-drawing-execution '<项目.json>' '<计划.json>'
python -B src/design_assist.py drawing-trace '<项目.json>' '<计划.json>'
```

`--recommendation-id` 和 `--decision-id` 可重复指定。任务引用保存外部绝对路径、文件原始字节 SHA256、任务 ID 和建议摘要；决策保存 ID 和内容摘要。引用任务须解决 `missing_inputs`，所选建议不能为 `needs_input`。简单任务也可不绑定来源对象，但计划仍须有可审阅的依据、操作、目标与回读安排；不绑定时 `origin` 为 `{"task": null, "decisions": []}`。

离线 `review-drawing-plan` 检查引用结构；预检、认可和执行重新检查引用文件及摘要。任何被引用任务文件字节或决策变化都会要求复核，当前不做逐字段语义失效判断。建议标为 `accepted` 不等于具体绘图方案已认可。

计划格式版本与适配器版本是两个维度：v2 计划也可使用旧器件放置适配器或实验 v3 电路适配器。新计划的认可绑定带 `binding_version=2`：项目设计内容、策略、工作流、多板上下文、任务依赖和未知扩展字段仍参与摘要；仅排除顶层 `created_at`、`updated_at`、`status`、`change_log`、`review_records`、`pending_reviews` 和项目任务条目的 `status`。纯进度更新不会使认可失效。v1 计划继续绑定整个项目对象，历史认可不会自动获得新语义。

执行继续沿用单次领取、目标锁、最多 50 步、逐步 intent/reply/snapshot 和失败留现场的公共流程。没有新增跨批次授权。结果保存计划摘要、项目 ID、来源和工程验证边界；绑定任务另以 Base64 保存原始字节到 `origin-task-evidence.json`。原任务与原决定不自动覆盖；`drawing-trace` 返回来源失效项及已有结果，将反馈留作独立记录。来源文件丢失或改变后仍能查看已归档结果，但会报告需复核。外部手册/计算附件不会随任务 JSON 自动复制。

## 可选多板上下文

`product-review '<product.json>'` 离线检查独立产品对象；同一对象也可放入项目的 `product_context`。最小结构如下，所有值均为演示数据，快照摘要需替换为实际证据：

```json
{
  "schema_version": 1,
  "id": "demo-product",
  "boards": [{
    "id": "controller", "project_uuid": "demo-project", "revision": "A",
    "snapshot_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "connectors": [{"id": "J1", "pins": ["1", "2"]}]
  }],
  "instances": [
    {"id": "left", "board_id": "controller"},
    {"id": "right", "board_id": "controller"}
  ],
  "connections": [{
    "id": "link-1",
    "a": {"instance": "left", "connector": "J1", "pin": "1"},
    "b": {"instance": "right", "connector": "J1", "pin": "2"},
    "signal": "TBD", "voltage": "TBD", "return_path": "TBD",
    "cable_mapping": "TBD", "evidence": []
  }]
}
```

板型与安装实例分开；端点须明确实例、连接器与引脚，不推断同名网络。校验拒绝不存在的端点、自连和重复连接，报告电平、回流、线缆映射或来源的缺项。快照摘要目前只检查格式，不自动查找各板文件或验证实际连接；`engineering_verified` 始终为 `false`。机械装配、电气兼容和实物接线仍待相应证据。

项目中的 `workflow` 或 `product_context` 变化接入现有全局变更传播，要求复核已登记对象；当前是保守传播，不是精确的板间电气影响推导。原项目的功能模块模型继续独立使用。

## 验证与剩余范围

2026-11-17 首期验证：165 项单元与固定脚本测试全部通过，无跳过；`python -B -m compileall -q src`、`workflow --scope component --missing ratings`、`capabilities --editor-version 4.1.100`、`modes` 离线冒烟及 `git -c core.whitespace=cr-at-eol diff --check` 均通过。测试涵盖双模式/尺度、依据变更阻断、v1/v2 认可差异、历史复核摘要兼容、来源归档、多板端点，以及 Source 延迟回读、属性保留、目标改变、超时和调度阻断。测试中的模拟 CAD 不是实机写入证明。

同日通过现有 Bridge 执行 `query --kind context --save` 的真实只读冒烟，返回 `design-query`，选中对象状态为 `captured`；旧 context 路径未提供编辑器版本，统一结果将 `editor_version` 保留为缺项。证据保存在外部数据目录 [queries/query-20261117T091921Z-3f304e7cea78.json](../../easyeda-设计助手-data/queries/query-20261117T091921Z-3f304e7cea78.json)。本次未调用 CAD 写入。

同日后续迭代完成[独立网表/详细 DRC 差分与执行反馈](audit-and-feedback.md)，增加 22 项测试，完整回归共 187 项全通过，无跳过。两次真实只读审计、输入归档与比较重算已验证；未进行 CAD 修改，派生刷新证据仍未知。上述 165 项为首期历史验证数量。

同日后续在用户明确授权的当前实验工程完成[Source 单属性实测与恢复](source-validation.md)。新增 `source_trial.py` / `execute-source-trial` 绑定 case 摘要、真实授权、实时完整基线与历史锁清单，独立归档单次写入及现场复核，不改变正式能力开关。U2 Name 正向写入出现额外 `zIndex:16→15`，严格源核验保留失败；恢复 Name 后完整网表与详细 DRC 恢复原基线，残留 `zIndex` 差异留档。此前“本次未写入”是对应迭代的历史记录。

新版 Source 属性适配器详见 [Source 局部属性契约](source-properties.md)。继续待完成的是界定源副作用、取得与 Source 版本关联的派生刷新证据，以及把完整差分接入正式自动验收；当前仍关闭。网络标签/端口修改、已连线对象移动、导线分量编辑、删除替换、PCB 自动布局布线、原生多板装配和制造发布不属于本次首期交付。历史 v3 有真实尝试与局部修复记录，通用电路端到端实机验收仍未完成，见外部可实施性评估；本次不重写这些历史状态。

同日再后续完成[层级历史恢复和经典属性接口实验](source-experiment.md)，所有源/完整网表/详细 DRC 已恢复最初基线，无残留。CLI 实验默认后端改为 `canvas-attribute`，已实测 U2 Name 与 C3 Value，新增 `check-source-roundtrip` 区分预先声明的局部规则响应和完整恢复。原 Source 路径失败、C3 探索中的未预期警告及全部保守执行结果继续保留，正式调度不开放；局部实验完成与全局派生刷新关联分别记录。
