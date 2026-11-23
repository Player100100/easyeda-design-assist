# 非空白页增量编辑 v4

`schematic-incremental-v4` 已在嘉立创EDA **4.1.100** 的非空白页完成一次完整实机验证：保留原电路，在空闲区域新增三个电阻、三个网络标号和三组导线，9 步全部回读通过，六个引脚网表精确匹配。独立后查确认原有全部源记录、8 个网表器件及其属性/连接保持不变，原有 6 条详细 DRC 警告逐项保留，没有新增、消失、改变或未匹配项。

这是独立模块新增的验证范围。移动/删除已有器件、重接已有网络、合并旧导线、PCB 写入、多页协同和保存未由该契约实现；Source 属性适配器仍关闭。`engineering_verified=false`、`drc_global_freshness_established=false`：操作和观察通过，不证明物理电路功能或全部派生数据刷新。

## 使用与绑定

后续在独立[支路契约 v5](branch-editing.md)完成“新器件物理接入旧网络”3步实测和独立后查；v4 本文的原源不可变、旧网名禁用规则不变。v5 单独核验目标组的精确边变化，不提供移动或删除旧对象。

先只读采集完整基线，随后用既有方案预检、认可和执行入口：

```powershell
python -B src/design_assist.py capture-incremental-baseline --window-id '<窗口ID>'
python -B src/design_assist.py prepare-drawing-execution '<项目.json>' '<计划.json>'
python -B src/design_assist.py approve-drawing-plan '<项目.json>' '<计划.json>' --shown-plan-sha256 '<预览摘要>' --user-message '<真实用户原话>' --reference '<对话引用>'
python -B src/design_assist.py execute-drawing-plan '<项目.json>' '<计划.json>' '<认可记录.json>'
```

实际参数以 `--help` 为准。用户明确委托自行设计并执行测试时，记录该原话、实验范围和前瞻方案；这类明确委托无需再次询问同一实验的许可。普通绘制仍按[绘图策略](autodraw-policy.md)绑定具体认可方案。

计划要求 `schema_version=2`、`execution_contract="schematic-incremental-v4"`，并增加以下字段（路径和摘要须替换为真实值）：

```json
{
  "incremental_baseline": {"path": "<完整基线JSON绝对路径>", "sha256": "<文件字节SHA256>"},
  "edit_region": [100, 610, 1000, 770],
  "reviewed_lock_sha256": "<已独立复核的历史锁清单摘要>"
}
```

其余目标、输入快照、来源、操作与 `connectivity` 格式复用[电路参数契约](circuit-executor.md)。v4 不接受 `recovery_of/base_components/base_flags/base_wires`。库证据是 `drawing-device-archive` 的 JSON 文件及其原始字节摘要；普通器件须开启 BOM/PCB 导出，位号由编辑器生成。

基线包含完整源、完整网表、详细 DRC、全部画布器件清单及非图框对象的引脚/本体几何，所有读取须绑定同一窗口、工程、页、版本和源。当前支持 DOCHEAD/CANVAS/COMPONENT/ATTR/WIRE/LINE，无总线或未知图形；编辑区域须在 A4 范围内，避开原器件、引脚、导线及文字锚点的保守边界。此边界检查不等同字体渲染。

