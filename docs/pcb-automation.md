# PCB 自动化与协作契约

本项目把原理图完成后的 PCB 工作分成四种用途：

| 用途 | 计划范围 | 项目行为 |
|---|---|---|
| 整板绘制 | 全部 PCB 器件和网络 | 采集完整基线，先完成布局/关键网络，再分批处理普通网络和铜区；每批回读后重新规划。 |
| 部分绘制 | 明确器件、网络和板内区域 | 只允许修改声明范围，范围外图元和网络必须保持不变。 |
| 特定线路建议 | 网络、端点或器件邻域 | 只输出路径、层、回流、线宽/阻抗依据、取舍和验证步骤，不写 CAD。 |
| 布局及关键网络 | 器件位置与关键网络 | 可执行受限布局和关键线计划，普通网络交回用户；移动器件前必须把其所有焊盘网络纳入复核范围。 |

## 从已完成原理图开始

1. 在 EasyEDA 中确认 PCB 已从当前原理图同步，保存原理图网表快照：

   ```powershell
   python -B src/design_assist.py schematic --save
   python -B src/design_assist.py pcb-capture --window-id '<PCB窗口ID>'
   ```

   `pcb-capture` 固定读取工程/文档身份、完整 PCB 源、器件及焊盘、走线、过孔、铺铜、层、层叠、网络、规则和详细 DRC。坐标采用 API 的 data-origin mil；不接受凭界面截图推定坐标。

2. 建立用途计划。`full`、`partial`、`advice` 和 `layout-critical` 分别对应四种方式：

   ```powershell
   python -B src/design_assist.py new-pcb-plan '<PCB快照.json>' '<原理图快照.json>' `
     --intent layout-critical --request '摆放功率级并完成输入回路' `
     --component-id '<功率级器件ID>' --net '<输入网络>' --net '<返回网络>'
   ```

   局部用途必须用 `--component-id` 或 `--net` 明确范围；整板用途自动登记基线中的全部器件和网络。计划还要补齐板框区域、板厂层叠/规则、坐标校准、功率和高速网络分类、返回路径及验证动作。

3. 对计划执行离线检查：原理图与 PCB 位号/焊盘网络映射、范围、图层、锁定器件、参数边界、完整源和基线哈希。`review-pcb-plan` 输出的 `ready_for_approval` 为真后，展示具体操作并记录真实认可：

   ```powershell
   python -B src/design_assist.py review-pcb-plan '<计划.json>'
   python -B src/design_assist.py approve-pcb-plan '<计划.json>' `
     --shown-plan-sha256 '<review结果中的plan_sha256>' `
     --user-message '我认可这批PCB布局和关键网络操作' --reference '对话记录#...'
   ```

