# 自动绘图触发与方案认可策略

用户已解除全面禁止写入 API 的限制：明确提出辅助绘制时，先生成方案并取得认可，然后允许在方案范围内写入。这个规则替代旧的“当前禁止所有写接口”规则。

## 默认配置

新项目档案包含以下 `drawing_policy`：

```json
{
  "mode": "explicit_batch",
  "trigger": "explicit_user_request",
  "allowed_operations": [
    "add_wire", "edit_wire", "move_component", "place_component",
    "place_graphic", "place_net_flag", "rotate_component", "set_property"
  ],
  "require_batch_approval": true,
  "require_snapshot_binding": true
}
```

`explicit_batch` 中的 batch 指认可方案所覆盖的一组操作，可以包含多个按依赖执行的步骤，不要求每一步重新确认。白名单表示策略允许的类别，不代表这些 API 已接入、已测试或可以绕过方案认可。

| 模式 | 行为 |
|---|---|
| `off` | 不新建绘图计划；可检查已有计划，但报告策略关闭 |
| `draft_only` | 只准备方案，不能执行写入 |
| `explicit_batch` | 用户认可具体方案后，允许在方案范围内写入 |

旧项目未设置策略（或为 `null`）时按 `draft_only` 处理；升级代码不会隐式放开已有项目。需要时修改外部项目档案，运行 `validate-project`，并用 `project-revise` 保留变更记录。仅 `explicit_batch` 允许非空操作白名单。

## 触发条件

由 agent 判断用户意图，不按单个关键词或项目状态自动触发。

| 用户表达或事件 | 对应动作 |
|---|---|
| “帮我画这个供电模块”“辅助绘制这部分接口” | 收集已有输入，生成具体方案，展示后等待认可 |
| “按照刚才的目标生成绘图方案” | 创建方案初稿，补齐对象与差异 |
| “这个方案可以，按它绘制” | 明确指向刚展示的具体方案时，记为认可；执行前核对范围与目标 |
| “这样设计合理吗”“自动绘图怎么实现” | 分析回答，不作为绘制任务或方案认可 |
| 项目建档、任务完成、阶段通过、建议标为 `accepted` | 更新记录，不自动触发写入 |
| 窗口/文档改变、需要方案外修改、关键前提失效 | 暂停，更新受影响方案并重新取得认可 |

用户明确要求绘制即可开始准备，不再询问是否允许生成方案。认可可以使用自然语言，不要求用户输入哈希或专用口令；agent 应将真实对话中的认可绑定到展示过的方案版本、目标和范围。有清晰且仍有效的认可记录就继续执行，不重复询问。仅有笼统开发授权、历史其他方案的同意或模型自行填写的 `approval` 字段，不构成本方案认可。

## 先形成可审阅方案

方案至少写清目标窗口、工程与文档、输入快照、对象清单、修改前后差异、预期连接/属性、依据与未决项，以及回读检查和失败处理方式。缺少目标或关键工况时继续独立分析，只询问影响方案的必要信息；不自行填造引脚、型号、数值或坐标。

复杂任务先按项目框架拆为需求、模块与接口、预算、风险、集成和验证，再把可执行部分整理为有输入、产物、依赖及验收判据的绘图步骤。整板概念框图不等于可执行绘图方案；不可把对概念方案的认可扩大为任意整板写入权限。

## 写入前核对

- 用户已明确认可当前方案；每项写入均在目标和允许操作范围内。同一认可方案内可连续执行多个步骤。
- 目标窗口、工程、焦点文档与计划一致。执行前核对快照和实际对象；快照文件的哈希只能证明文件内容，不能证明画布未被编辑。
- 完整型号、引脚/封装、连接关系、数值、坐标/单位及必要工况已有依据；影响当前步骤的关键未知已解决。
- 实际使用的 API 签名和返回类型已按本机 `easyeda-api-skill` 核验，并已有受约束的适配实现。不能把任意模型生成的 JavaScript 直接交给 bridge。
- 将真实认可记录、方案摘要、目标身份、操作及结果写入外部数据目录。计划变化需要更新摘要；保存认可字段不会自动验证或触发执行。

