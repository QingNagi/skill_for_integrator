# doc-integrator 内网使用手册

**把规范、任务书、测试表或扫描 PDF 的内容填进 Word 模板。** Agent 先生成带来源批注的草稿，供你核对，再根据你的确认生成终稿。Windows x64、GNU/Linux x86_64 和 ARM64 自部署套装均包含 Python、PDF 读取组件和简体中文、英文 OCR 模型。第一次运行实际任务时自动部署，以后同一用户复用已就绪的同版本环境。

## 选择离线包

三种机器共用同一个 skill 和同一套文档命令。把下载目录交给 Agent，让它识别系统和 CPU 后选择对应文件。

| 目标机 | 文件 | 首次及后续入口 |
|---|---|---|
| Windows x64 | `doc-integrator-offline-win64.zip` | PowerShell 的 `run.ps1` |
| GNU/Linux Intel 或 AMD，`uname -m` 显示 `x86_64` | `doc-integrator-offline-linux-x86_64.tar.gz` | `bash run.sh` |
| GNU/Linux ARM64，`uname -m` 显示 `aarch64` | `doc-integrator-offline-linux-aarch64.tar.gz` | `bash run.sh` |

Linux 原生 PDF/OCR 库要求 glibc 2.28 或更高。可用 `getconf GNU_LIBC_VERSION` 核对。Linux 旧版办公格式和 PDF 导出使用目标机已有的 LibreOffice。

**输入文件放哪里。** 模板和来源可放在不同盘符或目录。给完整路径，或给相对于当前项目的路径，如 `第二批/测量表.xlsx`。只给文件名时，Agent 会在指定工作区递归查找；同名且批次不明确时，会列出路径请你选，选定后才开始处理。默认输出在模板旁的 `整合输出`；可用 `--out` 指定另一个可写目录。

**SHA-256 用在哪。** 分发包旁的 `.sha256` 供 Agent 在拷贝后核对。Linux 可在两份文件所在目录执行 `sha256sum -c 文件名.tar.gz.sha256`；Windows 用 `Get-FileHash -Algorithm SHA256` 与清单比对。包内摘要供启动器验证载荷、识别同版本安装。校验值从可信发布位置取得。

## Windows 快速开始

1. **把 ZIP 拷到内网。** 目标机需要能在内网工作的 Codex 或 Claude Code、可用的本地命令执行权限，以及可写的项目目录和用户数据目录。可直接把 ZIP 路径、目标 Word 模板、来源文件一起交给 Agent。目标机可以没有系统 Python、pip 和单独的 OCR 程序。
2. **让 Agent 放好 skill 并执行任务。** Codex 项目入口是 `.agents/skills/doc-integrator/SKILL.md`；Claude Code 项目入口是 `.claude/skills/doc-integrator/SKILL.md`。ZIP 已包含顶层 `doc-integrator` 文件夹，解压目标应是上一级 `skills` 目录。若只想在当前会话直接处理文件，也可以解压到独立目录，让 Agent 读取里面的 `SKILL.md` 并调用同级 `run.ps1`。
3. **审核草稿。** 核对“整合输出”中的草稿和审核报告。看到 OCR 来源时，对照原 PDF 的相应页核实数字和单位。保存、关闭草稿后，告诉 Agent 是继续修改还是定稿。

**可直接复制给 Agent**（替换以下路径和文件名）：

> 在本项目使用 doc-integrator。离线包位于 D:\交接\doc-integrator-offline-win64.zip。如果本项目尚未配置这个版本，请把它解压到本项目的技能目录；已有同版就复用，保留已有规则和其他版本。读取 SKILL.md，直接用 run.ps1 执行任务，首次让它自行部署并自检。目标是“验证记录.docx”，来源是“测试数据.xlsx”和“扫描规范.pdf”。按模板批注填写，先给我草稿和审核报告；扫描识别的数值请标明原页。

如果 Agent 刚解压完而当前会话的技能列表还没有刷新，可让它直接读取上述入口文件并执行。同一项目后续可直接说“用 doc-integrator 按批注填写这份记录”。

## Linux 快速开始

把对应的 tar.gz、模板和来源路径交给 Agent，并告诉它「在本项目配置 doc-integrator，先按来源生成草稿」。Agent 在项目目录执行下列步骤。示例使用 Codex，Claude Code 可将 `.agents` 换成 `.claude`。项目中已经有同版入口时直接执行最后一行。

```bash
arch=$(uname -m)
mkdir -p .agents/skills
tar -xzf "/交接/doc-integrator-offline-linux-${arch}.tar.gz" -C .agents/skills
bash .agents/skills/doc-integrator/run.sh run -t "/模板/验证记录.docx" -s "/来件/扫描规范.pdf" --task "先生成草稿"
```

