# 属性修改修复与实机往返实验

2026-11-17（北京时间）。在用户明确授权的当前实验工程 `Agent_test/Schematic1/P1`、编辑器 `4.1.100` 完成历史层级恢复，以及 U2 Name、C3 Value 的实机修改和恢复。最终**规范化完整源摘要、所有源记录、完整网表字节及详细 DRC 均与最初实验前一致**，没有残留源差异。原六条 DRC 警告保持原样。

用户原话“你能试图修复问题并完成设计实验吗”与此前当前工程可随意用于测试的授权共同记录于[本轮修复计划](../../easyeda-设计助手-data/source-validations/repair-20261117/repair-plan.json)。本轮没有保存、重开文档或重放历史写入；每次写入独立留档。下面是局部兼容实验，不代表电路或整板工程验收。

## 修复方法与实测边界

前次 `sch_SourcePrimitiveComponent.update` 对 U2 Name 只传业务属性，却使其 ATTR `zIndex` 从 16 变为 15。文档格式明确 `zIndex` 是绘制层级；没有将它当作噪声忽略。实时帮助确认 `update` 和 `updateAttribute` 都未列出此参数，经典属性接口也没有层级 setter。本轮只能确认该副作用与使用的更新路径有关，没有定位或修改编辑器内部实现。

助手增加显式的 `canvas-attribute` 实验后端，使用已核对签名的 `sch_PrimitiveAttribute.get/modify`，直接对唯一已有 ATTR 提交 `{value: 新值}`，避开 Source 器件更新。写入前核对 ATTR ID、父器件、键和原值，再次核对窗口、工程/页/tab、名称映射、精确版本与完整源。写后仍用 Source 详情和完整源连续三次稳定回读，Python 严格限制只有目标属性的 `value` 改变；没有放宽层级、坐标或其他源差异检查。后端事先选择，不在失败后自动换接口重试。

`execute-source-trial` 的 CLI 默认后端改为 `canvas-attribute`，以避免新实验默认使用已知有副作用的路径；`--backend source` 保留用于明确选择的 Source 调查。Python 兼容入口 `execute_trial` 的默认值仍为 `source`，调用者应显式指定实验后端。正式 `schematic-source-properties-v1` 的固定实现和关闭开关不变，实验后端不注册为普通执行适配器。

```powershell
python -B src/design_assist.py execute-source-trial '<当前目标的case.json>' --shown-sha256 '<已展示摘要>' --user-message '<真实测试授权>' --reference '<对话引用>' --backend canvas-attribute --port <端口>
```

有历史锁时仍需先检查完整记录及现场，再指定精确的 `--reviewed-lock-sha256`。实验只提交一次写入，未知结果保留现场与锁；详情见[单次实验与复核机制](source-validation.md)。后端名称进入授权、claim、intent 和 result，固定脚本摘要也独立归档。

## 历史层级残留恢复

新内部模块 `source_repair.py` 提供一次性的 `prepare_z_index_restore` / `execute_z_index_restore`，使用核实的 `SYS_FileManager.setDocumentSource(source: string): Promise<boolean>`。它没有 CLI 任意源码入口，也不接收调用者给定的目标源码：必须从原始与当前的两份完整 Source/audit 归档重算。

准备条件为同一窗口、完整身份、名称目标、器件与版本；器件详情、完整网表和详细 DRC 相同；全部源记录中仅一条已有 Name ATTR 的整数 `zIndex` 不同，其余字段及 envelope 完全相同。目标源码由当前源只替换该字段生成，计算后须与完整原始源记录一致。执行前重新获取完整基线、核对历史锁，独立记录授权、单次 intent/reply，写后核对连续三次源回读及原始导出。任何额外差异不提交，已尝试写入后异常不重试。

本次只把 `58d64cb3e67eab12` 的 `zIndex:15→16` 恢复。结果为 `restored_observed`，源、网表和详细 DRC 对账通过；其独立 active claim 已释放。这个内部入口用于已查明的历史残留修复，不是常规属性修改方式。

- [修复 case 与原始证据](../../easyeda-设计助手-data/source-repairs/z-index-20261117T144308Z-9417b8e2acea/case.json)
- [单次修复结果](../../easyeda-设计助手-data/source-repairs/z-index-20261117T144308Z-9417b8e2acea/result.json)
- [实时接口帮助](../../easyeda-设计助手-data/source-validations/repair-20261117/help-discovery.json)、[源码设置签名](../../easyeda-设计助手-data/source-validations/repair-20261117/set-source-help.json)

## 三次往返的观察

