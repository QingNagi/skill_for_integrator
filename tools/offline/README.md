# traced-template-fill 内网使用手册

本合并套装仅供本地验收。正式部署使用分别核验的技能包和同平台运行时包；SkillSpector 门禁覆盖独立技能包。

**把规范、任务书、测试表或扫描 PDF 的内容填进 Word 模板。** Agent 先生成带来源批注的草稿，供你核对，再根据你的确认生成终稿。Windows x64、GNU/Linux x86_64 和 ARM64 自部署套装均包含 Python、PDF 读取组件和简体中文、英文 OCR 模型。第一次运行实际任务时自动部署，以后同一用户复用已就绪的同版本环境。

## 选择离线包

旧版 Windows 完整包曾得到 `CRITICAL / DO_NOT_INSTALL`。更换 PDF 引擎后的套装按各自 SHA-256 单独验收，部署方可将技术验收记录与对应包的安全评审一起核对。下列步骤用于功能验收及通过组织审核后的部署。[发布评审说明](https://github.com/QingNagi/skill_for_integrator/blob/main/qa/README.md)

## 正式安装

取得同一构建的技能 ZIP、本机 CPU 对应的 runtime 包、安装脚本、共用的 deploy.py 和 SHA256SUMS 清单，放在同一交付目录。从可信发布渠道核对清单及所用脚本的摘要。安装只需当前用户权限。

~~~powershell
& 'D:\交接\install.ps1' -ProjectRoot 'D:\验证项目'
~~~

~~~sh
sh /交接/install.sh --project-root /工作/验证项目
~~~

两个命令都指定已有项目目录。需要当前账号的所有项目共用时，使用 `-User` 或 `--user` 选择主目录。已有 Python 3.10+ 且只需源码能力时加 `-SourceOnly` 或 `--source-only`。双包安装可在没有系统 Python 的机器上从已校验的运行时提取解释器。

源码安装在 `.agents/skills/traced-template-fill`。`.claude/skills/traced-template-fill` 是 Windows 目录联接或 Linux 相对符号链接。技能目录中的每个文件均与源码 ZIP 比对。运行时存放在用户数据目录或 `DI_RUNTIME_DIR`，项目的 `.traced-template-fill/` 保存运行时入口与备份。安装器最后从宿主入口执行 `doctor`。

安装器在选定范围内检查旧名称和旧版本，整目录备份到 `.traced-template-fill/backups/<时间与编号>/`，打印每个原位置与备份位置。自定义规则和排版配置随目录保留。项目安装发现用户目录的另一份技能时打印位置。需要沿用的排版规范可从备份复制到项目的 `.doc-integrator/profiles/`。学得的映射可合并到 `.doc-integrator/learned.json` 的 `answers` 字段。同一版本重复安装会复用现有入口。

重新开启 Claude Code 或 Codex 会话后查看技能列表。Hermes 需要信任当前项目。Copilot CLI 可用 `/skills reload`，OpenCode 重启后更新列表。

| 机器条件 | 需要交给 Agent 的内容 | 可用功能 |
|---|---|---|
| 新电脑，需要扫描件识别 | 技能 ZIP + 对应平台运行时，或本地完整验收包 | Word/Excel 输入、PDF 文字、表格、页面渲染、中英文扫描件 OCR |
| 已有 Python 3.10+，仅处理 DOCX/XLSX | 技能 ZIP | 新版 Office 文档读写、来源快照及审核 |
| 已有 Python，只需可选中的 PDF 文字 | 技能 ZIP + 精简依赖 ZIP | 根据依赖包 README 使用离线 wheel；图片和扫描页仍需完整 OCR 运行时 |
| 需要旧 DOC/XLS 或 PDF 导出 | 以上部署加本机可用的 Office、WPS 或 LibreOffice | 格式转换；Linux 使用本机 LibreOffice |

技能与运行时需来自同一构建，配对校验失败时，重新取得匹配的两个包。Windows、Linux 的 CPU 指令和动态库不同，因此使用不同运行时。业务规则、调用命令和生成的 Word 格式相同。部署单位可分别审查技能 ZIP 和原生依赖。Windows 运行时带 CPython 官方 SPDX 和签名材料；各平台都有 CycloneDX 清单。源码与组织签发的安装批准应分别核验。

三种机器共用同一个 skill 和同一套文档命令。把下载目录交给 Agent，让它识别系统和 CPU 后选择对应文件。

| 目标机 | 文件 | 首次及后续入口 |
|---|---|---|
| Windows x64 | `traced-template-fill-offline-win64.zip` | `run.cmd` 或 PowerShell 的 `run.ps1` |
| GNU/Linux Intel 或 AMD，`uname -m` 显示 `x86_64` | `traced-template-fill-offline-linux-x86_64.tar.gz` | `bash run.sh` |
| GNU/Linux ARM64，`uname -m` 显示 `aarch64` | `traced-template-fill-offline-linux-aarch64.tar.gz` | `bash run.sh` |

Linux 原生 PDF/OCR 库要求 glibc 2.28 或更高。可用 `getconf GNU_LIBC_VERSION` 核对。Linux 旧版办公格式和 PDF 导出使用目标机已有的 LibreOffice。

**输入文件放哪里。** 模板和来源可放在不同盘符或目录。给完整路径，或给相对于当前项目的路径，如 `第二批/测量表.xlsx`。只给文件名时，Agent 会在指定工作区递归查找；同名且批次不明确时，会列出路径请你选，选定后才开始处理。默认输出在发起任务时当前工作目录下的 `整合输出`；可用 `--out` 指定另一个可写目录。

**SHA-256 用在哪。** 分发包旁的 `.sha256` 供 Agent 在拷贝后核对。Linux 可在两份文件所在目录执行 `sha256sum -c 文件名.tar.gz.sha256`；Windows 用 `Get-FileHash -Algorithm SHA256` 与清单比对。包内摘要供启动器验证载荷、识别同版本安装。校验值从可信发布位置取得。

## 运行项目的技能

~~~powershell
& '.\.agents\skills\traced-template-fill\run.cmd' run --draft -t '.\验证记录.docx' -s '.\测试数据.xlsx' --task '先生成草稿'
~~~

~~~sh
sh .agents/skills/traced-template-fill/run.sh run --draft -t '/模板/验证记录.docx' -s '/来件/扫描规范.pdf' --task '先生成草稿'
~~~

核对草稿和审核报告。含扫描数据时对照 PDF 原页复核数字和单位。审核人保存并关闭草稿后，助手可按批注修改或定稿。

合并的 offline 套装可解到独立工作目录进行兼容验收，Windows 使用其中的 `run.cmd`，Linux 使用 `sh run.sh`。旧的同目录配对入口仍支持将两件归档解到独立目录后直接调用。

## 由审核人启动批量出稿

需要严格控制出稿后模型成本的批次，向 Claude 或 Codex 说“请用交接模式准备这批任务，我退出对话后亲自出稿”，并给出各组模板、原始来源和适用的程序清单。Agent 会合并提问。问题答齐后，它会返回本批专属的封存清单和启动脚本。此时还没有生成本批草稿。

1. 等规划对话结束，记录返回的完整路径。Windows 双击本批的 `出稿.cmd`。Linux 在独立终端运行 `sh "/实际返回的路径/出稿.sh"`。两个入口都使用封存时已就绪的 Python 和同一个 `di.py deliver`。
2. 核对终端逐项列出的任务、型号、样品、原话和选项，按屏幕提示确认或拒绝。只有你确认的受保护决定才登记为 `confirm_cli`。原件、证据或答案发生变化时，根据提示回到规划步骤核实并重新封存。
3. 打开终端给出的交付清单。每份模板对应的草稿、审核报告、来源快照、实测填入与留空数量、仍需人工复核的原因都在其中。需要问 Claude 时另开对话并提供清单。草稿审核和最终定稿继续使用原有流程。

出稿阶段由本地程序控制。规划会话已经结束、由审核人另行启动时，该会话在出稿后的工具调用数为 **0**。直接在普通对话里让 Agent 生成草稿时，模型仍可自行复查，按实测次数统计。终端确认能区分确认渠道；组织身份和电子签名仍沿用单位自己的流程。

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

## 怎样核对实测值

拿到旧成品做对照时，请一并提供本批原始测试表、型号和批号、样品号、测试条件及单位。可以把测试程序的标签和分段说明作为来件。技能按样品编号定位源列；即使两列顺序互换，也要指回对应样品。相同路径同时由目录和文件参数传入时只处理一次，不同文件夹的同名文件各有自己的来源编号。

例如，任务要求只取中间电压，表中既有 `Vnorm` 又有未标电压的项目。后者需要核实测试条件。若速率栏的原始结果是 `1`、规范下限是 `800 Mbps`，请提供仪器状态码含义和测试档位；取得实测速率前，该栏应保留待核。报告区分原始数值、状态、规范限值和缺失证据。

一批待确认问题可以一次回答。同一任务只重算一次。问题给出后若替换过模板或来源，请重新查看问题。生成草稿后若输入有变，需重新出草稿并审核。审核报告里的来源链接可用于检查具体样品、单位、原始区间和条件。

## 首次部署与重复使用

| 运行时机 | Agent 的入口会做什么 |
|---|---|
| 第一次执行 | 在当前用户的版本缓存中查找对应载荷摘要。缺少就绪环境时，校验本地载荷并暂存展开。用内置 Python 实际读取文本页和双语扫描页，自检成功后执行原命令。 |
| 以后再次执行 | 检查就绪标记和必需文件。同版已就绪就直接执行，跳过安装、整包解压和首次安装自检。 |
| 首次部署中断 | 本次命令报告失败。修复磁盘、写权限或文件问题后重试，未通过自检的临时目录不会被当作成功安装。 |
| 换电脑、账号或发布包 | 新电脑或账号需要自己的环境。不同载荷有独立版本目录；已有项目技能带有自定义规则时先保留原目录，再迁移需要的规则。 |

Windows 的本机缓存位于 `%LOCALAPPDATA%\traced-template-fill\offline\` 内按载荷摘要分开的版本目录，目录名使用摘要前 24 位，就绪标记保存完整摘要。在多个项目调用同一个完整包入口时，它们复用该缓存。迁移项目可从两个宿主入口调用同一版本。

## 能力与依赖

| 功能 | 离线依赖 | 使用条件 |
|---|---|---|
| DOCX 读取、填表、批注、来源快照、草稿/终稿、自检 | 随包 Python 3.14.10 和技能脚本 | 无需安装 Office 即可生成 DOCX；人工审阅时用能显示批注的阅读器 |
| XLSX 数据、CSV、Markdown、TXT 来源 | 随包 Python | XLSX 在本套装中作为来源，目标模板为 Word |
| PDF 原生文字、图形和表格读取 | pdfplumber、pdfminer.six、pypdfium2，文本兜底用 pypdf | 原生文字带页码；合并表头和无框线表格请对照原 PDF |
| 扫描 PDF 中英文识别 | 随包 Tesseract 命令行、`chi_sim` 和 `eng` 模型 | 扫描页和部分混排区域在本机识别，低置信度的文字和数字会标记复核。分值反映识别器信心，所有测量值均须核对原页 |
| 旧版 .doc/.xls 输入、输出 .doc 或 PDF | 目标机已有的 Word/Excel、兼容 WPS 或 LibreOffice | 按约定由现有办公软件承担转换。没有转换后端时请提供 DOCX/XLSX；可正常交付 DOCX |
| Agent 理解请求、读取本地文件和运行命令 | 内网可用的 Agent 与模型 | 部署包提供技能运行环境；Agent 服务、模型及其访问权限由内网提供 |

Windows x64 套装支持原生 `run.cmd`；使用 `run.ps1` 时需要 Windows PowerShell 5.1 或 PowerShell 7，且组织策略允许执行相应本地脚本。不同办公软件对旧格式、字体和批注的显示可能不同，首次换环境时宜打开实际模板生成的草稿核对。

## 常用操作

在 PowerShell 中，从项目根目录可直接调用已解压的入口。Claude Code 项目把第一行的 `.agents` 换成 `.claude`。文件路径按当前工作目录解释。

```powershell
$di = (Resolve-Path '.\.agents\skills\traced-template-fill\run.cmd').Path
& $di run --draft -t '.\验证记录.docx' -s '.\扫描规范.pdf' -s '.\测试数据.xlsx' --task '按批注填写，实测数值取范围'
```

路径明确时，这一条命令从建任务一直做到草稿、来源快照和审核报告。遇到来源不明、缺件或 OCR 原页待核，就在本次输出里列出问题；在同一任务里记录回答后继续。只想先看方案时，使用 `run`。项目目录名含空格或中文时保留引号。

| 你要做什么 | 命令示例 |
|---|---|
| 查看环境、转换后端和 OCR 能力 | `& $di doctor` |
| 查找原文并读回一条引用 | `& $di search '任务目录' '电压'`，再执行 `& $di show '任务目录' 'S1:P3' --full` |
| 查看待确认项、记录你的选择 | `& $di questions '任务目录'`；收到你的选择后执行 `& $di answer '任务目录' Q1=2` |
| 生成草稿并读取审核意见 | `& $di draft '任务目录'`、`& $di review '任务目录'` |
| 你确认后生成终稿 | `& $di final '任务目录'`。有本地转换后端时可按需加 `--format pdf` |
| 检查一个已有的 DOCX/XLSX 文件 | `& $di doctor --selfcheck '成品.docx'` |

原文件保持只读。默认在发起任务时的当前工作目录下生成 `整合输出\<任务名>\`，其中包含 `草稿\`、`审核报告.md`，定稿后还有 `终稿\`。草稿批注中的链接指向来源快照，移动草稿时应保留相邻的快照目录。待确认项按任务保存；源文件换版后，已绑定原版本的回答需要重新核对。

## Windows 合并验收包结构

```text
traced-template-fill/
  SKILL.md            Agent 的调用条件、执行入口和文档审核流程
  README.md           本手册
  run.cmd             Windows 原生入口，校验、自动部署并运行
  run.ps1             PowerShell 入口，复用相同环境
  run.sh              Windows Git Bash 入口
  install.ps1         安装与自检，由 run.ps1 按需调用
  payload.sha256      载荷校验值及版本识别依据
  payload.zip         真正的离线载荷
    traced-template-fill/
      runtime-bom.cdx.json   对应平台的 CycloneDX 组件清单
      runtime/python/   Windows x64 Python、标准库、许可证
      runtime/ocr/      Tesseract 命令行程序（含 Leptonica）
      runtime/tessdata/ 简体中文、英文模型及来源记录
      vendor/           PDF 依赖、版本、哈希及许可证
      scripts/          文档整合、OCR 和离线自检脚本
      config/profiles/  排版配置
      config/rules/     内置字段别名与规则默认值
  references/          命令参考、规则及示例
```

## 常见问题

| 现象 | 处理 |
|---|---|
| 换电脑后是否要重新部署 | 将与目标系统和 CPU 匹配的完整套装拷到新机器。首次任务会校验载荷并用内置样文实测中英文 OCR。以后复用同版环境。保留解压的载荷，启动器发现必需脚本缺失或为空时会用它修复。 |
| 首次验收通过，任务中扫描页仍然没有文字 | 首次验收验证套装自带的 OCR；业务命令沿用用户设置。检查 `DI_OCR`、`DI_NO_VENDOR` 和 `DI_CACHE_DIR`，再结合审核报告中的页码核对。 |
| Word 报“无法创建工作文件” | 转换器为本次任务创建可写的临时目录。当前账户能够创建缺失的 Word 缓存时会自动补建。Word 2010 读取符合条件的本地普通旧版 Word 文档时，优先用已安装的 Microsoft Wordconv 转为 DOCX 并校验结果，避开已复现的 Word 后台长时间无响应。配置了文件阻止策略或带受限来源标记的文件交由 Office 处理。系统拒绝写入 `Content.Word` 时，错误信息会列出相应路径。仍不可用时继续尝试本机可用的其他办公后端。DOC、PDF 导出需要支持相应格式的可用后端；错误信息会指出失败原因。 |
| 更新了项目里的 skill，Claude 仍用旧版 | 核对本次加载的 `SKILL.md` 路径。个人和项目目录各有一份 `traced-template-fill` 时，保留所需的 `rules/`、`profiles/`，把旧数据迁到项目的 `.doc-integrator/`，将停用副本移到技能发现目录外备份，再打开新会话。 |
| 新机器提示找不到 python 或 pip | 用套装根目录的 `run.ps1` 运行。它会选择内置解释器。核对拿到的是带 `payload.zip` 的自部署版。 |
| 扫描页未识别，或者单位、数值看起来不对 | 运行 `doctor`，确认中英文模型可用，检查当前会话是否设置了 `DI_OCR=off`。打开审核报告列出的原页；必要时提供更清晰的扫描件或原始电子表格。 |
| 只有旧版文件时提示转换失败 | 用 `doctor` 检查本机后端；在本机办公软件中尝试打开原件，或请提供方另存为 DOCX/XLSX。 |
| 校验失败或载荷不完整 | 从可信交付位置重新复制完整 ZIP 并核对整包 SHA256。保留原有可用版本，完整复制后再重试。 |
| 安装目录不可写、策略禁止脚本 | 使用当前用户可写的项目及用户目录，并按内网管理流程允许执行相应脚本。 |
| 模型不能在内网访问，或 Agent 没有命令权限 | 请管理员提供内网可用的 Agent/模型及执行权限。也可按上方 PowerShell 示例直接操作技能。 |
| 默认草稿生成了，终稿仍被拦截 | 查看 `终稿拦截清单.md`。正式回答数据来源、OCR 核对和默认值问题；正式选择与草稿不同的，先重新出草稿。 |

## 已验证的环境

Windows x64 上使用包内 CPython 3.14.10 跑过简体中文与英文扫描页、带原生文字的 PDF 和混排页。使用外网代理不可用的测试环境与本机 Word 2010、Excel 2010，已验证 DOCX → DOC → DOCX、XLSX → XLS → XLSX 及两种 Office 文件导出 PDF；转换后能读回原文和数值。还验证了从样例模板生成草稿、读取 Word 人工批注与正文修改，再交付 DOCX、DOC、PDF 和 Markdown。另在 Windows 本地隔离运行的 LibreOffice 26.8.3.1 上验证了草稿审核、DOCX → DOC → DOCX 和 PDF 导出。往返文件的生成器和 PDF 的 Producer 均已核实，两个批注保留。其他 Office 版本、WPS 和本单位模板需分别实测。


## 工作区、数据与清理

在工作目录启动 Agent。输入可以是本机任意可读路径，文件名不唯一时，助手列出候选路径，等待选择。默认 `find` 只查工作区，指定 `--dir` 时只查所列目录。产物在当前目录的 `整合输出/<任务>/`，可以用 `--out` 指定输出根目录。

模板和来源原件保持只读。任务草稿旁的 `源文件快照/` 包含可供批注链接访问的来源副本；任务目录、原话记录和审核报告均可能含敏感信息。共享的 `整合输出/_缓存/` 包含解析文本、提取媒体和旧格式转换件。本地解析与 OCR 无需联网。Agent 能看到工具返回的文字，`show --full` 会把选中引用的完整内容交给宿主模型。使用外部模型时，这些内容会随对话发送给对应服务；内网保密任务请使用获准的模型环境。

关闭正在使用该输出目录的任务后，运行 `DI clean` 预览可清理的解析缓存，确认后运行 `DI clean --apply`。自定义输出根使用 `--out <输出根>`。草稿、快照、原件、终稿和审核记录继续保留。删除原件前务必考虑后续审核与历史任务的需要。

项目长期规则保存在工作区的 `.doc-integrator/learned.json`，排版规范在同目录下的 `profiles/`。跨项目只读默认值可放在 `DI_USER_DATA_DIR` 指定目录下的同名文件。老版本的定制规则可按项目迁入；同键优先使用项目设置。重新打开旧任务时可直接使用原任务目录。

在限制写用户目录的沙箱中，把 `DI_CACHE_DIR` 设为项目内可写的绝对目录再运行启动器。Windows 使用 `$env:DI_CACHE_DIR = Join-Path $PWD '.doc-integrator/runtime-cache'`；Linux 使用 `export DI_CACHE_DIR="$PWD/.doc-integrator/runtime-cache"`。每次任务使用同一位置。应用控制拒绝启动解释器或原生库时，按单位流程核准该版本。

定稿时，已有模板批注用 `--template-comments keep|drop` 选定去留，审核人批注用 `--reviewer-comments keep|drop` 单独选择。两类都存在时，可以一次说明两项决定。来源、样品对应和四个影响草稿、终稿或长期规则的选项用 `--user-quote "用户原话"` 随命令登记。

## 在内网交给 Agent 操作

启动会话前进入本次项目目录，把模板与来源的完整路径交给 Agent。首次运行会在用户目录部署 Python、PDF 库及中英文 OCR。可用 `DI_RUNTIME_DIR` 指定存放不同版本运行时的目录，例如 `$env:DI_RUNTIME_DIR = Join-Path $PWD '.doc-integrator/runtime'` 或 `export DI_RUNTIME_DIR="$PWD/.doc-integrator/runtime"`。它优先于原有 `DI_CACHE_DIR` 的安装位置。安装成功后，同一版本直接复用。

用套装启动器运行 `doctor --prune` 可预览旧版本，运行 `doctor --prune --apply` 才执行清理。当前版、最近安装的其他可验证版本、使用中的版本及改过规则、配置或其他文件的版本会保留。旧套装未登记安装时文件状态，也会保留。Linux 上若挂载为 `noexec`，可将 `DI_RUNTIME_DIR` 或 `XDG_DATA_HOME` 指向允许执行的文件系统后重新启动。

使用 `run --draft --json` 建立任务。若有待答题，Agent 直接展示返回的问题。回答后用 `answer --draft --json` 一步继续。多项核对可经 `questions --csv` 导出和 `answer --from-sheet` 回填；测试程序清单支持 XLSX 和 CSV。用 `answer --evidence` 提交时，技能会核对来源、段序和单位，登记文件摘要及实际工况。操作格式见技能中的 `references/commands.md`。

文件解析、OCR、缓存、草稿及来源快照均在本机处理。Agent 调用 `show --full`、展示问题或向模型转述时，当前片段会进入其配置的模型服务。机密数据应使用已获准的内网模型端点。缓存清理预览使用 `clean`，确认清理用 `clean --apply`；草稿、来源快照和审核事实仍保存在任务目录，可按项目留存要求单独管理。

同批次有多份独立任务时可用 `batch draft 清单.json --jobs 2`；旧缓存的空间检查用 `doctor --prune` 预览。Linux 若提示文件系统以 noexec 挂载，请把 `DI_RUNTIME_DIR` 指到允许当前用户执行文件的位置。
