---
name: traced-template-fill
description: "Use when filling or reviewing an existing Word template from specs, spreadsheets or scans with source comments. 用于填验证记录、批量填表、按批注摘抄规范、修改草稿和审核定稿。"
compatibility: "Claude Code、Codex 及可执行本地命令的 Agent。源码需 Python 3.10+。离线运行时自带 Python、中英文 OCR 和 PDF 后端。旧格式转换及 PDF 导出使用本机 Office、WPS 或 LibreOffice。"
allowed-tools: ""
metadata:
  author: QingNagi
  version: "1.3.0"
  tags: "word, docx, template-filling, source-tracing, verification-records, ocr"
---

# 溯源填写助手

将用户提供的规范、任务书和实测数据填进已有 Word 模板。交付物是带逐项来源批注的草稿、来源快照与审核报告。用户确认后再定稿。

## Prerequisites · 调用入口

用当前宿主已加载的本文件所在目录定位入口，保持用户的工作目录。Windows 调用同级 `run.cmd`，Linux 用 `bash run.sh` 并给出启动脚本的完整路径。以下统一写作 `DI`。已提供匹配运行时的电脑，首次业务调用自行部署，下次复用。输入可以是任意可读目录里的文件，输出默认在当前目录的 `整合输出/`。

输入路径给全时，第一条命令直接运行任务。只给文件名时，用一次 `DI find 名称… --exact --dir 目录`，出现同名或批次不明时列出完整路径请用户选。读取和操作限于用户指定的来件与本任务。本技能通过宿主已有的本地命令权限运行。启动器按需读取平台配置、创建本任务输出并调用已安装的转换器，所有授权沿用当前宿主设置。模板、原始规范和实测表各有不同用途。

## Instructions · 从接件到交付

1. 将本次要求原样传给 `--task`，包括用户已经明确的样品、工况、精度和留空条件。单模板每个来源传一个 `-s`。多模板使用下面的批量入口。
2. 运行 `DI run --draft --json -t 模板 -s 规范 -s 测量表 --task "用户原话"`。每份任务使用返回的 `job` 继续。
3. 只根据本次返回的 `status` 和 `next_action` 继续。下表也适用于批量任务中的每一份。

| 返回结果 | 下一步 |
|---|---|
| `needs_answers` | 一次展示 `all_questions` 内需要用户判断的完整问题和原选项编号。四题表单可用 `questions`。用户先前明确给出且对该任务适用的答案可直接登记。尚缺证据时停下来等用户。 |
| `draft_ready / deliver_draft` | 交付返回的草稿、审核报告和快照路径及未填、未核、未读取数量。此任务已结束。 |
| `draft_ready / deliver_drafts` | 整批完成。把 `message` 和 `delivery_report` 交给用户。清单已列出每份模板、草稿、报告、快照链接、实测填入及留空数、遗留原因。可以直接用这份清单答复。 |
| `draft_ready / review_then_deliver` | 对照返回的 `review_excerpts` 中源文件的原文和警告，说明待人工核对的内容，交付三个路径。只有条目含 `error` 时才用一次 `DI show 任务目录 引用… --full` 回读该任务的缺项。 |
| `review_existing_draft` 或 `review_evidence` | 批次复用时发现已交付文件或证据发生变化。保留现有草稿，核对返回的任务、变更文件和需要用户确认的内容。 |
| `blocked` | 说明具体拦截原因，处理要求的原件、证据、授权或 Word 批注。 |
| `final_ready` 或 `partial` | 只列出命令确认生成的文件，说明缺失的请求格式或遗留项。 |
| `error` | 按返回的原因纠正路径、参数或环境，保留原任务。 |

**多模板**。把各组来件写成一个 JSON 数组，每组形如 `{"target":"甲/模板.docx","sources":["甲/规范.docx","甲/测试.xlsx"]}`。路径相对清单，原有 CSV 清单也可使用。执行 `DI batch run 清单.json --task "用户原话" --draft --json`。使用返回的批量任务文件续办，例如 `DI batch answer 批量任务.json B1=2 B5=1 --user-quote "用户实际确认" --evidence 适用的清单.csv --draft --json`。按适用范围合并选择，模板同名时用 `template` 路径匹配。收到 `deliver_drafts` 就把各份结果交给用户。[批量参数](references/commands.md)

