# EasyEDA Design Assist

原理图完成并同步 PCB 后，项目提供整板绘制、部分绘制、特定线路建议、器件布局及关键网络绘制四种计划。详见[PCB 自动化契约](docs/pcb-automation.md)。

2026-11-22 在 `Agent_test` 实测17器件/40焊盘读取与映射、局部移动/旋转、顶底走线、通孔、板框、铜区重建、单网络自动布线及制造导出；铜区自定义参数和已布线板原生整板布局保留限制。该工程仅是工具样本，记录见[PCB 实机验证](docs/pcb-live-verification.md)。

中文名：EasyEDA 设计助手。GitHub 仓库名：`easyeda-design-assist`。本目录为独立 Git 仓库，源码、文档和测试独立维护。

**定位**：为 agent 提供辅助绘板的工程思路，以及通过嘉立创 API 和原始手册获取信息的工具。绘制者提出问题后，由 agent 基于真实元件、网络与工况给出建议。

## 目标

解决 agent 辅助绘板时方法不足、缺少工程依据的问题。手册功能保留原件访问与快速查询能力；选型和拓扑功能指导 agent 查阅关联元件与网络，提出可操作的绘制方案。与 `epro2-辅助绘制及审核` 配合：本项目服务设计过程，审核项目负责冻结版本的独立核验。

从 [Agent 指引](docs/agent-guide.md) 开始；[选型方法](docs/playbooks/selection.md) 与 [拓扑方法](docs/playbooks/topology.md) 给出具体步骤。新任务的 brief 自动附上对应指引。

原理图绘制指引覆盖模块框与标题、行列对齐、紧凑走线和标注净距。原理图方案审查及执行预览附带版式复核清单，走线预检给出计划新增导线的长度、直角折点和跨网交叉数。画后可用原生 SVG 筛查走线遮挡文字，再独立核对网表和详细 DRC。[原理图绘制指引](docs/playbooks/schematic.md) · [开源致谢](ACKNOWLEDGMENTS.md)

PCB 采用“约束先行、路径驱动、分层复查”：工况/封装与制造规则 → 机械/分区/布局 → 关键回路和普通布线 → 回流/铺铜 → 投板复核 → 样板实测与改版。个人开发者的高功率/高速板保留额定值、故障、热、阻抗、回流和适用实测要求，可将专项分析与测试委托板厂/实验室，并明确证据覆盖和未验证风险。`guide --mode pcb` 带入对应指引；完整采集、方案规划及实验性受限 PCB 操作通过 EasyEDA 客户端执行，真实验收状态见[PCB 自动化契约](docs/pcb-automation.md)。[PCB 设计方法与来源](docs/pcb-design-method.md) · [PCB 操作指引](docs/playbooks/pcb.md)

四层板及两层升四层提供独立的[四层 PCB 设计指导](docs/pcb-four-layer.md)：比较层叠方案，确定连续地参考、配电及换层返回路径，记录板厂物理层叠与逐层验收证据。核心规则随 `guide --mode pcb` 带入任务，DRC 通过与工程验收分别报告。

项目级设计框架提供离线档案、六阶段工作指引、记录缺项审查、显式依赖影响分析和可追溯修订归档：`new-project`、`project-guide`、`project-review`、`project-impact`、`project-revise`、`validate-project`。参见[项目设计方案](docs/project-design.md)与[阶段指引](docs/project-stages.md)。它不自动判定阶段通过或修改 CAD。

2026-11-17 首期架构已接入双交互模式、四级分析尺度、五步研发指引、统一操作目录/只读查询、建议—计划—结果追踪和可选多板引脚映射。`advice` 为默认辅助绘制，`collaborative` 用于准备协同绘制方案，均不直接授予写入权限。用法、兼容规则及未完成边界见[首期构建说明](docs/workflow-build.md)。新版 [Source 属性适配器](docs/source-properties.md)已实现但保持关闭，不能把离线预览或测试通过当作实机写入验收。