白名单以外的删除/替换、自动布线、铺铜、板框/叠层改变、保存覆盖及发布，须先明确扩展操作策略和方案范围。禁止将这些行为隐藏在 `set_property` 或其他宽泛操作名下。

## 执行与回读

按依赖顺序执行，每步记录返回值并回读受影响对象与连接，核对预期结果。目标身份变化、部分失败、结果不符或无法回读时立即停止后续操作，报告已完成项与未知状态，不盲目重试或自行扩展方案修补。不能假定 CAD 支持事务或自动回滚。

结果与方案一致仅表示操作核对通过；仍需适用的 ERC/DRC、独立设计审核及板级验证。

## 当前已实现的范围

```powershell
python -B src/design_assist.py new-drawing-plan '<项目.json>' --request '绘图目标'
python -B src/design_assist.py review-drawing-plan '<项目.json>' '<plan.json>'
```

第一个命令在外部数据目录 `drawing-plans/<编号>/plan.json` 创建待填初稿，由 agent 基于真实信息完善，不自动完成工程设计。第二个命令检查记录缺项、操作类型、重复 ID、未决事项和策略范围，并返回排除 `approval` 字段后的方案摘要。

`record_complete` 只表示记录完整；`within_policy_scope` 只表示策略范围符合。检查器不读取或验证快照内容，不校验工程正确性，也不验证用户认可。CLI 返回成功表示检查完成，即使报告存在缺项也可能退出码为 0；调用者须读取报告字段。

`review-drawing-plan` 的 `execution_available` 始终为 `false`，表示该检查命令不执行方案；`supported_execution_operations` 列出已实现的操作类别，`approval_verified` 始终为 `false`。配置或计划中的 `approval` 字段本身不会修改 CAD。

## 原理图器件放置执行器

未设置 `execution_contract` 的旧计划使用 `schematic-place-component-v2`，仅执行 `place_component`。本版仅支持工程中已有 DEVICE 且 `META.source` 可证明来源的器件，以及已核验编辑器 `3.2.149.88089769` 的角度约定；缺少映射时在写入前拒绝。支持一个认可方案中的多个器件，最多 50 步。v2 不能创建导线、修改属性或操作 PCB。首次真实写入已创建 R4，旧版回读失败原因已查明；2026-11-14 v2 单次真实创建 R5 及独立回查通过，实测为 FRC0603F5101TS、请求 90°/画布 270°、不镜像、BOM/PCB 关闭。其他角度、镜像、多步及标记组合尚未实测，见[实测记录](live-api-verification.md)。接口文档示例将原理图坐标记作 10mil，尚未完成实板校准。

新增实验契约 `schematic-circuit-v3` 可在空白原理图上新增器件、网络标号和导线，允许从已归档库器件导入，最终核对完整引脚网络及详细 DRC。v3 尚未实机验证；参数与 v2 不同，完整说明和样例见[电路执行器契约](circuit-executor.md)。以下器件参数示例仍为 v2。

1. `python -B src/design_assist.py drawing-snapshot --window-id '<窗口ID>'` 只读采集当前原理图页完整文档源、窗口、工程、页和 tab 身份。两个源读取及前后身份检查不一致时失败；源文件保存在外部数据目录。若仅一个窗口已连接可省略 `--window-id`。
2. `drawing-project-info --window-id '<窗口ID>'` 只读归档工程配置与 `.epro2` 导出（50MiB 上限，解析后工程日志/资源合计 100MiB 上限），包含用于 UUID 来源核验的 DEVICE/META。标准库在内存解析归档，不解压到磁盘。归档及写入后的工程证据包含完整工程，均保存在外部 data。`new-drawing-plan` 建立初稿。将 `target` 填为快照的窗口 ID、`identity.project_uuid`、`identity.document_uuid`、`domain`、快照**绝对路径**及文件 SHA256。每个操作必须包含原有六个文本字段，并新增如下结构化参数（值仅为示例，不可当作真实器件或坐标）：

