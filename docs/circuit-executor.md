# 实验性原理图电路执行器 v3

4.1.100已有网络支路已由独立[schematic-branch-v5](branch-editing.md)完成三步实机验收；固定操作/执行日志复用本执行器，但旧v3版本和契约不变。

`execution_contract: "schematic-circuit-v3"` 将一个具体认可方案按顺序执行为器件、网络标号和导线。未设置该字段的计划仍采用 v2 器件放置契约，参数不能混用。此实现尚未完成真实 CAD 电路写入验证；测试通过仅证明固定脚本和模拟流程符合约定。API 依据为本机 MIT 许可 `easyeda-api-skill` 1.1.29，未引入新的运行依赖或复制其他项目实现。

## 支持范围

非空白页的独立模块新增已由另行版本化的 [v4 契约](incremental-editing.md)在 4.1.100 完成三电阻闭环 9 步实测。下述 v3 的旧版本与空白页限制保持不变，不能混用两个适配器的基线或认可记录。

- 仅空白原理图电路，可含图框；源预检与执行前实时器件查询共同检查。最终 API 导出网表必须恰好只含方案新增器件及完整引脚；导出若包含其他页电路，也会停止核验。
- 使用编辑器 `3.2.149.88089769`。普通库器件允许尚未导入工程，由创建后的工程 DEVICE/META.source 精确核对来源；不能以 Symbol/Footprint UUID 替代 Device UUID。
- 一个方案最多 50 步；先放全部器件，再放网络标号，最后放导线。不支持镜像、未连接引脚、跨网络交叉、增量编辑已有电路、属性修改、PCB、保存或自动回滚。
- 每个网络使用一个连通的 `add_wire` 操作，可以包含多条正交分支。按精确几何检查每个引脚只接声明网络、标号落在对应导线、器件本体不重叠。

## 准备与执行

在项目目录按需运行只读归档命令：

```powershell
python -B src/design_assist.py drawing-snapshot --window-id '<窗口ID>'
python -B src/design_assist.py drawing-device-search --keyword 'AMS1117-3.3' --window-id '<窗口ID>'
python -B src/design_assist.py drawing-device-details --library-uuid '<库UUID>' --device-uuid '<器件UUID>' --window-id '<窗口ID>'
python -B src/design_assist.py drawing-device-archive --library-uuid '<库UUID>' --device-uuid '<器件UUID>' --window-id '<窗口ID>'
```

库归档命令保存 elibz2 的原始 base64、实际大小和库身份。计划的 `library_evidence.path` 必须是外部数据目录中的归档 **JSON 文件绝对路径**，`sha256` 是该 JSON 文件的哈希。预检在内存中读取归档，拒绝大小异常、符号身份不符、子部件歧义及重复引脚编号。库引脚编号支持 `Pin Number` 或 `NUMBER` 属性；同一引脚出现多个候选时拒绝。

填好项目和计划后：

```powershell
python -B src/design_assist.py prepare-drawing-execution '<项目.json>' '<计划.json>'
```

向用户展示预览中的目标、操作、连接、依据与实验局限。仅在已有真实的具体方案认可后，按[绘图策略](autodraw-policy.md)记录认可并执行。开发授权或模拟测试中的认可文本不构成真实画布方案认可。计划或项目变更会使旧认可失效。

## 参数与完整结构样例

以下为虚构的单引脚器件契约样例，不是 AMS1117 电路，也不能直接用于真实画布。需替换项目 ID、目标、路径/哈希、器件身份、库几何和工程依据后才可预检。所有数值采用 API 原理图坐标，单位 `10mil`；并非 PCB 的 1mil。样例三个操作及顶层字段展示完整结构：