已进一步接入[网表/详细 DRC 差分与独立执行反馈](docs/audit-and-feedback.md)：`query --kind audit` 采集证据，`compare-design-audits` 离线比较并归档，`drawing-feedback` 生成后续核验和受影响对象。源稳定不证明派生结果已刷新，观察一致也不自动关闭写后或工程验收。

[Source 单属性验证](docs/source-validation.md)提供离线准备/核对与独立单次实验入口。随后已完成[层级残留修复与经典属性接口实验](docs/source-experiment.md)：默认实验后端通过 `sch_PrimitiveAttribute.modify` 直接修改已有属性，U2 Name、C3 Value 的往返实测均保留层级；原 `zIndex` 残留已修复，最终完整源、网表与详细 DRC 均恢复最初基线。新增 `check-source-roundtrip` 区分已声明的实验响应与普通 DRC 门禁，正式 Source 调度继续关闭。

自动绘图触发规则：明确提出辅助绘制后，先形成可审阅的操作方案；用户认可方案后，允许在方案范围内执行，不为每一步重复询问。目标文档或方案范围变化时暂停并更新方案。新项目默认 `explicit_batch`，要求方案认可、操作范围和目标快照约束。原理图器件放置执行器 v2 已实现工程 DEVICE 来源映射、API/画布角度换算和完整引脚回读检查，通过单次实机创建 R5 的独立回查（请求90°/画布270°，不镜像，BOM/PCB关闭）；其余组合尚未实测，首次 R4 的旧执行结果仍为 uncertain。

实验性 v3 执行器补充了空白原理图上的库器件导入、网络标号、正交多分支导线，以及最终逐引脚网表和详细 DRC 核对。每步检查完整源差异，部分完成、回读未知或验证失败时保留现场与目标锁。v3 已有模拟流程与固定脚本测试，也有历史真实尝试和局部修复记录，**通用电路端到端实机验收尚未完成**；不能宣称支持通用已有电路编辑或 PCB 写入。使用方法见[电路执行器契约](docs/circuit-executor.md)、[自动绘图策略](docs/autodraw-policy.md)与[实测记录](docs/live-api-verification.md)。

## 快速开始

2026-11-18 新增[非空白页增量编辑 v4](docs/incremental-editing.md)：4.1.100 实机完成三电阻闭环、三个标号和三组导线共 9 步，独立回查六个新引脚连接精确，原有源记录、8 个网表器件及 6 条详细 DRC 警告保持不变。模块保留在当前测试页，未调用保存。

非空白页新增电阻接入两个已有网络，已完成 3 步实机执行与独立复核。执行器从原生 SVG 读取 A4 内框和标题栏保留区，并在预检中核对编辑区域与新增支路线段。目标线组接触其他线组时预检拒绝。旧对象移动、删除、引脚重接和 PCB 写入不在当前实现范围内。[已有网络新增支路 v5](docs/branch-editing.md)

在项目目录运行：

```powershell
python -B src/design_assist.py connect
python -B src/design_assist.py status
python -B src/design_assist.py context
python -B src/design_assist.py guide --mode selection
python -B src/design_assist.py workflow --scope component --missing ratings
python -B src/design_assist.py capabilities --editor-version 4.1.100
python -B src/design_assist.py query --kind context
python -B src/design_assist.py query --kind audit --save
python -B src/design_assist.py schematic --save
python -B src/design_assist.py drawing-components
python -B src/design_assist.py capture-incremental-baseline --window-id '<窗口ID>'
python -B src/design_assist.py drawing-project-info
python -B src/design_assist.py drawing-pins --primitive-id '<画布器件图元ID>'
python -B src/design_assist.py inspect '<连接快照.json>' --designator U1 --depth 1
python -B src/design_assist.py new-task --mode schematic --goal '核对当前稳压器外围并指出缺失约束'
python -B src/design_assist.py new-task --mode pcb --live --goal '分析所选网络的回流与铺铜条件'
python -B src/design_assist.py validate-task '<任务档案路径>'
python -B src/design_assist.py new-drawing-plan '<项目档案路径>' --request '为接口增加保护电路并列出连接方案'
python -B src/design_assist.py review-drawing-plan '<项目档案路径>' '<绘图计划路径>'
python -B src/design_assist.py drawing-trace '<项目档案路径>' '<绘图计划路径>'
python -B src/design_assist.py compare-design-audits '<修改前审计.json>' '<修改后审计.json>' --save
python -B src/design_assist.py drawing-feedback '<项目档案路径>' '<绘图计划路径>'
python -B src/design_assist.py handoff '<任务档案路径>'
```