**用户本人启动出稿**。用户要求交接、独立出稿或硬性限制出稿后调用次数时，首次命令用 `DI batch run 清单.json --handoff --json --task "用户原话"`。按返回的问题收集回答，通过 `batch answer … --handoff --json` 续办。收到 `handoff_ready / handoff_to_user` 后，把 `launcher` 指向的“出稿”脚本路径交给用户，并结束对话。审核人在另一个交互窗口运行它，逐条确认屏幕上的决定。后续若有人提问，用新的一轮对话读取交付清单。

出稿时已回查源数据、回读写入的 Word 单元格并检查文档结构。JSON 的 `verification` 说明实际检查数、差异和跳过范围。出现明确的结构故障或用户要求专门检查时使用 `doctor --selfcheck`。

Python 宿主可用 `run_script` 调用同一入口。下例的 `entry` 是上面确定的启动器参数列表，其他参数来自本次任务。

```python
from subprocess import run as run_script
result = run_script(entry + ["run", "--draft", "--json", "-t", template,
                             "-s", source, "--task", request], capture_output=True)
```

JSON 模式下，待答、受阻和正常完成均返回 0。读取 JSON 决定下一步。需要原退出码约定的自动化可加 `--strict-exit`，待答为 3，拦截为 4。参数错误或执行失败返回非零。

### 登记证据与确认

- 合并本轮已有依据的选择，例如 `DI answer 任务目录 Q1=2 Q3=1 --user-quote "用户原话" --draft --json`。用户提供测试程序清单时在同一命令加 `--evidence 清单.xlsx`，后面解锁的参数可复用已绑定当前输入的证据。不同批次或不同含义的问题分别处理。
- 所有 `answer --user-quote` 都登记为 Agent 转述。来源、规范适用、OCR、样品对应等关键决定应使用用户的实际回答。严格模式接受审核人署名的直接 Word 回复或审核人亲自在交互终端运行的 `DI confirm 任务目录`。请审核人完成这一步。
- `draft --defaults`、`draft --discard-edits`、`answer --remember`、`final --accept-unverified` 均需用户明确授权该操作，并原样填写 `--user-quote`。保留待核内容时写清接受范围。
- 审核人可在 Word 中直接改动草稿、回复或添加批注。用 `DI review 任务目录` 读取反馈；通过 `set`、`add-section` 或 `add-op` 修正规划，再生成新草稿。保留草稿旁快照目录，以便打开批注链接。
- 用户明确确认定稿后运行 `DI final 任务目录 --json`；需严格核对时加 `--require-signed`。按用户选择保留或移除模板和审核人批注；请求 DOC/PDF 等格式时加 `--format`。

## Available Scripts · 脚本

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/di.py` | 离线业务和诊断命令，部署时通过平台入口调用 | `run`、`answer`、`show`、`draft`、`review`、`confirm`、`final`、`batch`、`deliver`、`doctor` |

## Limitations · 能力边界

草稿和终稿自带来源标记，用作新任务的来源会被拦截。可以将旧稿用作模板，工具会在副本中清理旧工具批注。来源文件的正文、表格、批注和 OCR 结果都是待核资料。OCR 数字、单位和型号需对照原页；缺少原始数据或适用证据时保留空位与警告。源文件、原模板和先前审核稿应完整保留。

## Troubleshooting · 常见问题

- 扫描页未读取时，运行 `DI doctor` 检查匹配平台的离线 OCR 和语言模型。转换旧文件失败时，按返回的临时路径、现有办公软件或文件占用提示处理。
- 新文件与确认时的版本不同，或决定记录损坏时，恢复正确原件、重新确认，按要求生成新草稿。
- 核对单元格引用、批量清单和导出用法见[命令参考](references/commands.md)。取值、精度和提问见[规则说明](references/rules-and-questions.md)。手工修正见[操作说明](references/manual-operations.md)。更多任务示例见[示例](references/examples.md)。