同一用户首次运行会从本地载荷部署环境并识别中英文扫描样文。需要系统已有的 shell、GNU tar、gzip、sha256sum 和 flock。运行时默认放在 `~/.local/share/doc-integrator/offline/`；已设置 `XDG_DATA_HOME` 时使用该数据目录。该文件系统须允许执行程序。后续使用同一入口和参数。变更既有的入口版本前请保存该项目的自定义规则。

## 什么时候需要 OCR

| 来源内容 | 没有 OCR 时 | 完整套装的处理 |
|---|---|---|
| Word 正文、原生表格、Excel 单元格、CSV、TXT、Markdown | 可直接读取文字和数值 | 用文档结构定位来源，保留引用 |
| 带文本层的 PDF | 可直接提取正文，表格还原取决于原文件布局 | 优先保留原生文字和定位 |
| 只有像素的扫描 PDF | 可以定位到页，但搜索、摘抄和填表会缺少图中的文字 | 本地识别中英文，结果附原页页码和 OCR 标识 |
| 同一页有原生页眉、正文为扫描图 | 页眉可能读得到，正文仍可能遗漏 | 对命中检测条件的大块扫描区域补识别；小图、复杂拼版仍需检查原页 |
| 扫描表格、手写值、低清图片 | 缺少可靠的字段和值对应关系 | 识别文字行；复核表头、行列、单位、小数点和负号后再填入 |
| Word 内嵌截图、单独的照片 | 不能直接提取图片里的文字 | 可把相应内容转成逐页 PDF 后识别，或提供可检索原文 |

例如，规范中“≤ 0.05 mA”和“≤ 0.5 mA”只差一个零。即使成功识别，也要核对原页，不能据此自动判定合格。遇到未读页或识别失败，助手会登记缺口，由你补充清晰版本或确认处理方式。

## 首次部署与重复使用

| 运行时机 | Agent 的入口会做什么 |
|---|---|
| 第一次执行 | 在本机查找相同载荷摘要的已就绪环境；找不到时，从本地 `payload.zip` 校验并暂存展开。用内置 Python 实际读取文本页和双语扫描页，自检成功后发布，再执行原命令。 |
| 以后再次执行 | 检查就绪标记和必需文件。同版已就绪就直接执行，跳过安装、整包解压和首次安装自检。 |
| 首次部署中断 | 本次命令报告失败。修复磁盘、写权限或文件问题后重试，未通过自检的临时目录不会被当作成功安装。 |
| 换电脑、账号或发布包 | 新电脑或账号需要自己的环境。不同载荷有独立版本目录；已有项目技能带有自定义规则时先保留原目录，再迁移需要的规则。 |

