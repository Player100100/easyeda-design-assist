# 手册访问与快速查询

手册能力服务于绘制者的即时问题：保留整本原件，让 agent 随时查询参数、引脚、条件、应用图或布局要求。导入只建立可追溯访问与搜索索引，不生成分类摘录报告或自动认定参数。

## 获取与登记

先从当前元件属性或网表 props.Datasheet 找到链接，核对完整制造商型号、封装后缀与修订。agent 可使用自身网页/PDF 工具访问原件；CLI 只导入本地文件，不自动联网下载。没有链接时优先寻找厂商来源，供应商库信息只作线索。

```powershell
python -B src/design_assist.py new-task --mode datasheet --goal '查询目标器件的启动条件'
python -B src/design_assist.py import-datasheet '<任务.json>' '<手册.pdf>' --mpn '<完整型号>' --revision '<修订>'
python -B src/design_assist.py datasheets '<导入后任务.json>'
```

唯一已采集元件可提供 ManufacturerId，多元件需 --primitive-id；型号缺失时用 --mpn 明确声明，不能从 Name 或供应商编号猜测。声明与 CAD 型号不一致会拒绝。型号和修订的适用性仍须对照原件。

导入创建新任务版本，保留父任务、原上下文、手册原件、SHA256、逐页文字索引及访问清单。datasheets 输出的 report_path 是 evidence/datasheet.json 清单（名称保留以兼容已有任务），并非摘录报告。新清单为 v2；旧 v1 手册仍可查询，访问时从原件重建索引。

## 围绕问题访问

```powershell
python -B src/design_assist.py datasheet-search '<datasheet.json>' --query 'startup' --limit 10
python -B src/design_assist.py datasheet-page '<datasheet.json>' --page 5
python -B src/design_assist.py datasheet-page '<datasheet.json>' --page 5 --render
```

搜索是大小写无关的逐行字面匹配，返回物理页序、行号与邻近文字，不解释工程含义。跨行词组、扫描页、图中文字和复杂排版可能无法命中，应换关键词、查目录或直接看原页。total_matches 是匹配行数；truncated 表示返回数量受限。

datasheet-page 返回完整页提取文字；--render 额外将 PDF 原页渲染为 PNG，并返回外部路径供 agent 的图像工具查看。扫描 PDF 即使无可提取文字也保留原件和原页渲染能力。表格、脚注、电路图、上下标及跨页内容应查看原图或相邻页，不能只读搜索片段。页码为文件物理页序，不等同印刷页码。

PDF 导入需可选 pypdf；渲染需可选 PyMuPDF。UTF-8 .txt/.md 仅用标准库，换页符分隔页面。限制为 50 MiB、500 页、500 万提取字符；加密 PDF 拒绝导入，不做 OCR。每次查询校验原件哈希，v2 另校验索引哈希。

## 回答与引用

围绕当前问题给出结论，写清具体型号、修订、物理页序、表格/章节和适用工况。区分典型值、保证值、绝对最大额定值和推荐范围。查不到时说明未确定，不用常识补齐。

简单查询可以直接答复并引用原文。需要保存复杂设计建议时，在 task.sources 中登记实际核对过的页面或计算来源。导入来源默认 candidate；核对后写 reviewed 和 review_record，但不因导入或文字命中就把整本手册视为已核对。建议仍应保留假设、风险、适用条件和验证动作。handoff 仅冻结任务 JSON 与哈希，引用的原件尚需另行交接。
