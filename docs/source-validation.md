# Source 单属性验收准备与证据核对

2026-11-17。`prepare-source-validation`、`check-source-validation` 两个**离线、只读**入口用于准备单属性测试并核对修改后的证据，不创建绘图认可或执行 CAD 写入。后续已增加独立的单次实机实验入口；经用户明确授权，在当前实验工程完成 Name 写入及恢复，发现 `zIndex` 副作用，见本页后半部分。正式 Source 适配器仍为 `dispatch_enabled=false`。

同日再后续已完成[层级残留修复及经典属性接口实验](source-experiment.md)：`zIndex` 恢复原值，U2 Name、C3 Value 的往返全过程保留层级，最终完整源/网表/详细 DRC 恢复最初基线。新增 `--backend canvas-attribute`（实验 CLI 默认）和独立 `check-source-roundtrip` 核对；下面保留此前 Source 路径的失败及恢复记录，原结果不改写。

## 为什么单独观察属性

本机 4.1.100 实时帮助中 `SCH_SourcePrimitiveComponent.update` 明确说明：返回 `ok` 表示写入 Source，网表/DRC 等异步刷新，通常需等待约 1 秒。这个时间是接口使用提示，不是与 Source 版本绑定的完成凭据。依据为已归档的 [Source 方法帮助](../../easyeda-设计助手-data/api-schematic-tools-20261115/source-details.json)。

现有本机 `SCH_Document`、`SCH_Netlist` 文档和已归档方法目录未提供本实现可直接采用的 Source 版本/DRC 刷新完成凭据；本轮没有猜测或调用未核对签名的刷新方法，也没有用保存、重新打开或写回网表来代替只读验证。

单属性试验可以推进一个更小的验证目标：基线网表中该属性是旧值，修改后导出出现新值，且 Source 严格差分和完整网表只体现这一属性变化。这支持**该属性已被导出观察到**；不能据此证明所有网络或 DRC 已刷新，也不能证明实际调用了 Source 写入 API。

## 准备测试候选

在同一目标和源状态下依次采集：

```powershell
python -B src/design_assist.py query --kind source-component --primitive-id '<画布器件ID>' --save --port <端口> --window-id '<窗口ID>'
python -B src/design_assist.py query --kind audit --save --port <端口> --window-id '<窗口ID>'
python -B src/design_assist.py prepare-source-validation '<Source查询.json>' '<审计查询.json>' --property Name --value '明确的新值'
```

准备命令校验 Source 查询外壳与详情、客户端 `4.1.100`、窗口/完整身份、名称目标及规范化源一致。只能选择已有白名单中的一个属性，且新值不同于旧值；该属性旧值必须同时出现在 Source 详情、源 ATTR 及基线网表。

画布图元 ID 不作为网表组件键。工具要求 Source 源中的 `Unique ID` 只有一个器件所有者，网表中同一 `Unique ID` 只有一个组件，且两侧 `Designator`、`Device` 一致；缺失、重复或冲突时拒绝准备，不按位号猜测关联。

输出 `validation_path` 指向外部数据目录 `source-validations/case-*/case.json`。同时原样归档 `source-before.json`、`audit-before.json` 并记录字节摘要。`case` 包含目标、原值、新值、基线摘要和参数，固定声明 `cad_write_authorized=false`、`requires_isolated_target_confirmation=true`。加载时核对字节摘要并从原始基线重算 case，不能通过手改结论获得认可。

这是一份验证候选，不是可执行绘图计划。隔离测试目标确定后须在该目标重新采集并准备，不能把原页的 UUID、图元或基线直接移用到副本。具体写入范围的实际认可仍按[项目绘图策略](autodraw-policy.md)处理；当前写适配器不会因生成 case 而开放。

## 检查修改后的证据

在具体方案获认可并由相应测试流程完成修改后，重新采集同目标的 Source 与审计查询，再离线检查：

```powershell
python -B src/design_assist.py check-source-validation '<case.json>' '<修改后Source查询.json>' '<修改后审计查询.json>' --save
```

工具检查前后窗口、身份、名称、图元和网表组件键一致，并分别报告：