| 实验 | 计划与实际响应 | 严格源 / 恢复 | 结论 |
|---|---|---|---|
| U2 Name | `={Manufacturer Part}` → `EDA_CANVAS_NAME_PROBE_20261117` → 原值；预先声明 U2 `ATTRMATCHCODE` 新增/消失，实际准确匹配 | 正向只变 Name 值，`zIndex=16` 保持；最终完整恢复 | 已声明的局部实验预期满足 |
| C3 Value 探索 | `470uF` → `471uF` → `470uF`；计划无新增 DRC，实际新增一条 C3 `ATTRMATCHCODE`，恢复后消失 | 正向只变 Value，`zIndex=27` 保持；最终完整恢复 | 原 DRC 预期不满足，保留该发现 |
| C3 Value 确认 | 从新采集的完整基线生成新 case，预先声明同一条 C3 `ATTRMATCHCODE`；实际准确新增/消失 | 正向只变 Value，层级保持；最终完整恢复 | 事先声明的确认实验通过，不重放探索请求 |

以上每次正向网表仅改变目标属性，全部引脚连接不变，原有六条详细 DRC 警告保持。正向产生的是本次属性刺激对应的第七条警告，恢复后准确消失。C3 的探索结果没有被事后改写为“预期通过”；确认使用了独立计划、基线、case、intent 和恢复记录。

普通 `check-source-validation` 要求没有新增 DRC，因此三个正向执行仍保留原 `observation_failed`。现场逐项核对后，分别追加 `reviewed-known-state` resolution，只释放各自实验 active claim，使已授权的独立恢复得以进行。三个恢复执行均为 `property_observed`。这些原记录不因往返报告而变更。

本轮共七次单次写入：一次历史层级恢复，加三次实验各自的正向和恢复。无写入重试。四份历史锁的文件名与原始字节清单仍为 `5268c61e2c7abcdf1b1ed36e3b38d9394bb59e078fc4892d57d4e651b8918167`，没有活动实验 claim；原 Source `uncertain` 及全部历史失败/修复记录保留。

## 离线往返核对

新增 `check-source-roundtrip` / `check_roundtrip`，从单属性 case、修改中及恢复后的 Source/audit 分别验证：

- 属性刺激的严格源差异、完整网表仅目标属性变化及所有引脚连接不变。
- DRC 新增条目与实验声明的详细条目完全匹配，无其他新增、删除、变化或未匹配项；不能只按条数核对。
- 恢复后的源记录、规范化完整源摘要、器件详情、完整网表字节和详细 DRC 都回到本次基线。

```powershell
python -B src/design_assist.py check-source-roundtrip '<case.json>' '<实验中Source.json>' '<实验中audit.json>' '<恢复Source.json>' '<恢复audit.json>' --expected-drc-added '<实验前声明的详细新增DRC数组.json>' --save
```

不指定 `--expected-drc-added` 时，默认预期无新增。`--save` 保存原始 case 及六份查询（共七份输入）和字节摘要到 `source-validations/roundtrip-*/report.json`。报告的 `experiment_expectations_met` 仅表示给定证据符合声明的实验预期；离线工具不能验证声明的真实性或实际写入。CLI 退出码 0 表示核对完成，须读取该布尔字段。不会修改普通执行结果、关闭历史锁或启用正式调度。

`local_rule_response_observed` 仅在存在明确预期的规则刺激且严格往返均满足时为真，支持本次属性及此规则的响应。`drc_global_freshness_established`、`verification_passed`、`engineering_verified` 和 `general_source_dispatch_enabled` 均为 `false`。没有把单一规则的出现/消失扩大为所有网表/DRC 与 Source 版本同步的证明；本次局部实验已经完成，全局刷新关联仍是后续独立研究范围。

主要证据：

- [最终结论、所有计划和 case 引用](../../easyeda-设计助手-data/source-validations/repair-20261117/completion-result.json)
- [Name 往返报告](../../easyeda-设计助手-data/source-validations/roundtrip-20261117T145355Z-d2378d044a2c/report.json)
- [C3 探索报告（预期未满足）](../../easyeda-设计助手-data/source-validations/roundtrip-20261117T145355Z-9a9910701db9/report.json)
- [C3 预先声明的确认报告](../../easyeda-设计助手-data/source-validations/roundtrip-20261117T145355Z-07a300ddd6ea/report.json)
- [最终 Source](../../easyeda-设计助手-data/source-validations/repair-20261117/final-source.json)、[最终审计](../../easyeda-设计助手-data/source-validations/repair-20261117/final-audit.json)、[与最初基线比较](../../easyeda-设计助手-data/source-validations/repair-20261117/final-comparison.json)

当前实测兼容范围仅为编辑器 `4.1.100` 的这两个已有属性和目标，不含空值恢复、批量、多页、其他属性、几何/网络操作或 PCB；不据此开放全部 Source 能力。

## 代码验证

完整回归 **230 项通过，无跳过**；`compileall`、差异空白检查、两个新 CLI 的帮助与能力目录冒烟通过，本地文档链接均存在。新增测试覆盖单字段层级修复的限制与固定脚本、历史锁字节不变、丢失响应不重试、不同返回窗口不释放未知现场、领取后历史锁变化阻断写入、经典属性身份绑定/后端留档、严格源差异、预期/未预期 DRC 响应、精确恢复与源残留反例，以及离线往返 CLI。运行产物与实测证据在仓库外，代码未提交或推送。
