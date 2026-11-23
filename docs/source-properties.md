# Source 局部属性适配器契约

`schematic-source-properties-v1` 已有固定脚本、预检、公共执行日志接入和离线核验，当前 **`dispatch_enabled=false`**。正式计划可以离线预览，但不能创建认可记录或调度写入，也没有启用开关。独立的 `execute-source-trial` 仅用于明确授权、绑定单属性 case 的一次实验，不开放正式适配器；2026-11-17 Name 写入/恢复实测发现 `zIndex` 副作用，正向严格源验收未过，详见[实验记录](source-validation.md)。

同日后续[修复与确认实验](source-experiment.md)已完成：一次受严格完整差分约束的历史修复将 `zIndex` 恢复为 16，实验 CLI 默认改用经典属性 `get/modify` 后端，U2 Name 和 C3 Value 实测只改目标值、保留层级，最终完整恢复最初源/网表/详细 DRC。此为助手的替代更新路径，Source.update 的客户端副作用本身仍存在，正式适配器保持关闭。

## 参数与范围

仅接受 `schema_version=2` 的协同绘图计划，一次计划只修改一个已有器件的属性值。操作为 `set_property`，`parameters` 精确为：

```json
{
  "primitive_id": "实际画布器件ID",
  "patch": {"Value": "明确目标值"},
  "source_target": {
    "projectId": "实际工程UUID",
    "schematicName": "实际原理图名称",
    "pageName": "实际页名称"
  }
}
```

允许已有 `Supplier Part`、`Value`、`Voltage Rating`、`Datasheet`、`Name` 的值变更。值为 1–1000 字符的非空字符串；目标属性必须已存在，全部值已符合目标时不写入。禁止添加/删除属性、修改位号/库/封装/几何/引脚/网络或改变显示位置与可见性。批量属性、属性显示和连接操作需单独契约。

Source API 虽支持显式页目标，本实现进一步限于当前打开原理图页。预检绑定窗口、工程/页/tab UUID、完整源文件摘要和 Source 三段名称目标；固定脚本在写入前、提交前及回读时核对 UUID 与名称映射。客户端严格限定 `4.1.100`，不因 API 名称存在而自动开放。

## 固定调用与回读

调用 `sch_SourcePrimitiveComponent.get(options)` 后，通过 `update({...options, properties: patch})` 增量覆盖键值。脚本不接受模型提供的 JavaScript。`ok` 返回仅作为已提交线索，之后至多轮询 24 次、间隔 250 ms，并检查 6 s 的轮询期限；须看到预期属性和不同于输入的完整源，且详情与源连续三次相同才返回候选成功。单次 API 调用若阻塞仍受 Bridge 的传输超时约束，轮询期限不等于整次操作的硬实时上限。

Python 再独立核对：图元、窗口内身份与名称目标一致，详情中的其他字段不变，属性表完整保留且只有补丁键改变；完整源记录集合不变，只允许目标器件指定 ATTR 的 `value` 变化，其他内容均保持原样。复用旧属性修复核验器，不根据返回码或稳定的旧数据宣布完成。

这些检查只证明所声明的属性/源差异；本适配器尚未增加写后的新鲜网表和 DRC 差分验收，不能据此宣称连接、电气或整板已验证。Source Netlist/DRC 也没有被作为可用替代路径接入。

现已提供[独立审计差分与反馈工具](audit-and-feedback.md)：`query --kind audit` 复用 ManufactureData 网表与详细 DRC，`compare-design-audits` 进行离线差分，`drawing-feedback` 可检查与执行前后源的绑定。两次只读实测已通过，但派生数据与 Source 版本的刷新关联仍未知；这些工具尚未接入本适配器的自动验收，`dispatch_enabled=false` 保持不变。

写入前错误返回 `blocked`，已尝试写入后的异常、目标变化、超时或状态不收敛返回 `uncertain`。公共执行器保留 intent/reply/snapshot/result 和目标锁，不重试写入、不保存、不删除、不自动回滚。未知状态须先检查现场和日志，再按实际影响重新计划。

## API 证据

- 本机 `easyeda-api-skill` 1.1.29：`DMT_Schematic.getCurrentSchematicInfo()`、`getCurrentSchematicPageInfo()` 及身份接口；Source 新接口以客户端实时帮助补足。
- [4.1.100 实时 Source 方法帮助](../../easyeda-设计助手-data/api-schematic-tools-20261115/source-details.json)：`SCH_SourcePrimitiveComponent.get/update`、异步生效说明。
- [Source 参数类型](../../easyeda-设计助手-data/api-schematic-tools-20261115/source-types.json)：三段显式目标、`primitiveId` 和 `properties` 按键增量覆盖。
- [原只读实测](../../easyeda-设计助手-data/api-schematic-tools-20261115/source-read.json)与[可实施性评估](../../easyeda-设计助手-data/improvement-feasibility-20261117.md)：历史证据及待验证边界。

这些外部数据不随源码仓库发布；离线测试可独立运行。`test_source_adapter.py` 在 Node 模拟运行时执行固定脚本，覆盖延迟、旧源、属性丢失、目标/版本变化和超时；测试仅在进程内临时打开调度，不改变生产设置。

## 后续隔离验收

已增加[单属性验收准备与证据核对](source-validation.md)：`prepare-source-validation` 归档具体候选，`check-source-validation` 核对严格源变化及新值在网表中的出现。两者离线工作，不授予修改许可或改变调度开关；属性被导出观察到与 DRC 已刷新分别记录。独立实验路径复用固定脚本、严格核验和单次写入日志，不接受任意脚本；历史锁保留，未知结果通过完整现场复核另写 resolution，原失败结果不修改。

2026-11-17 用户明确授权当前 `Agent_test` 工程用于实验，随后对 U2 Name 进行一次写入和一次恢复。正向只有 Name ATTR 的值与 `zIndex:16→15` 改变，严格核验正确保留 `uncertain`；网表只观察到 Name 改变，新增一条 U2 `ATTRMATCHCODE`。现场完整复核后从新基线单独恢复 Name，恢复实验为 `property_observed`。最终器件详情、完整网表字节与详细 DRC 恢复原基线，但 `zIndex=15` 残留，不能声称完整源恢复。

能力目录已据实标为 `live-name-roundtrip-strict-failed`，正式调度仍关闭，没有把 `zIndex` 忽略或列为任意允许差异。后续须查明此副作用并界定契约、获取派生刷新关联证据，再评估开放范围。现有样本只支持 4.1.100 的 U2 Name 局部观察，不覆盖其他属性、版本或所有 DRC 规则。