- `source_property_verified`：器件详情只改变指定属性，源记录仅允许目标 ATTR 的值变化。
- `netlist_property_observed`：目标网表组件已经出现指定新值。
- `netlist_unchanged_except_property`：完整解析网表只存在指定属性变化，未知字段也参与比较。
- `property_observation_passed`：上述检查均满足，且连接/DRC 比较没有新增、变化或未匹配项；允许原 DRC 违规消失，但不因此关闭刷新核验。

候选属性没有出现、源仍是旧值、额外属性/几何/连接变化或 DRC 歧义时，结果为 `needs-investigation`。属性观察满足时为 `property-observed-drc-freshness-unknown`。`freshness_established`、`drc_freshness_established`、`verification_passed`、`engineering_verified`、`cad_write_authorized` 均保持 `false`。

`--save` 把 case、前后四份查询的原始字节及摘要保存到 `source-validations/report-*/`。报告是观察结果，不会被执行器或反馈工具作为刷新证明消费。CLI 退出码 0 表示命令运行完成；应读取状态字段，不能把退出码当成验收通过。无效身份、损坏归档等输入错误返回 2。

## 真实只读准备与负例验证（实机写入前的历史记录）

2026-11-17 当前工程为 `Agent_test`，目标 `Schematic1/P1`；本轮只读查询定位到 U2，图元 ID `620171ac96d22bc2`，网表组件键 `gge4`。`Unique ID`、位号和 Device 在 Source 与网表中一致。候选改动为：

| 属性 | 当前值 | 候选试验值 |
|---|---|---|
| `Name` | `={Manufacturer Part}` | `EDA_SOURCE_NAME_PROBE_20261117` |

这个字段在当前 Source 和网表中均有原始值，适合观察字段是否导出；修改将影响器件名称呈现及可能的 BOM 名称，**不是器件替换或电气参数变更**。当前工程名含 test 并不构成隔离目标确认，本轮未修改此页。

- [本轮上下文](../../easyeda-设计助手-data/contexts/context-20261117T123106Z-1f176646c901.json)
- [重新采集的 Source 详情](../../easyeda-设计助手-data/queries/query-20261117T123511Z-a1e3f9b0d46c.json)
- [相同源状态的审计](../../easyeda-设计助手-data/queries/query-20261117T123511Z-ccc50b224175.json)
- [具体候选及基线归档](../../easyeda-设计助手-data/source-validations/case-20261117T123511Z-0cdb24b1efb1/case.json)
- [以未修改数据运行的负例报告](../../easyeda-设计助手-data/source-validations/report-20261117T123608Z-8b568e66bf48/report.json)

负例没有进行写入：直接把基线作为修改后证据传给核对命令，结果正确保持 `needs-investigation`，源属性、网表属性与综合属性观察检查均未通过；没有把稳定旧值当成成功。

完整回归 **199 项通过，无跳过**，其中新增 `test_source_validation.py` 共 12 项；覆盖关闭状态、严格映射、基线归档重算、缺失/旧值、额外几何/网络/DRC 变化、目标改变、错误结构及离线 CLI。`compileall` 通过。模拟成功只支持核对逻辑，不是实机写入兼容证明。

## 实机前拟定的方案边界

1. 确认可修改的隔离测试页及其器件；在该页重新采集和生成 case，避免误用当前页身份。
2. 具体方案写明一个属性的前后值、是否包含恢复原值、目标与全部预期差异。未明确覆盖恢复时，不自行追加恢复写入。
3. 保留执行前证据、单次请求 intent/reply、修改后 Source 与审计；请求超时或回读未知时保留现场与锁，不自动重试或回滚。
4. 运行属性观察核对。若通过，仅记录属性/源/网表的局部兼容性；继续保留 DRC 刷新未知，不能直接开放写入。
5. 独立解决 DRC 刷新证据，再把完整验收接入执行器。人工 UI 修改也可供本离线工具检查，但不能据此宣称 `SourcePrimitiveComponent.update` 已通过实测。

## 独立单次实验路径

`source_trial.py` / `execute-source-trial` 单次修改 case 中一个已有白名单属性。默认 `canvas-attribute` 后端使用固定属性 `get/modify`；`--backend source` 使用原固定 Source `get/update` 路径，已知有层级副作用。两者都不改变正式能力开关，不接受 JavaScript，不把 case 中的 `cad_write_authorized=false` 改为认可；实验授权和后端单独留档。新后端实测与边界见[后续修复记录](source-experiment.md)。

