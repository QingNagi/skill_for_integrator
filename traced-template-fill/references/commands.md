# 命令参考

`DI` 在 Windows 自部署包中表示 `& "<技能目录>/run.ps1"`，Linux 套装使用 `bash "<技能目录>/run.sh"`，展开后的技能可用内置 Python 执行 `scripts/di.py`；`<任务目录>` = run/new 输出的“已建立任务”路径。

| 命令 | 用途 | 常用参数 |
|---|---|---|
| `doctor` | 检查 Python、办公软件、PDF 库 | |
| `doctor --selfcheck 文件` | 单独对 DOCX 或 XLSX 做结构自检 | 不需要 Office |
| `doctor --prune` | 使用私有离线套装预览旧版运行时。保留当前、最近、已定制、使用中和无法验证的安装 | `--apply` 执行清理 |
| `find 关键词…` | 模糊查找当前工作区或指定目录；精确查找指定工作区的所有同名文件 | `find 测量表.xlsx --exact --dir 项目目录`，可指定 `--depth` |
| `run -t 目标 -s 来源 --task "要求"` | 建任务并规划。加 `--draft` 一并生成带来源批注的首稿和审核报告，遇到待答题即暂停。已产出的草稿路径、来源快照路径和核查数量见命令输出。 | `-s` 可重复，`--name`、`--out`、`--profile`、`--format` |
| `new …` | 只建任务不规划（参数同 run） | `--plan` |
| `outline <任务目录\|文件>` | 提纲 | `--src S1` `--section H5` `--depth 3` `--lines 80` |
| `show <任务目录> <引用> [更多引用…]` | 按给定次序回读并去重，例如 `show <任务目录> S1:H5 S1:H6 S2:page1`。PDF 页码从 1 起，按阅读顺序显示段落、表格及 OCR 标记。另支持 `S1:T3.R2`、`S1:P8`、`S1:F2`、`S2:Sheet1!A1:D20`。 | `--rows 0-10`（表格）、`--full`、`--rows-max 40`（工作表） |
| `search <任务目录> 关键词` | 检索源文件 | `--src S1` `--top 8` `--target` |
| `figure <任务目录> S1:F2` | 导出图片（EMF/WMF→PNG） | |
| `plan <任务目录>` | 重新规划 | |
| `questions <任务目录>` | 列出待确认问题 | `--ask-json` `--all` |
| `answer <任务目录> Q1=1 …` | 记录回答 | `--remember` |
| `set <任务目录> <位置ID> "值"` | 指定内容 | `--src S1:P5` `--note 说明` `--runs` |
| `add-section <任务目录> --to 标题 --src S1:H5` | 摘入源章节 | `--replace` `--title` `--no-subheading` |
| `add-op <任务目录> JSON\|@文件` | 高级手工操作 | |
| `draft <任务目录>` | 生成带批注草稿（v1、v2…），尝试合并上一版人工修改 | `--check` 用办公软件验证；`--defaults` 仅用于已获同意的默认草稿；`--discard-edits` 仅在用户要求舍弃上版人工修改时使用 |
| `review <任务目录>` | 读取审核人批注与修改 | |
| `final <任务目录>` | 生成终稿 | `--format doc,pdf,md` `--reviewer-comments drop\|keep` `--template-comments keep\|drop`（模板批注）；`--strip-template-comments` `--draft 指定草稿` `--accept-unverified`（带着未核项定稿，登记到 终稿遗留项.md；须用户明确同意）。终稿前会先做收件人视角检查，不通过则不出正式稿，清单见 终稿拦截清单.md |
| `status <任务目录>` | 任务状态 | |
| `profile` | 查看/生成排版规范 | `--text "要求"` `--save 名称` `--name 名称` |
| `convert 文件 格式` | 用本地办公软件转换，默认保存到当前目录下的 `整合输出/转换_…/` | `--out 输出文件路径` |
| `batch init/new/plan/answer/draft/final` | 批量 | 见下方“批量清单” |

## 批量清单

以下命令从数据目录运行，按实际技能位置替换 `DI`。检查 CSV 中每份模板的来源、批次和任务说明，再执行 `new`。后续命令中的 JSON 路径取自 `new` 输出。

```text
DI batch init --targets "记录/*.docx" --sources "规范.docx" --pair "数据/{stem}.xlsx" --task "按对应数据填入各自记录" --manifest "批量清单.csv"
DI batch new "批量清单.csv"
DI batch plan "<批量任务.json>"
```

`batch plan` 显示合并问题及所属任务。只有实际回答适用于所列的每份任务时才用 `batch answer "<批量任务.json>" B1=2` 等命令；按各任务分别核对来源或选项有差异时，用单任务的 `questions` 和 `answer`。收到实际回答后运行 `batch draft` 加批量任务 JSON。各草稿审核完成后，用同一 JSON 运行 `batch final`；查看每个任务的成功或失败信息。

## 排版

`DI profile` 查看当前规范。用 `DI profile --text "正文仿宋三号，首行缩进2字符" --save 公文` 保存用户指定的规则，核对识别结果后在 `run` 的完整命令中加 `--profile 公文`。新规范保存在工作区的 `.doc-integrator/profiles/`。内置资源为 `config/profiles/默认_文档摘抄提示词.json`、`config/rules/aliases.json`、`config/rules/learned.json`。

