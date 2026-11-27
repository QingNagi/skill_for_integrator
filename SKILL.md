---
name: doc-integrator
description: Use when filling a Word template from specs, scanned PDFs or test data with source citations. 按批注摘抄、填表，可离线识别中英文扫描页。Do not use for standalone summaries.
metadata:
  author: QingNagi
  tags: "word, document-integration, source-tracing, verification-records"
---

# 文档整合助手

## Purpose

从规范、任务书或测试数据中摘取内容，按批注、章节或记录行填入 Word 模板。交付可溯源的草稿和审核报告，用户确认后生成终稿。原文件只读，默认输出到目标文件旁的 `整合输出/`。

## Prerequisites

先识别系统，再定位本 SKILL.md 所在目录，保留当前任务工作目录。把下文的 `DI` 替换成对应的执行入口，直接执行用户所需的第一个子命令。

| 当前能看到的文件 | 执行 DI 的入口 |
|---|---|
| SKILL.md 同级有 `run.ps1` | `& "<技能目录>/run.ps1" <子命令> ...`。此入口自动部署随包环境并继续原命令。以后仍用同一入口，同版本就绪即复用。 |
| Linux 套装与 SKILL.md 同级有 `run.sh` | `bash "<技能目录>/run.sh" <子命令> ...`。按 `uname -m` 选择 x86_64 或 aarch64 的 GNU/Linux 套装。 |
| 已展开的技能目录有 `runtime/python/python.exe` | `& "<技能目录>/runtime/python/python.exe" -B "<技能目录>/scripts/di.py" <子命令> ...` |
| 仅有源码且环境已有 Python 3.8+ | `python "<技能目录>/scripts/di.py" <子命令> ...`。内网新机应使用含运行时的完整套装。 |

首次部署由 Agent 执行。只要入口就绪就继续用户的原任务，无需单独启动安装器或每次跑安装验收。`doctor` 用于用户要求的环境检查或排查故障。目标模板、来源文件需可读，输出目录需可写。旧文件转换及 DOC/PDF 导出使用目标机已有的办公软件。[依赖说明](vendor/README.md)

## Available Scripts

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/di.py` | 检查环境，或单独验证已有 DOCX/XLSX 的结构 | `doctor` 或 `doctor --selfcheck 文件` |
| `scripts/di.py` | 建任务并生成映射方案 | `run -t 目标 -s 来源 --task 用户原话`，多来源重复 `-s` |
| `scripts/di.py` | 核对指定来源、记录回答、交付文稿 | `show 任务目录 引用`、`answer 任务目录 Q编号=选项号`、`draft/review/final 任务目录`（每次选一个子命令） |

文件参数相对于当前工作目录解析。任务有多个来源时，逐个传入文件路径。

## Instructions

宿主已有 Python 并需要以参数数组调用时，可直接使用标准库，例如 Linux 下的环境检查：

```python
from pathlib import Path
from subprocess import run as run_script