项目级离线建档示例：

```powershell
python -B src/design_assist.py new-project --name '项目名称' --goal '板卡用途和目标'
python -B src/design_assist.py project-guide --stage framing
python -B src/design_assist.py project-review '<project.json>'
python -B src/design_assist.py validate-project '<project.json>'
python -B src/design_assist.py project-impact '<旧版本.json>' '<编辑版本.json>'
python -B src/design_assist.py project-revise '<旧版本.json>' '<编辑版本.json>' --reason '变更原因'
```

修订命令会保留两个输入文件的原始字节、生成修订档案和影响报告，并为归档内容生成 SHA256 清单；影响分析只沿档案中显式登记的关系传播。档案中的 `pending_reviews` 可用 `review_records` 按当前设计快照摘要关闭，项目审查报告同时列出阶段记录缺项、任务阻塞和待复核项。相对的 `task_path` 按项目 JSON 所在目录解析，关联任务须提供 `task_id` 与 `task_sha256`；任务文件变化后应更新引用并重新复核。详见项目设计文档。

Windows 可直接双击项目根目录的 `start-api.cmd`。脚本检查 Python、复用已有 bridge；需要启动时读取已安装的 `easyeda-api-skill`，检查 Node.js 18+ 和 `ws` 依赖，并在后台启动 bridge。关闭启动窗口后 bridge 继续运行。默认等待客户端 60 秒；未连接时按提示在 EDA 的 **API Gateway → 重新连接** 操作。脚本不能代替客户端安装/启用扩展。

命令行可运行 `start-api.cmd --timeout 0` 只保证 bridge 运行，或 `python -B src/design_assist.py connect --skill-dir '<技能目录>'` 指定位置；也支持 `EASYEDA_SKILL_DIR` 环境变量。默认依次查找用户目录 `.claude/skills/easyeda-api-skill`、`.codex/skills/easyeda-api-skill`。新启动日志写入外部 data 目录的 `runtime/`，每次连接状态保存到 `connections/`。多窗口时传 `--window-id`，不会改变全局活动窗口。自动化调用批处理可设置 `EASYEDA_ASSIST_NO_PAUSE=1`。退出码：0 为成功（`--timeout 0` 时仅表示 bridge 就绪），2 为运行错误，3 为客户端等待超时或需要选择窗口。

`context` 只调用固定的只读 API：当前工程、焦点文档、所选图元 ID 和按类型允许读取的属性。原理图支持元件与导线，PCB 支持元件、直线、过孔和铺铜；元件属性包括位号、制造商型号、供应商编号及自定义属性等。需要嘉立创EDA客户端及 API Gateway bridge 已连接；它不自动启动 bridge。多个窗口时用 `--window-id` 明确指定。`context --save` 会保存上下文并输出路径。

图元详情接口为 beta，每次最多读取 100 个对象的属性，完整 ID 列表保留。上下文 v2 分别记录详情不可用、ID 不一致、截断、字段缺失与读取报错；旧版 v1 上下文仍可导入。字段保留 API 原名和原始单位，单位/层映射尚未实板校准；`context` 不包含完整连接关系。

`schematic --save` 使用固定只读 API 归档原理图网表、哈希、工程/焦点身份和范围限制。`inspect` 按 `--designator`、`--component-id` 或 `--net` 查询关联元件及引脚网络。`--depth 1–3` 控制扩展轮数，`--limit 1–500` 限制返回元件数，截断显式标记。网表元件键不承诺等同画布图元 ID。详见 [连接查询](docs/connectivity.md)。