有图框的页面在采集基线时还会导出同源原生 SVG。预检根据实测的内框和标题栏保留区检查编辑区域。旧基线仍可查阅，有图框的页面执行新方案前需重新采集。适用条件和实机复测见[图框避让](branch-editing.md#图框避让复测)。

所有新增器件、引脚与导线须落在编辑区域，新网名不得与基线重复，每网一个完整导线操作。预检只在离线模型上复用 v3 的新模块几何算法；真实 CAD 从不清空原图。写前再查完整源和实时基线，每步严格保护已有记录；最终按源图元→Unique ID/位号/Device→完整网表映射全部新增引脚，并逐项比较原器件所有属性及连接。

## 延迟身份与执行锁

4.1.100 实测中，器件创建请求内多次读取仍可能看到 `R?`、空 Unique ID；请求返回后独立导出/读取才补齐。固定脚本先返回 `created_pending_identity` 和已创建图元 ID，bridge 随后最多 8 轮只读审计/快照/getter，要求三次完整稳定身份，再归档工程 DEVICE 来源并由 Python 独立核验。首次导出可能因身份补齐导致源变化而拒绝；保留该读取证据，再继续只读观察。该流程只有一次创建请求，不重试写入。

目标窗口/页/版本改变、身份不完整、来源不符或原记录改变均停止；创建收据、PID、读取记录与错误完整保留。已有 `uncertain` 方案不重放，旧 v2/v3 的版本限制不变。

v4 用独立 `incremental-active.json` 协调活动实验。历史锁必须先独立核对现场，并在计划中精确绑定整个清单摘要；每步写前和最终审计再次核对，成功仅释放自己持有的活动声明，原四份历史锁不覆盖。其他活动实验不能并发。锁仅协调同一数据目录内的本工具，不提供 CAD 事务。

`drc_passed` 在 v4 中表示**相对基线的详细 DRC 完全保持**，不是全页无警告。原警告新增、消失或改变都失败；无法匹配的项也失败。失败保留原结果和活动锁，不自动删除、回滚、换接口或保存。

## 实机实验与证据（2026-11-18）

用户明确委托“自行设计实验并继续推进，执行器需要能够支持在非空白页完成编辑”，并已指定当前 `Agent_test` 工程专用于测试。

| 样本 | 实际结果 |
|---|---|
| `68026c…`、`45ef48…` 首步器件 | 同请求内身份未补齐，分别保留 `uncertain`；独立复核孤立器件后清理，各自恢复完整原始基线 |
| `18f3ea…` 两电阻链 | 8 步与四个引脚网表通过，原电路完整保留；两端网络各只有一个器件引脚，新增两条 `SHOWDANDLING`，严格终审 `verification_failed` |
| `47d3b1…` 三电阻闭环 | 从恢复后的新基线重新设计、留档、执行；9 步通过，六个引脚精确，原电路及详细 6 条 DRC 保持；独立后查再次通过 |

失败样本清理由 `drawing_incremental_cleanup.py` 的独立调查入口处理，不接入正常调度。实测发现删除新对象会重排后续新记录的 ticket、新 NET/Relevance 属性会延迟补齐层级；原记录保护从未放宽。经典导线删除有一次返回 false 且完整现场未变；经独立记录改用 SourceWire.remove 后只移除了 WIRE 主记录，留下 LINE/ATTR，未视为清理成功。最终另行核验每条原记录不变、所有残留均属于已知实验后，单次 `setDocumentSource` 恢复归档基线，连续三次完整源/网表/DRC 一致才释放活动锁。探索删除实现及当时的测试归档于外部 `incremental-experiments/20261118/investigation/`，不保留在可调用代码中；仅保留实测通过的孤立首步器件清理与精确基线恢复。所有失败回包及清理过程保留；这些行为不构成通用删除能力或正式 Source 开放。

最终实验在 API 坐标 `[100,610,1000,770]`，单位 10mil，器件无镜像、0°、BOM/PCB 开启，型号 FRC0603F5101TS。R1 `(300,680)`、R2 `(550,680)`、R3 `(425,740)`，每个网络连接两个电阻引脚：

| 器件 | 引脚 1 | 引脚 2 |
|---|---|---|
| R1 | EDA_EXP_20261118_IN | EDA_EXP_20261118_MID |
| R2 | EDA_EXP_20261118_MID | EDA_EXP_20261118_RET |
| R3 | EDA_EXP_20261118_IN | EDA_EXP_20261118_RET |

该闭环用于检验 CAD 写入与互连，不是供电或负载功能验收。模块保留在当前页，未调用保存/重开。基线规范化源 SHA256 为 `88c8c44858e174d5b4b95ff98fdc8169680fe81aab610e3ec877a2b9c7ae32a4`；独立后查源为 `8f56280b08739acc3545175ff6a93d783ab0ff82e51280b81e39ee1b1121cf5e`。历史锁清单摘要仍为 `5268c61e2c7abcdf1b1ed36e3b38d9394bb59e078fc4892d57d4e651b8918167`，无本次活动锁残留。

本机证据：

- [前瞻方案与授权范围](../../easyeda-设计助手-data/incremental-experiments/20261118/closed-loop-confirmation/experiment-plan.json)、[独立后查结论](../../easyeda-设计助手-data/incremental-experiments/20261118/closed-loop-confirmation/completion-result.json)。
- [最终执行结果](../../easyeda-设计助手-data/drawing-executions/47d3b193d8060d726d506ff7375fd03107f778722c5d48468cfa6113a2db9206/result.json)、[最终逐项审计](../../easyeda-设计助手-data/drawing-executions/47d3b193d8060d726d506ff7375fd03107f778722c5d48468cfa6113a2db9206/final-audit.json)。
- [两电阻链的失败结论](../../easyeda-设计助手-data/drawing-executions/18f3eadd5e37b7783c1f62e279eeec6cbab83889a7fe8c78f1c004185adfe91a/result.json)、[完整基线恢复](../../easyeda-设计助手-data/drawing-executions/18f3eadd5e37b7783c1f62e279eeec6cbab83889a7fe8c78f1c004185adfe91a/explicit-baseline-source-restore/result.json)。
- [CAD 导出 SVG](../../easyeda-设计助手-data/incremental-experiments/20261118/closed-loop-confirmation/after-svg.svg) 已离线渲染检查，原电路与测试模块分离；导出未保存 CAD 工程。

上述证据位于源码仓库之外，不随克隆分发。其余器件、角度、图形、接入原网络及物理功能仍需各自验证。

最终完整回归 **251 项通过，无跳过**，`compileall`、差异空白检查、能力目录及 CLI 帮助冒烟通过，项目 Markdown 本地链接均存在。测试覆盖原源/完整旧网表保护、详细 DRC 增删改和未匹配、区域/网名冲突、锁并发与历史保留、跨请求身份补齐且不重建、单次执行与失败留锁、受限基线恢复及原记录/网表变化时拒绝。