4. 当前 PCB 写入适配器已有局部工具样本实测，仍保留实验入口。执行必须显式使用 `--experimental-pcb`；程序在每个操作前检查完整源，写入后重新读取并核对源差异、焊盘网络和详细 DRC。异常时保留日志和现场，不自动重试、删除、保存或回滚：

   ```powershell
   python -B src/design_assist.py execute-pcb-plan '<计划.json>' '<认可.json>' `
     --window-id '<PCB窗口ID>' --experimental-pcb
   ```

## 固定操作

当前契约支持：移动器件、增加单段走线、增加过孔、在没有既有板框时增加闭合板框、增加铜区、重建指定铜区、对明确的网络列表调用 EasyEDA 自动布线、整板单独调用原生自动布局。自动布线拒绝空网络列表，避免误触发整板布线；高功率、高速和敏感网络不能直接交给普通自动布线。自动布局或新增板框完成后必须重新采集基线，再进行关键网络规划。当前实验契约绑定编辑器 `4.1.100`，其他版本须先适配和验证。

2026-11-22 在 `Agent_test` 完成17器件/40焊盘采集与映射、两电阻移动旋转、顶底走线、通孔、板框、铜区重建、一个明确网络的自动布线和制造文件导出。用户明确该板仅用于工具链测试，详细参数和未通过项见[实机验证记录](pcb-live-verification.md)。4.1.100 无物理层叠读取接口时保留原始错误，正常写入需提供 `constraints.external_stackup_evidence`；几何采集的 `complete=true` 不表示层叠已确认。`importChanges` 的 `true` 也不能代替客户端“应用修改”后实际器件/焊盘回读。

实测发现铜区创建和属性修改没有兑现任意边界线宽/优先级。预检仅允许该版本每层首个铜区的已知默认参数（边框0.2mil、大写名称、优先级1），其他参数拒绝；回读仍须与具体请求一致。边框宽度不是实际载流铜宽，不作为温升或承载能力依据。原生整板布局不会带动既有铜，已布线或铺铜的板预检阻断，改用具体局部方案；未布线板的整板布局闭环仍待验证。

计划及认可各自绑定输入摘要；同一计划只能领取一次。失败后的锁和运行档案保留在外部数据目录，核对最后的 intent、reply、failure 及真实画布后，用新基线和修订计划继续。工具不自动清理锁或补偿未知写入。`pcb-capture --without-drc` 可用于只读诊断，缺少详细 DRC 的基线不能认可写入。

## 计划参数与交付边界

新建计划是待补充的草稿，不会从网名猜关键网络或自动接受未知工况。agent 按原件与 CAD 证据填入下列内容，再展示方案：

| 字段 | 含义 |
|---|---|
| `scope.component_ids` / `scope.nets` | 明确允许操作和复核的器件/网络，局部任务不扩大到全板 |
| `constraints.unit` / `coordinate_system` | 固定为 `mil` / `data-origin` |
| `constraints.calibration` | 当前 `document_uuid`、`editor_version` 及实际坐标/旋转/层映射核验 `evidence` |
| `constraints.board_region` | 已核实的板内可操作矩形 `[xmin,ymin,xmax,ymax]`；复杂板框、孔槽与禁布仍由实际板框、规则和 DRC 约束 |
| `stackup_evidence` / `rules_evidence` | 层叠和制造规则原件、计算或板厂确认的引用 |
| `net_constraints.<网络>` | `kind` 为 `power`、`high-speed`、`sensitive`、`ordinary`；记录 `source`、`return_path`、`validation` 和适用的 `min_width` |
| `power_analysis` | 功率网络补充 `operating_conditions`、`fault_and_protection`、`loss_and_thermal` 的计算/复核依据 |
| `signal_analysis` | 高速网络补充 `edge_and_topology`、`impedance_and_timing`、`reference_transitions`；敏感网络另写 `coupling_review` |
| `min_via_hole` / `min_annular_ring` | 需要过孔时写入对应工艺的最小孔径/环宽，单位 mil |
| `excluded_schematic_components` | 仅用于经核实不进 PCB 的原理图对象，按原理图元件键说明理由；不自动猜测排除 |

每个操作必须含 `id`、`operation`、`parameters`、`rationale`。原生自动布局只接受未布线/未铺铜板的整板单独一批，机械固定件须事先锁定；普通自动布线使用 `RoutingNets` 非空列表和 `KEEP` 模式保留已有走线。直接走线使用明确路径的单段 `add_line`，差分、回流与载流约束由 agent 提前规划。源格式尚未适配、读取不完整、网络映射冲突、范围外变化或新增详细 DRC 均阻止继续。

建议用途交付“端点、路径、参考、约束、证据、未知及验证动作”；绘制用途交付真实前后快照、具体变更、范围外保持检查和剩余工作。布局/关键网络用途要列出交回用户的普通网络。整板用途还需逐层回流、热与关键网络复查、全部未连接/DRC、制造包独立查看和样板计划，不能用一次执行返回 `verified` 表示整板完成。高功率/高速同板须按设计方法核对电源运行及负载跳变时的链路表现。

`pcb-workflow --intent <用途>` 可查看流程；`inspect-pcb <快照> --net <精确网名>` 查原始端点与铜层图元；`compare-pcb <前快照> <后快照>` 比较同板完整源记录和实际详细 DRC。制造导出生成原文件及 SHA256 清单，文件名固定，原始 API 文件名只作为元数据保留。

`export-pcb` 可导出绑定当前工程和完整源的制造文件原件，提供 Gerber、BOM、贴片坐标和 IPC-D-356A；它不下单、不代表制造商已经接受文件。板厂、实验室和用户仍需按板卡版本核对层叠、阻抗、热、SI/PI、EMC/ESD 及实板结果。

## 四层板的规划与执行约束

四层板及两层升四层先按[四层 PCB 设计指导](pcb-four-layer.md)记录每层的物理位置、用途、参考网/层和允许网络，引用板厂铜厚/介质/规则。将依据放入现有 `stackup_evidence`，API 不可用时另附 `external_stackup_evidence`；各关键网络的 `return_path` 和适用的 `reference_transitions` 说明具体返回路径。

agent 据此限定每条 `add_line` 的层，以及 `route_nets.parameters.layers` 的信号层列表。指定为连续 GND 的层不进入普通自动布线范围；失败时先重看布局和出线，不自动加入参考层。补线之后重建受影响铜区，回读实际填充，并逐层复核平面缺口和换层地过孔。

这些是方案准备和人工/agent 复查要求，不新增计划必填字段，也不表示现有执行器会自动检查平面连续性。几何层数、`complete=true`、焊盘网络一致和详细 DRC=0 与物理叠层、阻抗、回流及热验收分别报告。测试工程证明的 CAD 工具链结果不能作为通用四层设计已验收的依据。

## 不能由自动化单独证明的事项

完整源和 DRC 回读只证明声明 CAD 操作在当前 API 模型中的结果。它们不能证明电源额定值、故障能量、温升、阻抗、回流、时序、眼图、误码率、EMC、安规或制造可行性。高功率/高速板的关键网络由 agent 按手册、工况和板厂规则提出约束，必要时交由板厂或实验室验证。

当前实现属于“工程 agent + 固定 CAD 适配器”：agent 负责选择、约束、路径、风险和验证计划，固定适配器负责已声明范围内的读取、调用和回读。没有合适的板卡、原理图快照、层叠、规则或工况证据时，系统只给条件化建议，不生成无依据的绝对坐标和数值。