```powershell
python -B src/design_assist.py execute-source-trial '<本目标重新准备的case.json>' --shown-sha256 '<已展示的case_sha256>' --user-message '<真实测试授权原话>' --reference '<对话引用>' --backend canvas-attribute --port <端口>
```

执行前须核对用户确实授权该目标、属性前后值和实验范围；恢复原值也须被授权范围覆盖。命令保存这些记录，但不能验证对话的真实性。固定校验仍绑定窗口、当前页完整身份、三段 Source 名称、`4.1.100`、源、完整网表、详细 DRC 及单属性 case 摘要；实时基线漂移、未处理的 Bridge 请求或同一 case 已领取均拒绝写入。case 归档原始字节与基线在读取时重算。

运行目录为外部数据目录 `source-trials/<case_sha256>/`，单独归档 `authorization.json`、原始 case/基线、实时基线、历史锁清单、`claim.json`、`intent.json`、`reply.json`、回读与 `result.json`。只提交一次写入，之后最多重复六次只读观察，间隔一秒；传输失败、严格源不符或观察失败不自动重试、不自动回滚、不保存或重开文档。

| 状态 | 含义及锁处理 |
|---|---|
| `blocked` | 固定脚本确认未尝试写入；保留本次日志，释放本次 active claim |
| `uncertain` | 已尝试写入，但返回、目标或严格源检查未通过；保留现场和本次 active claim |
| `observation_failed` | 固定写入严格回读通过，但导出/DRC 观察未满足目标；保留现场和本次 active claim |
| `property_observed` | 相对此次基线的严格源、目标属性及差分观察通过；释放本次 active claim，DRC 全局刷新和工程验收仍未知 |

命令退出码 0 仅对应 `property_observed`；上述其他实验结果返回 3，输入错误返回 2。不会自动更改用户决定、正式写入认可或历史执行结果。

### 历史锁与现场复核

实验使用独立的 `source-trial-active.json`。有历史锁时，先核对记录、原持有人/窗口、待处理请求及当前完整现场，再以 `--reviewed-lock-sha256 '<当前完整文件清单摘要>'` 绑定所审阅的历史文件。此参数不能关闭正式执行锁；历史 `owner.json`、修复记录及未知结果不修改、不删除。同一目标已有未关闭的实验 active claim 时，不能启动新实验。

遇到未知结果，先只读归档全部源差异、完整器件详情、网表与详细 DRC。`review_trial_state` 生成绑定完整状态的复核摘要；调查者明确说明全部预期和非预期差异后，`close_reviewed_trial` 再实时读取并核对该摘要，只有现场完全匹配且无待处理请求才写独立的 `resolution.json`，随后释放本次实验 active claim。每次复核读取保存到独立 `reviews/review-*/`；失败后重查不覆盖先前证据。摘要排除每次采集变化的审计时间/来源摘要，仍绑定规范化源、完整网表字节和详细 DRC 内容。

该复核只有内部 Python 入口，没有 CLI 强制解锁选项。原 `uncertain` / `observation_failed` 结果保持原样；resolution 为 `reviewed-known-state`、`verification_passed=false`，只说明当前状态已完整查明，不把严格失败改成成功。后续恢复需从复核后的最新现场生成另一个 case、独立 intent 和结果。

## 2026-11-17 原 Source 路径授权实验与恢复结果（历史）

用户明确说明“目前我打开的工程是专门用于该项目实验的，可以随意用于测试，继续推进进度还有阻碍吗”。据此在 `Agent_test/Schematic1/P1`、编辑器 `4.1.100` 的 U2 上执行一次 Name 写入、复核和一次恢复。图元与网表身份沿用本页所列 U2 / `gge4`，没有保存、重开或重放历史请求。

