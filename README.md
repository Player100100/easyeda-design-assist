# EasyEDA Design Assist

中文名：EasyEDA 设计助手。GitHub 仓库名：`easyeda-design-assist`。本目录为独立 Git 仓库，源码、文档和测试独立维护。

**定位**：为 agent 提供辅助绘板的工程思路，以及通过嘉立创 API 和原始手册获取信息的工具。绘制者提出问题后，由 agent 基于真实元件、网络与工况给出建议。

## 目标

解决 agent 辅助绘板时方法不足、缺少工程依据的问题。手册功能保留原件访问与快速查询能力；选型和拓扑功能指导 agent 查阅关联元件与网络，提出可操作的绘制方案。与 `epro2-辅助绘制及审核` 配合：本项目服务设计过程，审核项目负责冻结版本的独立核验。

从 [Agent 指引](docs/agent-guide.md) 开始；[选型方法](docs/playbooks/selection.md) 与 [拓扑方法](docs/playbooks/topology.md) 给出具体步骤。新任务的 brief 自动附上对应指引。

## 快速开始

在项目目录运行：

```powershell
python -B src/design_assist.py connect
python -B src/design_assist.py status
python -B src/design_assist.py context
python -B src/design_assist.py guide --mode selection
python -B src/design_assist.py schematic --save
python -B src/design_assist.py inspect '<连接快照.json>' --designator U1 --depth 1
python -B src/design_assist.py new-task --mode schematic --goal '核对当前稳压器外围并指出缺失约束'
python -B src/design_assist.py new-task --mode pcb --live --goal '分析所选网络的回流与铺铜条件'
python -B src/design_assist.py validate-task '<任务档案路径>'
python -B src/design_assist.py handoff '<任务档案路径>'
```

Windows 可直接双击项目根目录的 `start-api.cmd`。脚本检查 Python、复用已有 bridge；需要启动时读取已安装的 `easyeda-api-skill`，检查 Node.js 18+ 和 `ws` 依赖，并在后台启动 bridge。关闭启动窗口后 bridge 继续运行。默认等待客户端 60 秒；未连接时按提示在 EDA 的 **API Gateway → 重新连接** 操作。脚本不能代替客户端安装/启用扩展。

命令行可运行 `start-api.cmd --timeout 0` 只保证 bridge 运行，或 `python -B src/design_assist.py connect --skill-dir '<技能目录>'` 指定位置；也支持 `EASYEDA_SKILL_DIR` 环境变量。默认依次查找用户目录 `.claude/skills/easyeda-api-skill`、`.codex/skills/easyeda-api-skill`。新启动日志写入外部 data 目录的 `runtime/`，每次连接状态保存到 `connections/`。多窗口时传 `--window-id`，不会改变全局活动窗口。自动化调用批处理可设置 `EASYEDA_ASSIST_NO_PAUSE=1`。退出码：0 为成功（`--timeout 0` 时仅表示 bridge 就绪），2 为运行错误，3 为客户端等待超时或需要选择窗口。

`context` 只调用固定的只读 API：当前工程、焦点文档、所选图元 ID 和按类型允许读取的属性。原理图支持元件与导线，PCB 支持元件、直线、过孔和铺铜；元件属性包括位号、制造商型号、供应商编号及自定义属性等。需要嘉立创EDA客户端及 API Gateway bridge 已连接；它不自动启动 bridge。多个窗口时用 `--window-id` 明确指定。`context --save` 会保存上下文并输出路径。

图元详情接口为 beta，每次最多读取 100 个对象的属性，完整 ID 列表保留。上下文 v2 分别记录详情不可用、ID 不一致、截断、字段缺失与读取报错；旧版 v1 上下文仍可导入。字段保留 API 原名和原始单位，单位/层映射尚未实板校准；`context` 不包含完整连接关系。

`schematic --save` 使用固定只读 API 归档原理图网表、哈希、工程/焦点身份和范围限制。`inspect` 按 `--designator`、`--component-id` 或 `--net` 查询关联元件及引脚网络。`--depth 1–3` 控制扩展轮数，`--limit 1–500` 限制返回元件数，截断显式标记。网表元件键不承诺等同画布图元 ID。详见 [连接查询](docs/connectivity.md)。

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
- 提供固定操作集的只读 API bridge 客户端。
- 提供任务记录格式和建议完整性检查。
- 已将所选图元的有限属性接入版本化上下文，并保留缺失与错误信息。
- 已提供六类任务的 agent 工作指引，新任务自动携带对应方法。
- 已支持原理图网表采集与局部连接查询；2026-11-13 实测导出 77 个元件，按 LED1 查询返回 4 个网络和 23 个元件（含目标），未截断。
- 已支持本地手册原件访问、按需搜索、完整页读取及可选原页渲染。
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
