# 网表、DRC 差分与执行反馈

2026-11-17。已提供独立的只读审计、离线比较与反馈入口。它们帮助发现变化并列出后续核验，不自动将 CAD 回读结果或工程任务标为完成。Source 属性正式调度仍关闭，派生网表/DRC 与 Source 版本的刷新关联尚未建立。

最新[层级修复与经典属性往返实验](source-experiment.md)已完成，最终完整源、网表字节和详细 DRC 均恢复最初基线。U2 Name 与 C3 Value 的局部规则响应/恢复已逐项验证，独立 `check-source-roundtrip` 可归档复核；这不改变本工具的全局刷新和工程验收边界。

## 采集审计证据

在项目目录运行；多个窗口时须明确指定窗口：

```powershell
python -B src/design_assist.py status
python -B src/design_assist.py query --kind audit --save --port <Bridge端口> --window-id '<目标窗口ID>'
```

`--save` 输出 `query_path`，文件位于外部数据目录 `queries/`。结果为统一的 `design-query` 外壳，`data` 是 `schema_version=1`、`kind=design-audit` 的记录，包含：

- 工程、焦点页、tab 和窗口身份，采集时间及客户端版本。
- 原始完整源和规范化源 SHA256；规范化只忽略已有契约列出的 DOCHEAD `client`、`updateTime`、`version`，不忽略未知字段。
- 一次 `sch_ManufactureData.getNetlistFile('design-assist','JLCEDA')` 的网表原文及其 UTF-8 字节 SHA256。
- `sch_Drc.check(true,false,true)` 返回的原始 DRC 数组，固定设置、读取范围和固定脚本 SHA256。

固定脚本核对采集前后目标、规范化源和客户端版本一致；Python 再检查目标、窗口、源、网表及记录结构。网表上限 20 MiB，DRC 上限 5000 项。各接口顺序读取，网表范围以本次 API 导出为准，DRC 范围以当前原理图 API 结果为准；不宣称覆盖全工程或所有板。

**源稳定不证明派生结果已经刷新。** 当前 `freshness.derived` 固定为 `not-established`，统一结果的 `missing_fields` 包含 `derived_freshness`。修改源后读到两次相同的旧网表或旧 DRC，仍不能作为写后验收证据。

## 离线比较与归档

在已认可的实际修改前、后分别采集审计；比较命令本身不连接 CAD，也可用于两次只读采集：

```powershell
python -B src/design_assist.py compare-design-audits '<修改前查询.json>' '<修改后查询.json>'
python -B src/design_assist.py compare-design-audits '<修改前查询.json>' '<修改后查询.json>' --save
```

输入接受统一查询或内层 `design-audit`，并检查外壳的查询名、目标、窗口和版本与内层一致。输入文件须位于源码目录之外。不同工程/焦点页拒绝比较；客户端版本不同则返回 `comparable=false`，要求重新建立同版本基线。缺少版本或不符合当前范围/设置契约时，须重新采集。

网表按**组件键与引脚编号**匹配，报告组件增删、位号变化、引脚增删及引脚网络变化。同位号的替换器件仍作为不同组件处理；引脚编号为空或在同组件内重复时拒绝可靠比较。网表组件键不承诺等同画布图元 ID，不跨板拼接同名网；网络变化也可能只是重命名，须结合工程意图核查。器件值、额定值及其他属性的工程等效性不属于连接差分的结论。

DRC 按**规则与图元所属 sheet、primitiveId 集合**定位，再比较完整记录；图元数组顺序不影响匹配，严重级别、网络及未知字段的变化均保留为 `changed`。报告包含 `added`、`removed`、`changed`、`unchanged` 和两侧 `unmatched`。同一身份存在多个剩余候选时不猜测配对；完全相同的重复项按实际数量匹配。缺少规则、图元或 sheet 的旧汇总记录保留为未匹配，数量相同不会判为通过。只有布尔值或总数、缺少当前审计结构的旧文件须重采，不会自动升级为详细基线。

| 结果字段 | 当前含义 |
|---|---|
| `no_observed_regressions` | 同版本、连接差分为空，且 DRC 无新增、变化或未匹配；允许旧违规消失。不是零违规，也不判断变化是否符合具体方案 |
| `source_changed` | 两侧规范化源摘要是否不同 |
| `freshness_established` | 当前固定为 `false`，没有派生数据与 Source 版本关联证据 |
| `verification_passed` | 当前固定为 `false`，独立差分不能关闭写后核验 |
| `engineering_verified` | 当前固定为 `false`，不替代计算、工况或实板验证 |

`--save` 生成 `audit-comparisons/<comparison-id>/report.json`，并把两个输入的**原始字节**分别复制为 `before.json`、`after.json`，记录原路径与字节 SHA256。反馈加载归档时重新校验输入摘要并重算结论；输入损坏或手改报告结论会被拒绝。退出码 0 仅表示命令成功，不表示工程验收通过。

## 从执行生成独立反馈

```powershell
python -B src/design_assist.py drawing-feedback '<项目.json>' '<计划.json>'
python -B src/design_assist.py drawing-feedback '<项目.json>' '<计划.json>' --comparison '<已归档的report.json>'
python -B src/design_assist.py drawing-trace '<项目.json>' '<计划.json>'
```