`drawing-components` 提供当前页画布器件 ID。`drawing-pins --primitive-id '<画布ID>'` 固定只读查询该器件的引脚 ID、编号、名称、原始坐标、方向、长度和未连接标记，并归档目标身份与完整源。最多返回 500 个引脚，报告总数和截断；读不到器件/引脚、字段不兼容或读取期间编辑时失败。引脚坐标不表示网络连接，网表键不可直接代作画布 ID。2026-11-13 已在测试页验证 U1/R1/R2 共 20 个引脚；详见[实机验证记录](docs/live-api-verification.md)。

`new-task` 默认离线创建初稿；`--live` 会读取客户端，`--context <JSON>` 可导入之前的上下文。任务档案生成到项目旁的 `easyeda-设计助手-data/`；可设置绝对路径 `EASYEDA_ASSIST_DATA_DIR`。`handoff` 冻结当前记录并生成 SHA256 清单，作为设计意图输入交给审核项目；引用的外部资料尚不自动复制，审核方仍需补齐版本与原始证据。

## 交互模式、分析尺度与工程问题

`--interaction advice|collaborative` 与 `--scope component|module|board|product` 分别控制交互意图和分析尺度；可用于 `workflow`、`new-task` 和 `new-project`。`workflow`/`new-task` 还可用 `--signal` 登记跨模块或跨板耦合，`--missing` 请求查证提示。旧项目六阶段保持兼容，个人研发默认通过 `workflow` 查看五步循环。

下面六种 `--mode` 继续作为工程问题类型，不承担绘图授权：

| 模式 | 目的 |
|---|---|
| `datasheet` | 按具体问题快速查阅原手册、参数、引脚或应用图 |
| `selection` | 引导 agent 查关联元件/网络并按工况比较选型 |
| `topology` | 引导 agent 结合现有连接提供局部绘制方案 |
| `schematic` | 检查当前原理图局部连接、保护与外围 |
| `pcb` | 给出布线、回流、铺铜和局部布局建议 |
| `local-check` | 对用户指定的修改范围做影响分析与核对 |

这些模式提供工作指引与任务组织；工程推理、计算和建议由 agent 完成。

## 保留手册并按需查询

先创建 `datasheet` 任务，再导入本地 PDF 或 UTF-8 文本：

```powershell
python -B src/design_assist.py new-task --mode datasheet --goal '核对目标器件工作条件和外围要求'
python -B src/design_assist.py import-datasheet '<任务.json>' '<手册.pdf>' --mpn '<完整型号>' --manufacturer '<厂商>' --revision '<修订>'
python -B src/design_assist.py datasheets '<导入后任务.json>'
python -B src/design_assist.py datasheet-search '<访问清单 datasheet.json>' --query 'recommended operating'
python -B src/design_assist.py datasheet-page '<访问清单 datasheet.json>' --page 5
```

PDF 导入需可选依赖 `python -m pip install 'pypdf>=5,<7'`；纯文本导入仅使用标准库。含多个所选元件时用 `--primitive-id` 定位。导入生成新任务版本，保留原件、SHA256 和逐页搜索索引。扫描 PDF 保留原页访问，目前不做 OCR。

搜索按关键词返回页码及邻近文字；需结合完整页、型号和工况，必要时查看原图。导入不预生成分类摘录或参数结论。CAD 库中的手册链接可供 agent 的网页/PDF 工具访问，CLI 本身不联网下载。详见 [手册访问流程](docs/datasheets.md)。

## 当前进展

