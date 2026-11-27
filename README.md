# 溯源填写助手（traced-template-fill）

把规范、任务书或测试数据填入已有的 Word 模板，交付带来源批注的草稿、来源快照和审核报告。审核人确认后才生成终稿。旧名为 `doc-integrator`。

## 内网首次使用

Windows x64、Linux x86_64 和 Linux aarch64 各有独立的运行时套装，均使用同一份技能指令。离线套装包括 Python、PDF 后端和中英文 OCR；旧版 Word/Excel 文档转换与 PDF 导出使用目标机已有的办公软件。

**完整包用于功能验收及经单位批准的部署。** 历史 Windows 完整包的安全建议为 `CRITICAL / DO_NOT_INSTALL`。按包的 SHA-256 查看对应的完整评审报告；源码或精简包的结论仅适用于该项交付。新完整包在构建目录生成，源码仓库独立维护技能和构建工具。[内网部署与数据使用手册](tools/offline/README.md) · [安全评审](qa/README.md)

## 源码安装与调用

只复制仓库内的 [traced-template-fill](traced-template-fill) 文件夹，保留其中的脚本、规则、排版配置和离线依赖。在项目内选择对应宿主的技能目录；已配置两个入口指向同一目录时，只需维护一份内容。

| 使用环境 | 项目内位置 | 调用示例 |
|---|---|---|
| Claude Code | `.claude/skills/traced-template-fill/SKILL.md` | `/traced-template-fill 按报告里的批注摘入任务书的进度要求，先出草稿` |
| Codex | `.agents/skills/traced-template-fill/SKILL.md` | `$traced-template-fill 把规范和测试表填进这份验证记录` |

Claude Code 也可将文件夹放入用户目录下的 `.claude/skills/`，供多个项目使用。请选择项目级或用户级其中一处安装。同一台机器上已经有两份 `traced-template-fill` 时，先保留两份中的自定义规则与排版配置，将停用的副本移到 `skills` 目录外备份，重新开始会话。技能支持自然语言触发。没有技能加载器时，可在技能目录直接执行 `python scripts/di.py --help`。[命令参考](traced-template-fill/references/commands.md)

## 提交一个任务

给出**目标模板、来源文件和取值要求**。例如，“把规范里的电特性填进验证记录，再从测试表取每个样品的中间电压结果。同一样品有多个值时填最小值到最大值，用 ~ 连接。” 指定批次、型号或版本能帮助核对来源。多份记录可按文件名配对，再检查批量清单。[完整示例](traced-template-fill/references/examples.md)

纯源码使用下表选择 Python。

| Python 环境 | 可用能力 |
|---|---|
| 3.8 | DOCX、XLSX、文本处理 |
| 3.9 及以上 | lite 包随附 pypdf，支持 PDF 文本层；源码安装时需自备兼容的依赖 |
| 自行配置完整 PDF 后端 | 解释器须满足所选 pdfplumber、pdfminer.six、pypdfium2、Pillow 等各自的版本和架构要求，另需 Tesseract 以及中英文模型 |
| 三平台完整离线套装 | 自带 CPython 3.14.10 及中英文 OCR |

在技能目录运行 `python scripts/di.py doctor` 可查看本地 Office 转换能力和 PDF 后端。DOCX/XLSX 与文本输入可离线处理。旧 Word 文件转换、DOC/PDF 导出需要本机安装可用的 Office、WPS 或 LibreOffice；没有转换软件时可以直接提供 DOCX/XLSX。平台离线包、精简包、Python 兼容性及许可见 [依赖说明](traced-template-fill/vendor/README.md)。

## 审核与定稿

1. 默认输出在发起任务时当前工作目录下的 `整合输出/<任务>/`。在 `草稿/` 打开相应版本的 DOCX，保留旁边的 `源文件快照/`，以便从批注跳回对应章节、表格或样品数据。使用新的 Word/WPS/LibreOffice 环境时，先试点一处批注。打开 `审核报告.md` 查看哪些值已核对、哪些位置未填。
2. 查看带 ⚠ 的批注。模板里的旧值、本批未核的来源、仅按标题匹配的章节和未读取页都值得复核。0/1 通过标志不能代替实测值。若数据文件缺少型号或批号，或两个文件的样品编号完全不对应，请核对文件或明确对应关系。按推荐出草稿后，定稿前仍须正式回答遗留问题。
3. 可以直接修改草稿，也可以加批注意见。保存并关闭后告诉助手“按批注修改”或“确认定稿”。重新生成时会尝试带入上一版的直接修改和批注。可定位的冲突保留人工修改，并在批注内给出新规划值。旧版保留，无法合并的内容需对照核实。
4. 来源核对及样品对应等关键决定须通过 `--user-quote` 保存审核人原话，记录在任务的 `decisions.jsonl`。终稿默认在 `终稿/`，移除助手来源批注。模板批注和审核人批注分别由你选择保留或删除。终稿支持 DOCX，可另要求 DOC、PDF、Markdown；请以脚本列出的实际生成路径为准。草稿和审核报告仍保留。