```json
{
  "id": "op-1", "operation": "place_component", "target": "测试器件",
  "before": "目标位置没有该器件", "after": "指定器件位于指定坐标",
  "expected_result": "库身份、坐标、方向及 BOM/PCB 标记回读一致",
  "parameters": {
    "library_uuid": "已核验的库 UUID", "device_uuid": "已核验的器件 UUID",
    "expected_project_device_uuid": "经工程 DEVICE/META.source 证明的 UUID",
    "mapping_evidence": {"path": "工程导出证据的外部绝对路径", "sha256": "证据JSON文件的64位小写SHA256"},
    "x": 100, "y": 200, "unit": "10mil", "mirror": false,
    "rotation_convention": "easyeda-pro-api-0.2.53-negated-create",
    "requested_api_rotation": 90, "expected_canvas_rotation": 270,
    "expected_pins": [{"number": "1", "x": 100, "y": 180}, {"number": "2", "x": 100, "y": 220}],
    "add_into_bom": true, "add_into_pcb": true, "sub_part_name": null
  }
}
```

`sub_part_name` 为 null 表示预期 getter 返回空子部件；多数单部件器件也返回明确名称，应按库详情填写，不把 null 当通配符。坐标必须是有限数值（绝对值不超过 1000000），请求/预期角度限定 0、90、180、270 度，且 `expected_canvas_rotation=(-requested_api_rotation)%360`。`expected_pins` 必须列出完整引脚编号和已核验目标坐标，1–500 个、不重复；不猜测非对称器件的编号位置。旧 `rotation` 参数不再接受，旧版认可记录失效。

来源必须是工程 DEVICE 文档 UUID 的 `META.source=device_uuid|library_uuid` 精确对应；Symbol、Footprint UUID 不替代 Device UUID。解析按 ticket 较大、同 ticket 时 client 较小选择 META，拒绝同序冲突及被删除的文档或 META。证据必须与输入快照的窗口、工程、页、tab、规范化源相符；执行前后还读取实时工程再次核验来源，Python 对后置工程证据和全部回读字段独立复核。引脚精确比较编号和 API 坐标，不从坐标推断网络连接。

3. `prepare-drawing-execution '<项目.json>' '<计划.json>'` 离线核对记录、策略、快照及可执行参数，输出包含完整操作、请求、依据、目标身份、回读和失败处理的预览。agent 须向用户展示并说明局限，等待对当前具体方案的认可。
4. 获得真实认可后执行 `approve-drawing-plan '<项目.json>' '<计划.json>' --shown-plan-sha256 '<刚展示的 plan_sha256>' --user-message '<用户认可原话>' --reference '<对话消息引用>'`。此命令只记录认可，不连接 CAD。不得凭项目开发授权伪造认可；CLI 不能认证用户身份或判断自然语言意图，调用方须核实。认可绑定完整项目档案、策略、计划、源快照及适配器版本，变更后须重新展示并取得认可。
5. `execute-drawing-plan '<项目.json>' '<计划.json>' '<认可记录.json>' [--port 49620]` 先离线再次核验并领取方案，再锁定目标文档。每步先将意图持久化，再在指定窗口中复核项目、页、tab 和完整源，调用固定器件创建 API，重新读取图元并检查器件库身份、子部件、位置、方向及标记，保存返回及写入后的源，才执行下一步。退出码 0 表示所有步骤回读一致，3 表示中止或状态未知，2 表示输入及预检错误。

执行目录 `drawing-executions/<plan_sha256>/` 保存项目、计划、快照、认可记录和逐步意图、返回、回读及结果。方案只能领取一次；执行前失败也保留领取记录。独立的 `drawing-locks/` 以工程和文档身份协调同一数据目录内的执行；它不能锁住 EDA 人工编辑、其他工具或其他数据目录。写入前完整源比对会发现先前变化，但与创建调用之间并非原子操作；用户须在执行期间避免并发编辑或切换焦点。

超时、创建调用异常、回读失败、目标变化或进程崩溃时，实际写入可能已经发生。程序停止后续步骤、保留目标锁，不自动重试、删除、保存或回滚；查阅执行目录中最后的 intent/reply/result，检查真实画布与连接，并确认原执行请求已结束。经人工核对状态并决定后，才可手动移除对应 `target_lock` 目录（其中 `owner.json` 指向执行目录）；新建快照和修订方案，按需要重新取得认可。日志也可能因故障中断，缺少 result 不代表未写入。不得直接清理锁并盲目重跑。执行结果不代替 ERC/DRC 或独立电气审核。
