# EasyEDA Design Assist

中文名：EasyEDA 设计助手。GitHub 仓库名：`easyeda-design-assist`。本目录为独立 Git 仓库，源码、文档和测试独立维护。

**定位**：为 agent 提供辅助绘板的工程思路，以及通过嘉立创 API 和原始手册获取信息的工具。绘制者提出问题后，由 agent 基于真实元件、网络与工况给出建议。

## 目标

解决 agent 辅助绘板时方法不足、缺少工程依据的问题。手册功能保留原件访问与快速查询能力；选型和拓扑功能指导 agent 查阅关联元件与网络，提出可操作的绘制方案。与 `epro2-辅助绘制及审核` 配合：本项目服务设计过程，审核项目负责冻结版本的独立核验。

从 [Agent 指引](docs/agent-guide.md) 开始；[选型方法](docs/playbooks/selection.md) 与 [拓扑方法](docs/playbooks/topology.md) 给出具体步骤。新任务的 brief 自动附上对应指引。

项目级设计框架提供离线档案、六阶段工作指引、记录缺项审查、显式依赖影响分析和可追溯修订归档：`new-project`、`project-guide`、`project-review`、`project-impact`、`project-revise`、`validate-project`。参见[项目设计方案](docs/project-design.md)与[阶段指引](docs/project-stages.md)。它不自动判定阶段通过或修改 CAD。

自动绘图触发规则：明确提出辅助绘制后，先形成可审阅的操作方案；用户认可方案后，允许在方案范围内执行，不为每一步重复询问。目标文档或方案范围变化时暂停并更新方案。新项目默认 `explicit_batch`，要求方案认可、操作范围和目标快照约束。原理图器件放置执行器 v2 已实现工程 DEVICE 来源映射、API/画布角度换算和完整引脚回读检查，通过单次实机创建 R5 的独立回查（请求90°/画布270°，不镜像，BOM/PCB关闭）；其余组合尚未实测，首次 R4 的旧执行结果仍为 uncertain。

实验性 v3 执行器补充了空白原理图上的库器件导入、网络标号、正交多分支导线，以及最终逐引脚网表和详细 DRC 核对。每步检查完整源差异，部分完成、回读未知或验证失败时保留现场与目标锁。v3 已有模拟流程与固定脚本测试，**尚未完成真实 CAD 写入验证**，不支持已有电路增量编辑、属性修改或 PCB 写入。使用方法见[电路执行器契约](docs/circuit-executor.md)、[自动绘图策略](docs/autodraw-policy.md)与[实测记录](docs/live-api-verification.md)。

## 快速开始

在项目目录运行：

```powershell
python -B src/design_assist.py connect
python -B src/design_assist.py status
python -B src/design_assist.py context
python -B src/design_assist.py guide --mode selection
python -B src/design_assist.py schematic --save
python -B src/design_assist.py drawing-components
python -B src/design_assist.py drawing-project-info
python -B src/design_assist.py drawing-pins --primitive-id '<画布器件图元ID>'
python -B src/design_assist.py inspect '<连接快照.json>' --designator U1 --depth 1
python -B src/design_assist.py new-task --mode schematic --goal '核对当前稳压器外围并指出缺失约束'
python -B src/design_assist.py new-task --mode pcb --live --goal '分析所选网络的回流与铺铜条件'
python -B src/design_assist.py validate-task '<任务档案路径>'
python -B src/design_assist.py new-drawing-plan '<项目档案路径>' --request '为接口增加保护电路并列出连接方案'
python -B src/design_assist.py review-drawing-plan '<项目档案路径>' '<绘图计划路径>'
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

## 模式

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
- 当前尚无 CLI 联网下载、OCR、实时库存、完整 PCB 几何采集或 UI 扩展。工作指引不保证工程正确性，建议仍需证据与验证。

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
