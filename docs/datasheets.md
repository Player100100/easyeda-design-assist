# 手册原文与候选摘录

`import-datasheet` 把本地原件接入一个现有任务，并输出新的任务版本。旧任务与旧上下文不会覆盖。该步骤完成原文归档与定位，不把关键词搜索当作参数理解。

## 用法

```powershell
python -B src/design_assist.py new-task --mode datasheet --live --goal '核对所选器件的供电、外围和布局要求'
python -B src/design_assist.py import-datasheet '<任务.json>' '<手册.pdf>' --revision 'Rev A'
```

只有一个已采集元件时可读取它的 `ManufacturerId`。有多个元件时必须传 `--primitive-id`。缺少制造商型号时传 `--mpn`，不能把供应商编号或 `Name` 当成完整 MPN。传入型号与 CAD 制造商型号冲突时拒绝导入。型号与修订只是声明，仍需人工检查文档是否覆盖该型号及封装后缀。

支持 `.pdf`、UTF-8 `.txt`、`.md`。PDF 用可选 `pypdf` 依赖，文本页由换页符 `\f` 分隔。PDF 页码是物理页序，不是印刷页码；行号基于已提取文本。加密 PDF、不含可提取文字的扫描件、超出 50 MiB/500 页/500 万字符的文件会被拒绝。混合扫描 PDF 的空白文字页会记录限制；图、表格、上下标、跨页条件需要对照原页。

## 输出

- 新 `task.json`：状态回到 `analyzing`，加入 `parent_task`、`datasheets`、`sources` 和手册人工核对缺项；原有建议与决定保留，不生成新的工程结论。
- `parent-task.json`：导入前任务记录副本。
- `initial-context.json`：本次任务的 CAD 上下文。
- `evidence/datasheet.pdf`（或 txt/md）：归档原件；SHA256 存在来源与报告中。
- `evidence/pages.json`：全部提取页文本。
- `evidence/datasheet.json`：声明的器件身份、文档修订、提取方式、覆盖统计、页码/行号摘录及限制。
- `brief.md`：供人工或 agent 核对的分类原文段落。

每类至多保留 20 个候选段落，超出时标记截断；逐页完整文本仍保留。某类没有命中不意味着文档没有该要求。绝对最大值与推荐工作条件分别检索，原文里的否定、脚注及工况不得忽略；不自动转换单位或把极限值当作设计值。

## 从摘录到建议

1. 对照归档原件，确认完整型号、厂商、版本和页码，并检查表格列、脚注、封装适用性。
2. 在任务中补充电压、电流、温度、接口/负载等工况；初始 `missing_inputs` 不会因导入自动消失。
3. 核对过的来源可设置 `review_status: reviewed`，并写入非空 `review_record`，记录核对者、时间、原页与结论。默认 `candidate` 来源只能作为待核对依据，不能支撑 `verified` 建议。
4. 建议的 `evidence` 引用摘录 `source_id`，同时记录适用条件、风险与验证动作。来源经过核对仍不等于工程建议已验证。
5. 运行 `validate-task` 后按原工作流交接。当前 `handoff` 冻结 JSON 和校验和，尚不复制外部证据文件；交接时应另外提供相关任务的 `evidence/` 与其原件哈希。

联网检索、OCR、自动读表、参数标准化、器件比较以及工程规则推理属于后续阶段。