`drawing-feedback` 离线读取本计划的来源、领取记录和执行结果，每次生成一个新文件：`drawing-feedback/<plan-sha256>/feedback-*.json`。`drawing-trace` 的 `feedback_paths` 返回这些文件的位置。反馈记录执行结果内容摘要、来源失效项、后续动作、原建议的验证清单和当前项目显式依赖涉及的复核对象及其 `reviewed_against` 摘要。

| 执行状态 | 后续处理 |
|---|---|
| 未领取 | 完善具体方案与前置证据；反馈不构成认可 |
| 已领取但没有结果 | 先查现场与日志，不重放执行 |
| `blocked` | 核对目标及前提，保留领取记录，另建后续方案 |
| `partial` / `uncertain` / `verification_failed` | 查已完成项、画布和原请求状态，保留锁，不自动重试或修补 |
| `verified` | 补齐绑定的网表/DRC 差分、刷新证据及原建议的工程验证；差异逐项核查 |

当前 `--comparison` 仅接受 `verified` 且全部步骤完成的执行。它检查比较目标及前后源摘要分别与执行的 `input-snapshot.json`、最后一步 snapshot 一致；不属于本次执行的比较被拒绝。部分执行或未知状态仍可生成不附比较的反馈，用于先处理现场。

有来源失效、执行未通过、缺少比较、观察差异或刷新证据未知时，反馈保持 `needs_review=true`，受影响对象保留 `review_required=true`。即使观察没有回归，也不会因未知刷新证据而关闭复核。受影响对象仅沿**当前项目已登记的显式引用**传播；未绑定来源的计划可能没有复核对象，不能由此推定没有工程影响。

已归档的 `origin-task-evidence.json` 用于还原所选原建议的 `validation`，解码后核对原始字节摘要；原任务文件丢失或被修改时仍可使用冻结清单，并报告来源失效。没有关联任务或相应归档时，`original_validation_items` 可能为空，不表示无需验证。反馈不覆盖原任务、用户决定、认可、执行结果或项目复核/完成状态，也不释放执行锁。

## 本轮验证与剩余边界

2026-11-17 全部 **187 项**单元及固定脚本测试通过，无跳过；其中 `test_design_audit.py` 的 22 项覆盖目标/源变化、过期派生模型、DRC 身份与歧义、重复引脚、归档损坏/结论改动、离线 CLI、冻结验证清单、显式影响传播和执行绑定。`python -B -m compileall -q src` 通过。

同日客户端 `4.1.100` 的两次真实只读审计均得到 **23452 字节**网表及 **6 条 warn**：1 条 `ONENETNAME`、5 条 `NETOBJECTONGRID`。两次规范化源和连接一致，6 条警告均匹配为未变化，没有新增、变化或未匹配项；`freshness_established` 与 `verification_passed` 均为 `false`。归档已通过摘要校验和重算读取。本轮没有调用 CAD 写入。

外部证据（不随源码仓库发布）：

- [第一次只读审计](../../easyeda-设计助手-data/queries/query-20261117T120706Z-ad2a3f76c926.json)
- [第二次只读审计](../../easyeda-设计助手-data/queries/query-20261117T121045Z-bbb6be90f430.json)
- [原始输入与比较归档](../../easyeda-设计助手-data/audit-comparisons/comparison-20261117T121112Z-d912ba325738/report.json)

另在同一客户端完成[当前页器件清单](../../easyeda-设计助手-data/queries/query-20261117T122106Z-a5a38cd6170b.json)及 [U2 Source 只读查询](../../easyeda-设计助手-data/queries/query-20261117T122301Z-58ea5a402b54.json)：清单返回 17 个对象、未截断，Source 器件详情和引脚几何均返回成功，`missing_fields` 为空，名称目标为 `Schematic1/P1`。这补充了后续属性测试所需的读取依据；尚未建立隔离副本或执行写入，也不提供派生刷新证明。

这次实测证明当前客户端能返回并比较详细格式，未验证真实修改后的刷新。下一步仍需按 [Source 属性契约](source-properties.md)准备具体隔离页方案，验证单属性写入及派生数据刷新，再决定是否开放对应写入能力。独立差分尚未作为 Source 执行器的自动验收步骤；PCB 写入、通用电路编辑和跨板原生装配也未由本功能获得支持。

同日后续已增加[单属性验收候选与导出观察](source-validation.md)，完整回归增至 199 项；这是离线证据核对能力，不改变上述派生刷新未知和写入关闭边界。本页 187 项为差分/反馈迭代当时的验证数量。

再后续已在用户明确授权的当前实验工程完成 U2 Name 写入/恢复，完整回归 212 项。详细 DRC 新增 U2 `ATTRMATCHCODE` 并在恢复后消失，完整网表恢复原始字节摘要；这支持该属性和该规则的局部响应，不证明所有派生结果已刷新。正向发生 `zIndex` 副作用，严格源验收失败，正式调度保持关闭。具体单次日志、复核及残留差异见[实机记录](source-validation.md)；本页此前未写入的描述保留为当时的历史状态。