```json
{
  "schema_version": 1,
  "project_id": "实际项目档案ID",
  "created_at": "实际创建时间",
  "request": "添加已核验单引脚器件、GND 标号及连接",
  "execution_contract": "schematic-circuit-v3",
  "target": {
    "window_id": "实际窗口ID", "project_uuid": "实际工程UUID",
    "document_uuid": "实际页UUID", "domain": "schematic",
    "snapshot_path": "C:/外部数据目录/source.json",
    "snapshot_sha256": "替换为快照JSON文件的64位小写SHA256"
  },
  "operations": [
    {
      "id": "device", "operation": "place_component", "target": "新增器件",
      "before": "无器件", "after": "器件位于100,200",
      "expected_result": "身份、方向和完整引脚回读一致",
      "parameters": {
        "library_uuid": "实际库UUID", "device_uuid": "实际器件UUID",
        "library_evidence": {
          "path": "C:/外部数据目录/device-archive.json",
          "sha256": "替换为库归档JSON文件的64位小写SHA256"
        },
        "sub_part_name": "", "x": 100, "y": 200, "unit": "10mil",
        "requested_api_rotation": 0, "expected_canvas_rotation": 0,
        "rotation_convention": "easyeda-pro-api-0.2.53-negated-create",
        "mirror": false, "add_into_bom": true, "add_into_pcb": true,
        "expected_pins": [{"number": "1", "x": 110, "y": 195}]
      }
    },
    {
      "id": "ground", "operation": "place_net_flag", "target": "GND 标号",
      "before": "无标号", "after": "GND 位于130,195",
      "expected_result": "标号网络、位置及连接点回读一致",
      "parameters": {
        "identification": "Ground", "net": "GND", "x": 130, "y": 195,
        "unit": "10mil", "requested_api_rotation": 0, "expected_canvas_rotation": 0,
        "rotation_convention": "easyeda-pro-api-0.2.53-negated-create", "mirror": false
      }
    },
    {
      "id": "ground-wire", "operation": "add_wire", "target": "GND 连接",
      "before": "未连接", "after": "器件引脚1与标号连接",
      "expected_result": "导线几何一致，最终引脚网络为GND",
      "parameters": {"unit": "10mil", "net": "GND", "line": [110, 195, 130, 195]}
    }
  ],
  "connectivity": [{"component": "device", "pin": "1", "net": "GND"}],
  "evidence": ["实际库归档及经核对的工程依据"],
  "unresolved": [],
  "readback_plan": "每步核对图元和完整源，最终逐引脚网表比对及详细DRC",
  "failure_plan": "停止后续步骤、保留现场和锁，人工核对后修订方案",
  "approval": null
}
```

`place_component.parameters` 必须恰好包含样例中的字段，不含 v2 的 `mapping_evidence` 或 `expected_project_device_uuid`。`sub_part_name` 必须是归档中明确存在的字符串，不接受 null 通配；`expected_pins` 精确列出所有引脚编号及目标 API 坐标。库符号源绕原点逆时针旋转后平移，转换到 API 坐标时反转 Y；`expected_canvas_rotation=(-requested_api_rotation)%360`。这套库几何转换尚未实机验证；已有源与 API 器件坐标的 Y 反向记录不等于已验证所有符号和角度。

`place_net_flag.identification` 只接受 `Power`、`Ground`、`AnalogGround`、`ProtectGround`；预期一个位于放置原点的连接引脚。该引脚的实际编号及 getter 返回仍需实机核对；不兼容时停止，不推定创建失败。

`add_wire.line` 接受一个折线数组或多个折线数组，如 `[[100,200,140,200],[120,200,120,240]]`。坐标有限且绝对值不超过 1000000；不接受斜线、零长线或不连通分支。`connectivity.component` 引用器件操作 ID，`pin` 是精确引脚编号，必须覆盖所有新增器件引脚且不重复。

## 执行核验与故障处理

每步先持久化 intent，再在绑定窗口核对身份、完整源及编辑器版本，执行一次固定创建调用。写入不重试；只读 getter 和完整源最多进行 12 轮稳定性读取，轮间隔 250ms，要求三次相同及普通器件位号就绪。创建器件前重新取库归档并比对符号几何/属性，创建后从工程导出核验实际 DEVICE 来源，Python 再独立核对回读。

完整源差异必须仅含本步骤图元及其 ATTR，旧对象不可改变。此限制可能与编辑器真实连线时自动改线或生成 junction 的行为冲突；尚未实测，不会自动放宽检测。导线源 Y 反向和网络标号连接点也需实测。执行期间避免同时编辑或切换焦点，检查与创建之间没有 CAD 事务锁。

全部步骤完成后导出网表，将操作 ID 对应到实际位号，逐引脚精确比较网络，拒绝遗漏/额外器件或引脚。调用 `sch_Drc.check(true,false,true)` 读取详细违规；仅空数组通过，`warn`、`error`、`fatalError` 均使执行核验失败，未知返回格式拒绝。DRC 不证明选型、稳定性、热设计或整板功能正确。

| 状态 | 含义 | 目标锁 |
|---|---|---|
| `blocked` | 未发出写入，前置检查失败 | 结果日志落盘后释放 |
| `partial` | 已有步骤回读成功，后续步骤在写前失败 | 保留 |
| `uncertain` | 写入可能已发生，或回读/最终审计不能可靠完成 | 保留 |
| `verification_failed` | 最终网表网络不一致或 DRC 存在违规 | 保留 |
| `verified` | 全部步骤与适用的最终核验通过 | 结果日志落盘后释放 |

`completed` 只统计逐步回读通过的操作，不能解释为整个电路通过。CLI 仅 `verified` 返回 0，其余执行结果返回 3；输入/预检异常返回 2。日志持久化自身失败可能无法生成 result，缺日志不能证明未写入。方案只能领取一次；锁协调同一 data 目录内的本工具，不锁住其他工具或 CAD 人工操作。

出现保留锁或进程中断时，按执行目录的 intent/reply/snapshot、最终网表和 DRC 核对真实画布，确认原请求已结束。人工决定后才移除对应目标锁并使用新快照/修订方案；不清理领取记录来重放旧计划，不自动删除、保存或回滚。