| 阶段 | 实际观察 | 判定 |
|---|---|---|
| 正向写入 | Name 从 `={Manufacturer Part}` 变为 `EDA_SOURCE_NAME_PROBE_20261117`；接口回读连续三次稳定 | API 确实写入，但不单靠返回值验收 |
| 严格源核对 | 只有 Name ATTR `58d64cb3e67eab12` 改变，除目标值外 `zIndex` 从 16 变为 15；envelope、坐标、旋转、可见性等其余字段不变 | `uncertain`，正向严格验收失败；未放宽校验器 |
| 派生观察 | 完整网表只改变 U2 Name，引脚连接不变；详细 DRC 从 6 条变为 7 条，新增 U2 `ATTRMATCHCODE` | 支持目标属性及该规则对本次变化有响应，不证明全部规则已刷新 |
| 现场复核 | 实时核对完整差异与调查记录一致，独立留存 resolution | 关闭本次实验 active claim，保留原失败结果和四份历史锁 |
| Name 恢复 | 从复核后的基线把 Name 改回 `={Manufacturer Part}`，仅有 Name 值差异 | 恢复实验为 `property_observed`，仍非全局验收 |
| 最终对账 | Source 器件详情、完整网表字节与原基线一致，引脚连接不变；详细 DRC 恢复原 6 条，新增 `ATTRMATCHCODE` 消失 | Name 与派生观察已恢复；源仍残留 `zIndex=15`，不是完整源恢复 |

正向、恢复各只提交一次写入。四份历史锁的文件名和字节清单摘要仍为 `5268c61e2c7abcdf1b1ed36e3b38d9394bb59e078fc4892d57d4e651b8918167`；本次 active claim 已释放，历史正式执行锁仍保留。复查已归档的 `ISCH_SourcePrimitiveComponentUpdateOptions` 与 `ISCH_SourcePrimitiveComponentUpdateAttributeOptions`，两者都未列出 `zIndex` 参数；这仅说明这两个已核对接口没有相应参数，不证明其他接口也没有。没有猜测签名、调用新的属性呈现 API 或直接写回完整源。

主要证据（位于源码仓库外）：

- [实验范围、授权原话和历史锁清单](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/experiment-plan.json)
- [正向单次结果（保留 uncertain）](../../easyeda-设计助手-data/source-trials/480c62aa7e2f655648f9eddfcee357c8a51c52a026f312035a4c9ec0a98f1c08/result.json)、[全部源差异](../../easyeda-设计助手-data/source-trials/480c62aa7e2f655648f9eddfcee357c8a51c52a026f312035a4c9ec0a98f1c08/investigation-source-delta.json)、[独立现场复核](../../easyeda-设计助手-data/source-trials/480c62aa7e2f655648f9eddfcee357c8a51c52a026f312035a4c9ec0a98f1c08/resolution.json)
- [恢复计划及具体 case](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/restore-plan.json)、[恢复单次结果](../../easyeda-设计助手-data/source-trials/d5775c08198436598ec24928283a123b81905f80ec4cd499cde59723c97afde5/result.json)
- [最终完整对账与残留差异](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/roundtrip-result.json)、[最终比较](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/roundtrip-comparison.json)、[最终 Source](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/final-source.json)、[最终审计](../../easyeda-设计助手-data/source-validations/live-20261117-u2-name/final-audit.json)

本阶段结束时，能力目录为 `live-name-roundtrip-strict-failed`，正式 `dispatch_enabled=false`。当时待办是界定 `zIndex` 副作用、为相应范围建立新实验契约，以及取得与 Source 版本关联的派生刷新证据；不能仅凭单一 Name 样本放开其他属性、版本或通用编辑。后续残留修复与经典属性实验已完成，见[最新结果](source-experiment.md)，Source 自身的关闭边界保持。用户对当前实验工程的测试授权已记录，无需把先前“尚未确认隔离工程”继续当作阻碍。

本轮完整回归 **212 项通过，无跳过**；`compileall` 通过。新增 `test_source_trial.py` 共 13 项，覆盖固定单次提交、授权/摘要与实时基线、未知响应不重试、历史锁字节保留、旧导出、现场漂移、采集时间变化、失败复核证据不覆盖、实测 `zIndex` 副作用、恢复相对新基线通过仍保留原始残留差异，以及 CLI 失败退出码。模拟测试仅验证代码边界，实机结论以以上原始证据为准；本页前述 199 项是离线准备迭代的历史数量。