## 任务目录结构
```
整合输出/
  _缓存/                      解析缓存（按文件内容哈希，多任务共享）
  <目标名>_<时间>/
    job.json                  任务信息
    plan.json questions.json questions.md answers.json manual_ops.json
    审核报告.md                逐项来源/原始值/未填项/警告
    草稿/<目标名>_草稿_v1.docx  带批注草稿（v2、v3… 均保留）
    草稿/源文件快照/            源文件副本（含书签、黄色高亮），批注链接指向这里
    终稿/<目标名>_终稿.docx(.doc/.pdf/.md)
```

## 兼容性
- 读写核心只用 Python 标准库（3.10+），生成的 docx 采用 Word 自身的写法（批注、超链接、书签、表格），
  已用 Word 2010 验证可打开与导出；WPS、LibreOffice、Word 2013 及以上版本尚未实机验证，只是批注、超链接、书签的结构按 Word 标准写法生成；在这些软件里首次使用请先 `draft --check` 或人工打开一份草稿确认批注链接能跳转。
- 旧格式与导出：自动探测 MS Office（COM）→ WPS（KWps/KET COM）→ LibreOffice（soffice）；
  可用环境变量 `DI_OFFICE=word|wps|libreoffice` 指定。
- 控制台乱码：设置环境变量 `DI_UTF8=1` 强制 UTF-8 输出。

关键问题的 `answer` 和批量等价操作，以及 `--defaults`、`--discard-edits`、`--accept-unverified`、`--remember` 均使用 `--user-quote "用户原话"` 记录依据。每个任务写入自己的 `decisions.jsonl`。

`DI clean [--out 输出根]` 预览可清理的共享缓存；确认所有使用方已结束后加 `--apply`。快照与历史文稿仍保留。

## 自动化调用与审核表

在 `run --draft`、`answer --draft`、`draft` 和 `final` 后加 `--json`，标准输出只含一个 JSON 对象。成功为退出码 0。3 表示 `needs_answers`，直接展示 `questions` 中最多四道题，下一次调用可同时提交这批回答。4 表示 `blocked`，读取 `reason`。2 表示错误或部分导出失败。Python 调用用 `subprocess.run(args, check=False, capture_output=True, text=True)`，读完结果后再判断退出码。设置 `DI_TIMING=1` 可在标准错误中读取解析、规划、写入和校验各阶段的用时（秒）。

- `DI questions <任务> --csv 待核清单.csv` 导出当前任务的可选决定。复核人可编辑决定和备注，以 `DI answer <任务> --from-sheet 待核清单.csv --reviewer 姓名 --user-quote "审核原话" --draft --json` 导入。保留原始位置、题号、选项和版本标识。
- `DI answer <任务> Q3 --evidence 程序清单.xlsx --reviewer 姓名 --user-quote "审核原话" --draft --json` 可回答数据段归属、单位和工况。段序清单含“来源、标签、段序、测试号、参数、单位”。单位或工况清单含“来源、测试号、参数、单位”或“来源、测试号、参数、条件”。使用题目所指工作簿与工作表的来源标识。支持 CSV 和 XLSX。对于工作区中唯一的同名源工作簿且只有一张可见表，XLSX 表头可写成“测试名（读数.xlsx A 列标签）”。其他情形请显式提供“来源”与“标签”。从 1 开始填写段序，单个连续块内需要细分时，在“首行、末行”列给出相对行号，或在备注中写“第1~4行”。同一测试号可对应多个段。工况列登记实际施加的条件，额外的规范条件须单独核对。同一轮可写 `answer <任务> Q1=2 Q3 --evidence 程序清单.xlsx --user-quote "审核原话" --draft --json`。Q1 保存人工选项，Q3 单独关联清单哈希。
- 任务目录下的 `facts.json` 保存本次确认和来源。审核报告按位置引用事实。产品和程序版本可在测试规则中指定。用 `--remember` 明确确认过且适用范围和选项均匹配的决定可以跨任务复用。每一批都要核对批次身份。

### 扫描与图文混排 PDF

审核报告中的 OCR 行带原页裁切图，保存在 `草稿/源文件快照/OCR/`。识别引擎按词给出置信度。低于 80/100 的数字、单位、小数点和正负号会分别标记，复核时可点开对应原图。解析相同识别像素的工作共用本机 `_缓存/ocr/`。更换识别程序、模型、语言或分辨率会重新识别。

可用以下环境变量调节一个任务对混排区域的判断。重新运行解析时会使用新设置。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `DI_OCR_NATIVE_CHARS` | 15 | 原生字符数低于该值时按稀疏文本页处理 |
| `DI_OCR_SPARSE_MIN_AREA` | 0.04 | 稀疏文本页中触发 OCR 的图像最小面积占整页的比例 |
| `DI_OCR_MIXED_MIN_AREA` | 0.25 | 混排页正文区域内，图像触发核对的最小面积占整页的比例 |
| `DI_OCR_BODY_MARGIN` | 0.12 | 混排页顶部、底部分别排除的页高比例（0 到 0.49） |

原生文字和达到面积阈值的图像在正文区域重叠时，审核报告会登记该页供人工核查。

`batch draft 清单.json --jobs 2` 可让两项独立任务同时出草稿。清单有重复任务目录时会暂停。每个子任务产生独立 JSON 结果；若需要人工核对，会给出该任务的题目。

`doctor --prune` 预览同一私有运行时缓存中可回收的版本。按命令提示显式应用。使用中、缺少可核验安装记录、包含本地改动以及当前和最近的一个版本均保留。

任务目录中的 `decisions.jsonl` 在收到带原话的决定时生成，每行记一条操作。`questions.md` 随规划生成，列出完整选项。终稿会对照决定日志检查相应的答复。