Windows 默认本机缓存位于 `%LOCALAPPDATA%\doc-integrator\offline\<载荷摘要>\doc-integrator\`。用户目录较长时会缩短缓存目录名，仍用完整摘要校验。匹配的项目级或用户级安装也可复用。项目里的技能入口、用户缓存和文档输出是三个位置。要持续从项目里调用 skill，请保留解压出的技能文件夹。

## 能力与依赖

| 功能 | 离线依赖 | 使用条件 |
|---|---|---|
| DOCX 读取、填表、批注、来源快照、草稿/终稿、自检 | 随包 Python 3.14.10 和技能脚本 | 无需安装 Office 即可生成 DOCX；人工审阅时用能显示批注的阅读器 |
| XLSX 数据、CSV、Markdown、TXT 来源 | 随包 Python | XLSX 在本套装中作为来源，目标模板为 Word |
| PDF 原生文字、图形和表格读取 | 随包 PyMuPDF 1.28.2、pypdf 6.21.4 | 优先使用 PyMuPDF；纯文本后端的版面能力较少 |
| 扫描 PDF 中英文识别 | PyMuPDF 内置 OCR 接口、`chi_sim` 和 `eng` 模型 | 在本机运行；包内包括模型文件，无需调用外网服务 |
| 旧版 .doc/.xls 输入、输出 .doc 或 PDF | 目标机已有的 Word/Excel、兼容 WPS 或 LibreOffice | 按约定由现有办公软件承担转换。没有转换后端时请提供 DOCX/XLSX；可正常交付 DOCX |
| Agent 理解请求、读取本地文件和运行命令 | 内网可用的 Agent 与模型 | 部署包提供技能运行环境；Agent 服务、模型及其访问权限由内网提供 |

Windows x64 脚本要求 Windows PowerShell 5.1 或 PowerShell 7，且组织策略允许执行相应本地脚本。不同办公软件对旧格式、字体和批注的显示可能不同，首次换环境时宜打开实际模板生成的草稿核对。

## 常用操作

在 PowerShell 中，从项目根目录可直接调用已解压的入口。Claude Code 项目把第一行的 `.agents` 换成 `.claude`。文件路径按当前工作目录解释。

```powershell
$di = (Resolve-Path '.\.agents\skills\doc-integrator\run.ps1').Path
& $di run -t '.\验证记录.docx' -s '.\扫描规范.pdf' -s '.\测试数据.xlsx' --task '按批注填写，实测数值取范围'
```

后续命令中的任务目录、引用编号和问题编号以本次输出为准。项目目录名含空格或中文时保留引号。

| 你要做什么 | 命令示例 |
|---|---|
| 查看环境、转换后端和 OCR 能力 | `& $di doctor` |
| 查找原文并读回一条引用 | `& $di search '任务目录' '电压'`，再执行 `& $di show '任务目录' 'S1:P3' --full` |
| 查看待确认项、记录你的选择 | `& $di questions '任务目录'`；收到你的选择后执行 `& $di answer '任务目录' Q1=2` |
| 生成草稿并读取审核意见 | `& $di draft '任务目录'`、`& $di review '任务目录'` |
| 你确认后生成终稿 | `& $di final '任务目录'`。有本地转换后端时可按需加 `--format pdf` |
| 检查一个已有的 DOCX/XLSX 文件 | `& $di doctor --selfcheck '成品.docx'` |

原文件保持只读。默认在目标模板旁生成 `整合输出\<任务名>\`，其中包含 `草稿\`、`审核报告.md`，定稿后还有 `终稿\`。草稿批注中的链接指向来源快照，移动草稿时应保留相邻的快照目录。待确认项按任务保存；源文件换版后，已绑定原版本的回答需要重新核对。

## Windows 包内结构

```text
doc-integrator/
  SKILL.md            Agent 的调用条件、执行入口和文档审核流程
  README.md           本手册
  run.ps1             日常唯一必需的命令入口，负责首次自动部署
  install.ps1         安装与自检，由 run.ps1 按需调用
  payload.sha256      载荷校验值及版本识别依据
  payload.zip         真正的离线载荷
    doc-integrator/
      runtime/python/   Windows x64 Python、标准库、许可证
      runtime/tessdata/ 简体中文、英文模型及来源记录
      vendor/           PDF 依赖、版本、哈希及许可证
      scripts/          文档整合、OCR 和离线自检脚本
      profiles/         排版配置
      rules/            字段别名和用户明确记住的规则
  reference/          命令参考、规则及示例
```

## 常见问题

| 现象 | 处理 |
|---|---|
| 新机器提示找不到 python 或 pip | 用套装根目录的 `run.ps1` 运行。它会选择内置解释器。核对拿到的是带 `payload.zip` 的自部署版。 |
| 扫描页未识别，或者单位、数值看起来不对 | 运行 `doctor`，确认中英文模型可用，检查当前会话是否设置了 `DI_OCR=off`。打开审核报告列出的原页；必要时提供更清晰的扫描件或原始电子表格。 |
| 只有旧版文件时提示转换失败 | 用 `doctor` 检查本机后端；在本机办公软件中尝试打开原件，或请提供方另存为 DOCX/XLSX。 |
| 校验失败或载荷不完整 | 从可信交付位置重新复制完整 ZIP 并核对整包 SHA256。保留原有可用版本，完整复制后再重试。 |
| 安装目录不可写、策略禁止脚本 | 使用当前用户可写的项目及用户目录，并按内网管理流程允许执行相应脚本。 |
| 模型不能在内网访问，或 Agent 没有命令权限 | 请管理员提供内网可用的 Agent/模型及执行权限。也可按上方 PowerShell 示例直接操作技能。 |
| 默认草稿生成了，终稿仍被拦截 | 查看 `终稿拦截清单.md`。正式回答数据来源、OCR 核对和默认值问题；正式选择与草稿不同的，先重新出草稿。 |

## 已验证的环境

Windows x64 上使用包内 CPython 3.14.10 跑过简体中文与英文扫描页、带原生文字的 PDF 和混排页。使用外网代理不可用的测试环境与本机 Word 2010、Excel 2010，已验证 DOCX → DOC → DOCX、XLSX → XLS → XLSX 及两种 Office 文件导出 PDF；转换后能读回原文和数值。还验证了从样例模板生成草稿、读取 Word 人工批注与正文修改，再交付 DOCX、DOC、PDF 和 Markdown。WPS、LibreOffice 及其他 Office 版本请用本单位的模板另做验收。
