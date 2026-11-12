# 原理图连接查询

选型、拓扑和局部检查应先了解实际关联元件与网络。`context` 处理当前选择；`schematic` 提供不依赖选中对象的连接快照。

```powershell
python -B src/design_assist.py schematic --window-id '<窗口 ID>' --save
python -B src/design_assist.py inspect '<快照.json>' --designator U1
python -B src/design_assist.py inspect '<快照.json>' --component-id gge1 --depth 2 --limit 50
python -B src/design_assist.py inspect '<快照.json>' --net '目标网络'
```

固定调用 `sch_ManufactureData.getNetlistFile('design-assist', 'JLCEDA')`，仅读取返回 File 的文字，不调用 saveFile，也不激活其他文档。方法和枚举已核对本机 `easyeda-api-skill` 1.1.29。该接口为 beta，导出可能因原理图校验不通过失败；失败时报告错误，不自动修复或换用写接口。

快照保存 API 原始 JSON、其 UTF-8 SHA256、工程与焦点文档身份、窗口、采集时间和范围限制。当前只接受已验证的 JLCEDA `version: 2.0.0`、`components → props/pinInfoMap → number/net` 结构；未知格式、缺少网络字段或重复 JSON 键拒绝解析，不当成无连接。网表内容限制 20 MiB。

范围以一次 API 导出为准，焦点页是采集锚点，不承诺网表只含此页或覆盖所有板卡。不得把不同快照或板中的同名网络直接合并。网表元件键仅用于当前导出，未证明等同画布图元 ID。多窗口必须明确指定；采集前后复核工程/焦点，文档内部并发编辑无法检测，绘制后应重新读取。

`inspect` 精确匹配位号、元件键或网络名，只能指定其中一个。重复位号拒绝并要求用元件键。以元件为起点时，深度 1 表示该元件→各网络→直接相邻元件；以网络为起点时，第一轮返回该网络上的元件，后续轮再沿这些元件扩展。深度最大 3，元件数默认 100、最大 500；每条展开网络最多返回 2000 个引脚端点。截断返回 `truncated`、`total_members`、`omitted_members`。

结果保留元件原始属性和引脚信息，可查邻居的型号、值、手册链接与引脚连接。边界元件其他引脚虽保留原值，其网络可能尚未展开；继续定点查询以回答实际问题。空字符串网络不会把未分配网络的引脚连在一起；空网不是已证明的悬空故障。查询不推定信号方向、网名含义、电压、电流或功能。

将快照路径/哈希、目标和实际使用的连接登记为任务来源。针对供电、负载、驱动、反馈与保护继续查证，结合用户工况与厂商原文推理，遵循 [通用指引](agent-guide.md) 和对应模式 playbook。
