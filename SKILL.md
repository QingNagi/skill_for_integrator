---
name: doc-integrator
description: Use when filling a Word template from specs, scanned PDFs or test data with source citations. 按批注摘抄、填表，可离线识别中英文扫描页。Do not use for standalone summaries.
metadata:
  author: QingNagi
  tags: "word, document-integration, source-tracing, verification-records"
---

# 文档整合助手

## Purpose

从规范、任务书或测试数据中摘取内容，按批注、章节或记录行填入 Word 模板。交付可溯源的草稿和审核报告，用户确认后生成终稿。原文件只读，默认输出到发起任务时当前工作目录下的 `整合输出/`。

## Prerequisites

下文的“技能目录”指本次加载的 `SKILL.md` 所在目录。用户指定了项目内的副本时，使用该副本。将表中的入口解析为实际路径，后续调用复用它，并保持发起任务时的工作目录，以便正确定位输入和输出。

| 运行环境 | 执行下文的 DI |
|---|---|
| Windows 项目离线包，PowerShell | `& "<技能目录>/run.ps1" <子命令> ...` |
| Windows Git Bash 或 GNU/Linux Bash 离线包 | `bash "<技能目录>/run.sh" <子命令> ...` |
| 已展开的运行环境 | 用技能目录内的 `runtime/python/python.exe`（Windows）或 `runtime/python/bin/python3`（Linux）执行同一目录下的 `scripts/di.py`。 |
| 纯源码 | 用本机 Python 3.8+ 执行技能目录内的 `scripts/di.py`。 |

输入路径和部署方式明确时，第一条工具调用直接执行任务。若需判断入口，在同一次 shell 调用中检查同级启动脚本并执行对应命令。离线包会自行完成首次部署，同版已就绪即执行原命令。`doctor` 只在用户要求检查或遇到运行故障时使用。模板和来源需可读，输出目录需可写。旧格式转换与 DOC/PDF 导出使用本机的办公软件。[依赖说明](vendor/README.md)

## Available Scripts

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/di.py` | 检查环境，或单独验证已有 DOCX/XLSX 的结构 | `doctor` 或 `doctor --selfcheck 文件` |
| `scripts/di.py` | 新任务出草稿或先看规划 | `run --draft -t 目标 -s 来源 --task 用户原话`。只要规划时省略 `--draft`。多来源重复 `-s` |
| `scripts/di.py` | 核对指定来源、记录回答、交付文稿 | `show 任务目录 引用`、`answer 任务目录 Q编号=选项号`、`draft/review/final 任务目录`（每次选一个子命令） |

文件参数相对于当前工作目录解析。Python 工具宿主可用 `from subprocess import run as run_script` 并以 `run_script(argv, check=True)` 调用同一入口。

## Instructions

1. 路径齐全时直接用用户指定的模板和来源。只给文件名时，在指定输入目录一次运行 `find 文件名 --exact --dir 目录`，多个名称可一并传入。若同名文件或批次仍不唯一，给出候选并等用户选择。只有用户指定时才采用成品示例。
2. **新任务交付草稿**时，执行一次 `run --draft -t 模板 -s 来源 --task 用户原话`。定位到可填写的位置和来源、且没有待答题时，脚本写入带批注的草稿并做结构检查。若未找到对应位置，请用户指明后在原任务内继续。用户只要求看方案时用普通 `run`。
3. **按命令结果继续。** 显示“草稿已生成”且结构检查通过时，按本次输出直接交付草稿、源文件快照和审核报告的路径，并逐项报告未填、原有值本批未核、未读取数量。用户只问交付位置及数量时，三个路径和三项计数就是完整答案。结构自检严重项为 0、只含已说明的 Office 版本兼容提示时，转达提示并结束工具调用。逐条审核明细按需读取报告，路径缺失时再列目录。来源映射有疑问、实测数据有风险或涉及扫描页时，用一次 `show 任务目录 引用1 引用2 --full` 核对相关引用，例如 `S1:page1`。来源查不到或规划与要求不符时再用 `outline/search` 和[手工操作](reference/手工操作.md)。草稿交给用户打开批注审核。
4. **有待确认问题**时，先呈现实际题目和选项，每批最多四题。Claude Code 需要表单时用 `questions 任务目录 --ask-json`，回答按选项描述里的原编号记录。批次、样品号、取值冲突或来源缺失由用户决定。保留超限实测值和警告，0/1 标志不作测量值。用 `answer` 记录实际选择并核对新问题，答完后运行 `draft 任务目录`。文件换版须重新确认。只有明确要求按推荐出草稿时才用 `draft --defaults`，只有要求记住规则才用 `answer --remember`。[规则与提问](reference/规则与提问.md)
5. 有修改意见时用 `review` 读取，按意见调整后重新 `draft`。新草稿会尝试并入上版的直接修改和批注；核对合并摘要和冲突，向用户说明未能带入的修改。只有用户明确要舍弃修改时才使用 `--discard-edits`。
6. 用户确认后运行 `final`。有审核人批注时由用户选择 `--reviewer-comments keep` 或 `drop`。若终稿被拦，按 `终稿拦截清单.md` 的类别、数量说明问题。补答或更正，必要时重新出草稿；正式选择与默认草稿不同时须重新生成。只有用户明确接受列出的遗留项才用 `--accept-unverified`，将 `终稿/终稿遗留项.md` 留作内部记录。核实实际生成的文件，转达脚本对个人信息、外链、隐藏文字等内容的提示。

## Examples

用户给全了两个 Word 路径，要求“按报告批注摘入任务书的进度要求，先给我审核”。把 `DI` 替换为上表对应入口，在用户当前工作目录中执行：

```sh
DI run --draft -t "报告.docx" -s "任务书.docx" --task "按报告批注摘入任务书的进度要求，先给我审核"
```

例如命令报告结构检查通过且未填、原有值未核、未读取均为 0，使用命令给出的三个路径和三个 0 直接回给用户。若停在待答题，向用户展示题目，收到回答后继续。

“把规范电特性填进记录，再从 B.xlsx 取中间电压的范围值”需要两次 `-s`，核对测试条件、样品号、单位和范围算法。详细示范见 [摘抄与填表](reference/示例.md)。多个模板用 `batch init` 配对来源，逐任务核对批次；排版用 `profile`。参数及批量示例见 [命令参考](reference/命令参考.md)。

## Limitations

- 草稿、终稿的原生目标为 DOCX。旧版 Word 模板须能在本机转换为 DOCX。Excel 工作簿可作数据来源，CLI 目前无法写入 Excel 目标。
- PDF 表格、图片提取随本地后端变化。完整套装会对纯扫描页及命中检测条件的图文混排页执行本地中英文 OCR。按原页复核识别片段，尤其是数值、单位、型号和表格行列，再记录用户确认。未读取项须标出页码。Word 内嵌位图、独立图片和手写表格没有自动结构化识别保证。批注跳转效果也取决于阅读器。
- `profile` 控制摘抄章节和题注的排版。填写已有表格时沿用模板单元格格式。

## Troubleshooting

- **File or format error** — 对照 `doctor` 检查转换后端；没有可用办公软件时，请用户提供另存的 DOCX/XLSX。离线 PDF 优先使用可用的 PyMuPDF，缺少时可用已安装的 pdfplumber 或 pypdf 提取内容。降级为纯文本后，逐项核对表格和图形来源。
- **Validation failed** — 用 `doctor --selfcheck 文件` 定位结构问题。保存并关闭仍有锁文件的草稿后重试；有未核问题时按审核报告或终稿拦截清单处理。以工具列出的实际输出路径判断哪份草稿、终稿或额外格式已生成。
