# EasyEDA Design Assist

中文名：EasyEDA 设计助手。GitHub 仓库名：`easyeda-design-assist`。本目录为独立 Git 仓库，源码、文档和测试独立维护。

**状态**：已实现一键 API 连接、只读上下文、所选图元属性采集、手册原文导入与页码摘录候选、任务档案及建议字段校验。自动参数解释、设计建议引擎、PCB 几何分析尚未实现。

## 目标

在用户绘制嘉立创EDA原理图与 PCB 时提供结合上下文的专业建议，并记录建议依据、适用条件和用户决定。与 `epro2-辅助绘制及审核` 配合：本项目服务于设计过程，审核项目负责冻结版本的独立核验。

## 快速开始

在项目目录运行：

```powershell
python -B src/design_assist.py connect
python -B src/design_assist.py status
python -B src/design_assist.py context
python -B src/design_assist.py new-task --mode schematic --goal '核对当前稳压器外围并指出缺失约束'
python -B src/design_assist.py new-task --mode pcb --live --goal '分析所选网络的回流与铺铜条件'
python -B src/design_assist.py validate-task '<任务档案路径>'
python -B src/design_assist.py handoff '<任务档案路径>'
```

Windows 可直接双击项目根目录的 `start-api.cmd`。脚本检查 Python、复用已有 bridge；需要启动时读取已安装的 `easyeda-api-skill`，检查 Node.js 18+ 和 `ws` 依赖，并在后台启动 bridge。关闭启动窗口后 bridge 继续运行。默认等待客户端 60 秒；未连接时按提示在 EDA 的 **API Gateway → 重新连接** 操作。脚本不能代替客户端安装/启用扩展。

命令行可运行 `start-api.cmd --timeout 0` 只保证 bridge 运行，或 `python -B src/design_assist.py connect --skill-dir '<技能目录>'` 指定位置；也支持 `EASYEDA_SKILL_DIR` 环境变量。默认依次查找用户目录 `.claude/skills/easyeda-api-skill`、`.codex/skills/easyeda-api-skill`。新启动日志写入外部 data 目录的 `runtime/`，每次连接状态保存到 `connections/`。多窗口时传 `--window-id`，不会改变全局活动窗口。自动化调用批处理可设置 `EASYEDA_ASSIST_NO_PAUSE=1`。退出码：0 为成功（`--timeout 0` 时仅表示 bridge 就绪），2 为运行错误，3 为客户端等待超时或需要选择窗口。

`context` 只调用固定的只读 API：当前工程、焦点文档、所选图元 ID 和按类型允许读取的属性。原理图支持元件与导线，PCB 支持元件、直线、过孔和铺铜；元件属性包括位号、制造商型号、供应商编号及自定义属性等。需要嘉立创EDA客户端及 API Gateway bridge 已连接；它不自动启动 bridge。多个窗口时用 `--window-id` 明确指定。`context --save` 会保存上下文并输出路径。

图元详情接口为 beta，每次最多读取 100 个对象的属性，完整 ID 列表保留。上下文 v2 分别记录详情不可用、ID 不一致、截断、字段缺失与读取报错；旧版 v1 上下文仍可导入。字段保留 API 原名和原始单位，单位/层映射尚未实板校准；采集结果不包含完整连接关系。已验证真实原理图页的空选择采集；选中图元详情和 PCB 仍需实板联调。

`new-task` 默认离线创建初稿；`--live` 会读取客户端，`--context <JSON>` 可导入之前的上下文。任务档案生成到项目旁的 `easyeda-设计助手-data/`；可设置绝对路径 `EASYEDA_ASSIST_DATA_DIR`。`handoff` 冻结当前记录并生成 SHA256 清单，作为设计意图输入交给审核项目；引用的外部资料尚不自动复制，审核方仍需补齐版本与原始证据。

## 模式

| 模式 | 目的 |
|---|---|
| `datasheet` | 提取关键器件参数、引脚限制、推荐外围和布局要求 |
| `selection` | 基于工况与约束比较器件和替代方案 |
| `topology` | 提出局部功能拓扑及取舍 |
| `schematic` | 检查当前原理图局部连接、保护与外围 |
| `pcb` | 给出布线、回流、铺铜和局部布局建议 |
| `local-check` | 对用户指定的修改范围做影响分析与核对 |

这些模式目前是任务档案类型，不表示对应的分析能力已经自动实现。

## 导入单器件手册

先创建 `datasheet` 任务，再导入本地 PDF 或 UTF-8 文本：

```powershell
python -B src/design_assist.py new-task --mode datasheet --goal '核对目标器件工作条件和外围要求'
python -B src/design_assist.py import-datasheet '<任务.json>' '<手册.pdf>' --mpn '<完整型号>' --manufacturer '<厂商>' --revision '<修订>'
```

PDF 导入需可选依赖 `python -m pip install 'pypdf>=5,<7'`；纯文本导入仅使用标准库。含多个所选元件时用 `--primitive-id` 定位；唯一元件的 `ManufacturerId` 可提供型号，`Name` 不作型号推断。导入生成新的任务目录，保留父任务记录、手册原件与 SHA256、逐页文字及分类摘录。扫描版目前不做 OCR。

分类涵盖绝对最大值、推荐工作条件/电气特性、引脚、应用外围、保护和布局/散热。摘录保留页码和原文，均为 `candidate`；关键词可能命中目录或否定句，不会据此生成数值参数或工程结论。请对照原页核对型号、修订和表格；完成后可将相应 `sources` 的 `review_status` 改为 `reviewed` 并填写 `review_record`。详见 [手册工作流](docs/datasheets.md)。

## 当前进展

- 确立设计期建议与独立审核的项目边界。
- 提供固定操作集的只读 API bridge 客户端。
- 提供任务记录格式和建议完整性检查。
- 已将所选图元的有限属性接入版本化上下文，并保留缺失与错误信息。
- 已支持本地手册归档、逐页文字提取、分类摘录候选和任务来源引用。
- 尚未实现联网手册检索、参数自动解释、完整连接关系、器件选型数据库、建议引擎、PCB 几何推理或 UI 扩展。

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