如果终稿检查发现未核值、占位文字、本机路径、结构错误等问题，助手会给出 `终稿拦截清单.md`。逐项处理后再定稿。只有你明确接受具体遗留项时，助手才可带着这些问题定稿，并留存内部记录 `终稿遗留项.md`。请结合脚本对作者信息、保留批注、外部链接等的提示决定是否外发。

## 格式和范围

- 目标为 DOCX 或可转换为 DOCX 的 Word 模板。Excel 是可读取的数据源；命令行无法生成已填写的 Excel 工作簿。
- PDF 文字和表格由 pdfplumber 读取。PDFium 渲染扫描页或混排页中的图片区域，交给本地 Tesseract 识别简体中文和英文。识别文字保留原页引用，低置信度的数字会提示复核。合并表头、手写数字和单位请对照原页检查。仅有 pypdf 时可读取文本层。Word 内嵌图片及独立图像可先转成逐页 PDF，再交给技能处理。
- 默认摘抄规范采用标题黑体三号、正文宋体小四、首行缩进两字、1.5 倍行距，并给图表编号。填写记录时沿用现有单元格格式。指定新排版要求可用 `profile` 保存并在任务中选用。[排版与命令](traced-template-fill/references/commands.md)

## 源码、精简包和离线套装

公开仓库提供源代码。直接安装 `traced-template-fill/` 即可处理已有的 DOCX/XLSX；阅读 PDF 时使用部署方已装的兼容后端。维护者可在本地构建并分别评审以下产物。

| 产物 | 内容 | 使用位置 |
|---|---|---|
| source ZIP | 可独立安装的技能，不含 wheel 或解释器 | 用于技能本体复核 |
| lite ZIP | 技能与两个纯 Python wheel | 已有 Python 3.9+，需 PDF 文本提取 |
| 完整离线套装 | 技能、对应架构 Python、PDF 原生组件、中英文 OCR 模型及许可证 | 本地验收；项目许可与发布审批待定 |

以下命令从仓库根目录运行，输出到仓库外的 `../release/`。依赖下载仅在构建机进行。校验、构建阶段可以从 `--vendor-dir` 或 `--from-dir` 指定的本地目录取原件。

```powershell
python tools/fetch_vendor.py --platform all --mirror pypi
python tools/build_offline_bundle.py --platform source,lite --tag review --out ../release
python -m venv .build/zig
& ./.build/zig/Scripts/python.exe -m pip install ziglang==0.14.1
$zig = & ./.build/zig/Scripts/python.exe -c "from pathlib import Path; import ziglang; print(Path(ziglang.__file__).with_name('zig.exe'))"
foreach ($platform in 'win_amd64','manylinux_x86_64','manylinux_aarch64') {
    python tools/build_ocr_runtime.py --zig $zig --platform $platform
    if ($LASTEXITCODE) { throw "OCR 构建失败 ($platform)" }
}
python tools/build_win64_agent_kit.py --out ../release/traced-template-fill-offline-win64.zip
python tools/build_linux_agent_kit.py --arch all --fetch-inputs --from-dir ../build-inputs --out ../release
```

以上是 Windows 构建机的三平台交叉编译示例。部署到内网只需相应架构的完整包。维护者也可预置与清单匹配的 Tesseract 程序和原始依赖，再离线组装。[构建输入、原件摘要与工具链](traced-template-fill/vendor/README.md)

同一份 Markdown 手册随完整包交付。对每个 ZIP 分别运行 Tier 1；本体通过的项目不能直接计入运行时的安全结论。SHA-256 用于核对收到的字节；部署方通过可信渠道取得摘要或签名后再核验。[第三方组件和许可](traced-template-fill/THIRD_PARTY_NOTICES.md) · [评审入口与证据](qa/README.md)