skill = Path(".agents/skills/doc-integrator").resolve()
run_script(["bash", str(skill / "run.sh"), "doctor"], check=True)
```

1. 确定要填写的目标和提供内容的来源。只给文件名时先运行 `find 文件名 --exact --dir 工作区`，只给片段时用 `find 关键词`。重名时列出候选的相对路径，等用户选定后再建任务；若已明确给了目录、批次并且唯一匹配，可直接使用该路径。成品示例只有用户指定才作为来源。将用户原话完整传给 `run --task`，保留电压条件、取值方法、版本号等限定词。记录返回的任务目录。
2. 对照规划摘要核对目标行、源章节、忽略项和已识别规则。用 `outline`、`search` 定位，再用 `show` 读回**每个选中的引用**。表格可分段读，标题命中、近似匹配或带警告的引用用 `show --full`。按 ID 让脚本复制原文和数值。区分空章节、跳过的占位文字与未读取的页。规划不符时按 [手工操作](reference/手工操作.md) 修正。
3. 将实际待确认问题和选项交给用户，每批最多四题。Claude Code 可用 `questions --ask-json` 生成表单，用选项描述里的原编号记录回答。其他环境显示任务目录内的 `questions.md`。来源所属批次、样品号无交集、取值方式冲突或字段无来源时，由用户决定。数字相近不能确认来源；保留超限实测值和警告，0/1 标志不作测量值。用 `answer` 记录用户的实际选择，重规划后核对新问题。文件换版重新确认，各任务的回答按版本保存。仅在用户要求记住对应规则时加 `--remember`。[规则与提问](reference/规则与提问.md)
4. 运行 `draft`，确认命令输出了草稿路径。只有用户同意按推荐出草稿时才用 `--defaults`，默认值只对本版草稿有效。交付 DOCX、来源快照位置和 `审核报告.md`，报出未填、原有值未核和未读项的实际数量。旧值保持原样并提示本批未核。请用户打开批注核对来源，修改或加批注后保存并关闭。
5. 有修改意见时用 `review` 读取，按意见调整后重新 `draft`。新草稿会尝试并入上版的直接修改和批注；核对合并摘要和冲突，向用户说明未能带入的修改。只有用户明确要舍弃修改时才使用 `--discard-edits`。
6. 用户确认后运行 `final`。有审核人批注时由用户选择 `--reviewer-comments keep` 或 `drop`。若终稿被拦，按 `终稿拦截清单.md` 的类别、数量说明问题。补答或更正，必要时重新出草稿；正式选择与默认草稿不同时须重新生成。只有用户明确接受列出的遗留项才用 `--accept-unverified`，将 `终稿/终稿遗留项.md` 留作内部记录。核实实际生成的文件，转达脚本对个人信息、外链、隐藏文字等内容的提示。

## Examples

用户说“按报告批注摘入任务书的进度要求，先给我审核”。保留项目工作目录，模板和来源路径换成用户提供的文件。

```sh
DI run -t "报告.docx" -s "任务书.docx" --task "按报告批注摘入任务书的进度要求，先给我审核"
```

例如输出的任务目录为 `整合输出/报告_示例`，规划引用为 `S1:H2`，核对及草稿命令如下。若有待答问题，收到用户回答后先运行 `answer`。请求只到草稿时，交付草稿即停。

```sh
DI show "整合输出/报告_示例" S1:H2 --full
DI questions "整合输出/报告_示例"
DI draft "整合输出/报告_示例"
```

“把规范电特性填进记录，再从 B.xlsx 取中间电压的范围值”需要两次 `-s`，核对测试条件、样品号、单位和范围算法。详细示范见 [摘抄与填表](reference/示例.md)。多个模板用 `batch init` 配对来源，逐任务核对批次；排版用 `profile`。参数及批量示例见 [命令参考](reference/命令参考.md)。

## Limitations

- 草稿、终稿的原生目标为 DOCX。旧版 Word 模板须能在本机转换为 DOCX。Excel 工作簿可作数据来源，CLI 目前无法写入 Excel 目标。
- PDF 表格、图片提取随本地后端变化。完整套装会对纯扫描页及命中检测条件的图文混排页执行本地中英文 OCR。按原页复核识别片段，尤其是数值、单位、型号和表格行列，再记录用户确认。未读取项须标出页码。Word 内嵌位图、独立图片和手写表格没有自动结构化识别保证。批注跳转效果也取决于阅读器。
- `profile` 控制摘抄章节和题注的排版。填写已有表格时沿用模板单元格格式。

## Troubleshooting

- **File or format error** — 对照 `doctor` 检查转换后端；没有可用办公软件时，请用户提供另存的 DOCX/XLSX。离线 PDF 优先使用可用的 PyMuPDF，缺少时可用已安装的 pdfplumber 或 pypdf 提取内容。降级为纯文本后，逐项核对表格和图形来源。
- **Validation failed** — 用 `doctor --selfcheck 文件` 定位结构问题。保存并关闭仍有锁文件的草稿后重试；有未核问题时按审核报告或终稿拦截清单处理。以工具列出的实际输出路径判断哪份草稿、终稿或额外格式已生成。
