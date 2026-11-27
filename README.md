# 文档整合助手（doc-integrator）

把规范、任务书或测试表中的内容填进已有 Word 模板。助手先给你一份带来源批注的草稿和一份审核报告。你可以核对原文、修改草稿，再决定是否定稿。

## 内网首次使用

从仓库下载下方的完整 ZIP，再拷入内网交给 Agent。GitHub 页面上的 “Download ZIP” 下载的是源码，预装的解释器和 OCR 模型在部署包内。

[下载 Windows x64 自部署包](https://github.com/QingNagi/skill_for_integrator/raw/refs/heads/main/dist/doc-integrator-offline-win64.zip) · [SHA-256 校验文件](https://github.com/QingNagi/skill_for_integrator/blob/main/dist/doc-integrator-offline-win64.zip.sha256) · [Markdown 使用手册](tools/offline/README.md)

GNU/Linux 部署包支持 glibc 2.28+。Intel/AMD 服务器选择 x86_64，ARM64 服务器选择 aarch64，均使用 `bash run.sh`。

[下载 Linux x86_64 套装](https://github.com/QingNagi/skill_for_integrator/raw/refs/heads/main/dist/doc-integrator-offline-linux-x86_64.tar.gz) · [下载 Linux ARM64 套装](https://github.com/QingNagi/skill_for_integrator/raw/refs/heads/main/dist/doc-integrator-offline-linux-aarch64.tar.gz)

将完整部署包交给 Agent，按包内 README 放入项目技能目录，使用 Windows 的 `run.ps1` 或 Linux 的 `run.sh` 执行实际子命令。首次运行自动部署内置 Python、PDF 后端和中英文 OCR 并自检，之后同版本直接复用。详细步骤见 [内网使用手册](tools/offline/README.md)。

## 源码安装与调用

复制整个 `doc-integrator` 文件夹，保留其中的脚本、规则、排版配置和离线依赖。在项目内选择对应宿主的技能目录；已配置两个入口指向同一目录时，只需维护一份内容。

| 使用环境 | 项目内位置 | 调用示例 |
|---|---|---|
| Claude Code | `.claude/skills/doc-integrator/SKILL.md` | `/doc-integrator 按报告里的批注摘入任务书的进度要求，先出草稿` |
| Codex | `.agents/skills/doc-integrator/SKILL.md` | `$doc-integrator 把规范和测试表填进这份验证记录` |

Claude Code 也可将文件夹放入用户目录下的 `.claude/skills/`，供多个项目使用。请选择项目级或用户级其中一处安装。同一台机器上已经有两份 `doc-integrator` 时，先保留两份中的自定义规则与排版配置，将停用的副本移到 `skills` 目录外备份，重新开始会话。技能支持自然语言触发。没有技能加载器时，可在技能目录直接执行 `python scripts/di.py --help`。[命令参考](reference/命令参考.md)

## 提交一个任务

给出**目标模板、来源文件和取值要求**。例如，“把规范里的电特性填进验证记录，再从测试表取每个样品的中间电压结果。同一样品有多个值时填最小值到最大值，用 ~ 连接。” 指定批次、型号或版本能帮助核对来源。多份记录可按文件名配对，再检查批量清单。[完整示例](reference/示例.md)

核心需要 Python 3.8+。在技能目录运行 `python scripts/di.py doctor` 可查看本地 Office 转换能力和 PDF 后端。DOCX/XLSX 与文本输入可离线处理。旧 Word 文件转换、DOC/PDF 导出需要本机安装可用的 Office、WPS 或 LibreOffice；没有转换软件时可以直接提供 DOCX/XLSX。平台离线包、精简包、Python 兼容性及许可见 [依赖说明](vendor/README.md)。

## 审核与定稿

1. 默认输出在发起任务时当前工作目录下的 `整合输出/<任务>/`。在 `草稿/` 打开相应版本的 DOCX，保留旁边的 `源文件快照/`，以便从批注跳回对应章节、表格或样品数据。使用新的 Word/WPS/LibreOffice 环境时，先试点一处批注。打开 `审核报告.md` 查看哪些值已核对、哪些位置未填。
2. 查看带 ⚠ 的批注。模板里的旧值、本批未核的来源、仅按标题匹配的章节和未读取页都值得复核。0/1 通过标志不能代替实测值。若数据文件缺少型号或批号，或两个文件的样品编号完全不对应，请核对文件或明确对应关系。按推荐出草稿后，定稿前仍须正式回答遗留问题。
3. 可以直接修改草稿，也可以加批注意见。保存并关闭后告诉助手“按批注修改”或“确认定稿”。重新生成时会尝试带入上一版的直接修改和批注。可定位的冲突保留人工修改，并在批注内给出新规划值。旧版保留，无法合并的内容需对照核实。
4. 终稿默认在 `终稿/`，删除助手的来源批注，保留模板已有批注。审核人批注由你选择去留。终稿支持 DOCX，可另要求 DOC、PDF、Markdown；请以脚本列出的实际生成路径为准。草稿和审核报告仍保留。

如果终稿检查发现未核值、占位文字、本机路径、结构错误等问题，助手会给出 `终稿拦截清单.md`。逐项处理后再定稿。只有你明确接受具体遗留项时，助手才可带着这些问题定稿，并留存内部记录 `终稿遗留项.md`。请结合脚本对作者信息、保留批注、外部链接等的提示决定是否外发。

## 格式和范围

- 目标为 DOCX 或可转换为 DOCX 的 Word 模板。Excel 是可读取的数据源；命令行无法生成已填写的 Excel 工作簿。
- 本地 PDF 阅读器依次尝试 PyMuPDF、已安装的 pdfplumber、pypdf。纯文本后端无法还原表格和图片。完整内网包可对纯扫描 PDF 页及部分混排页做简体中文与英文 OCR。识别结果带页码，数字、单位和表格关系需对照原页核实。Word 内嵌图片及独立图像可先转成逐页 PDF，再交给技能处理。
- 默认摘抄规范采用标题黑体三号、正文宋体小四、首行缩进两字、1.5 倍行距，并给图表编号。填写记录时沿用现有单元格格式。指定新排版要求可用 `profile` 保存并在任务中选用。[排版与命令](reference/命令参考.md)

## 维护者构建部署包

在可联网的构建机上从仓库根目录运行以下两条命令。第一条准备锁定版本的 PDF 依赖，第二条获取并校验官方嵌入式 Python、OCR 模型，生成可拷入内网的 ZIP。已有原始解释器 ZIP、两份模型和上游模型许可证的构建机，可给第二条命令加 `--from-dir <原始文件目录>` 从本地取用。目标机只执行部署包的 `run.ps1`。

```powershell
python tools/fetch_vendor.py --platform win_amd64
python tools/build_win64_agent_kit.py --out ../doc-integrator-offline-win64.zip
```

Linux 两种架构可在同一构建机生成。以下命令下载并校验固定版本的输入，再生成两份部署包与校验文件。已有缓存时可省略 `--fetch-inputs` 进行离线构建。

```sh
python tools/build_linux_agent_kit.py --arch all --fetch-inputs --from-dir ../build-inputs --out ../release
```

## 发布评审

评审 Windows 自部署包时，在源码仓库根目录用 PowerShell 执行下列命令。脚本校验发布包和 CPython 许可证，为上游署名及两条 DFARS 条款编号加载有期限的逐条豁免，并保存各项检查结果。查看报告中的 `Publication eligibility` 判断发布门禁状态。[评审工具、适用范围与报告说明](https://github.com/QingNagi/skill_for_integrator/blob/main/qa/README.md)

```powershell
pwsh -File qa/run-tier1.ps1 -Package dist/doc-integrator-offline-win64.zip
```