- 2026-11-18 完成 v4 非空白页实机增量实验和跨请求器件身份补齐修复；每步严格保护原对象，最终比对完整旧网表与详细 DRC。两电阻样本因新增 DRC 严格失败，清理后修订为三电阻闭环并完成 9 步验收，251 项回归全通过，历史失败结果和锁保留；见[增量编辑契约与证据](docs/incremental-editing.md)。
- 2026-11-17 完成历史 `zIndex` 残留恢复，经典属性后端的 U2 Name 与 C3 Value 往返及 C3 事先声明的确认实验，230 项回归全通过。完整源摘要、所有源记录、网表字节和详细 DRC 回到最初基线，无残留差异；原失败结果和四份历史锁保留。用法及实测边界见[修复与实验记录](docs/source-experiment.md)。
- 2026-11-17 在获明确授权的当前实验工程完成 U2 Name 单次写入、现场复核与独立恢复。新增单次实验日志、历史锁清单绑定和完整状态复核，212 项测试全通过；正向 `zIndex` 副作用使严格源验收失败，恢复后 Name/完整网表/详细 DRC 回到原基线但保留该源差异。正式 Source 调度保持关闭，见[实测记录](docs/source-validation.md)。
- 2026-11-17 前序迭代增加离线 Source 单属性候选、原始基线归档及证据核对；真实只读准备和旧数据负例通过，当时完整回归 199 项全通过。后续实测见上一条。
- 2026-11-17 完成独立网表/详细 DRC 比较、原始字节归档与结果重算、执行反馈和显式依赖复核清单；187 项测试全通过。客户端 4.1.100 两次只读实测获得一致连接及 6 条未变化的详细 DRC 警告，刷新证据仍未知；见[差分与反馈](docs/audit-and-feedback.md)。
- 2026-11-17 完成首期模式/范围、统一目录/查询、任务与决策来源追踪、多板显式端点记录和关闭状态下的 Source 属性适配器；v1/v2 计划认可语义分别保留，见[首期构建](docs/workflow-build.md)。
- 确立设计期建议与独立审核的项目边界。
- 提供固定读取与受限原理图新增操作的 API bridge 客户端。
- 提供任务记录格式和建议完整性检查。
- 已将所选图元的有限属性接入版本化上下文，并保留缺失与错误信息。
- 已提供六类任务的 agent 工作指引，新任务自动携带对应方法。
- 已支持原理图网表采集与局部连接查询；2026-11-13 实测导出 77 个元件，按 LED1 查询返回 4 个网络和 23 个元件（含目标），未截断。
- 已支持本地手册原件访问、按需搜索、完整页读取及可选原页渲染。
- 已提供项目级离线档案、六阶段指引、记录审查、显式依赖影响传播与修订归档；阶段是否通过和工程是否正确仍需人工/专项核验。
- 已实现“明确绘制请求 → 方案 → 用户认可 → 方案范围内写入”的受限执行链路；2026-11-14 v2 单器件创建及独立回查通过。首次 R4 的旧执行结果仍保留为 uncertain，其他参数组合及连线写入仍待验证。
- v3 已补充库归档/引脚几何预检、器件/标号/导线新增、旧图元保护及最终网表/DRC 核对；模拟测试覆盖成功、断连、DRC 警告、超时及日志写入失败，真实电路写入仍待验证。
- 2026-11-13 在辅助画图测试工程实测完整源、61 个器件图元、31 个网表元件、库搜索/详情及 20 个引脚；只读查询前后规范化源一致。新增 `drawing-pins`，90 项回归测试通过。次日获批放置测试新增 R4；原有图元与完整网表未变，测试器件与失败记录保留。
- 当前尚无 CLI 联网下载、OCR、实时库存或 UI 扩展。PCB 受限绘制已有4.1.100局部工具链实测，通用整板及真实电气性能验收仍待完成。工作指引不保证工程正确性，建议仍需证据与验证。

架构和流程见 [docs/architecture.md](docs/architecture.md) 与 [docs/workflow.md](docs/workflow.md)。

无客户端也可验证任务流程（输入为明确标注的虚构样例）：

```powershell
python -B src/design_assist.py new-task --mode schematic --context design/context.example.json --goal '演示稳压器局部检查任务'
```

源码直接运行使用 Python 3.11+，无第三方运行依赖。`pyproject.toml` 提供包元数据和预留命令入口；当前推荐源码运行，输出路径按源码项目位置计算。

运行基础测试：

```powershell
python -B -m unittest discover -s src -p 'test_*.py'
```

查询脚本集成测试需要 Node.js，通过模拟 API 执行实际 JavaScript；未安装 Node.js 时这组测试会跳过。Python CLI 本身不依赖 Node.js。
