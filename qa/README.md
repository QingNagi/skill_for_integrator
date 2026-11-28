# 源码技能的 Tier 1 门禁

从仓库根目录构建源码 ZIP，在 PowerShell 7.2 以上运行官方静态检查。CI 在每次 push 和 pull request 上运行相同入口，保存 ZIP、SHA-256、无基线 SkillSpector JSON 和带基线的 SkillEvaluator JSON、HTML、Markdown。各次运行使用独立目录。[CI 工作流](../.github/workflows/skill-source-tier1.yml)

```powershell
python -B tools/build_offline_bundle.py --platform source --tag 1.3.0 --out ../release
./qa/run-tier1.ps1 -SourceGate -ScannerDiagnostics -PreviousVersion 1.2.0 -Package ../release/traced-template-fill-1.3.0-source.zip
$LASTEXITCODE
```

工具安装在专用环境时，传入 `-Evaluator`、`-SkillSpector`、`-Gitleaks` 的绝对路径。内部接收条件为 13 项全部通过、无未完成扫描、直接扫描 complete、无活动 HIGH/CRITICAL、质量 A，且 6 个 harness 均可加载。这里使用 SkillEvaluator 1.2.1、SkillSpector 2.13.7 和 Gitleaks 8.30.1。Tier 1 的发布资格字段单独保留。NVIDIA 目录发布还需相应的 Tier 2 和 Tier 3 证据。[报告定义](https://docs.nvidia.com/skills/skillevaluator/reports)

独立诊断始终保留全部发现。正式 Tier 1 接收仓库 qa 下有摘要固定的外部基线，`skillspector_baseline` 记录加载及抑制情况。第三轮基线是空集，因此入口核对抑制数为零。将来同意接受某条发现时，需登记两位复核人、证据、重审条件、负责人及到期日，再逐项更新基线、固定摘要和核对条件。路径适用性由上游分类器单独计算，原始分与适用分见 `skillspector_applicability`。依赖、Windows 运行时分别走供应链审核。[第三轮逐项记录](reports/third-review/security.md) · [上游基线语义](https://docs.nvidia.com/skills/skillevaluator/tier1-reference)

## 1.1.2 及更早发布包的历史复核

[最新 1.1.2 现场验收、Claude 过程与交付件摘要](reports/second-review/README.md) · [历史数据盲测和全流程验收](2026-11-28-数据核对与全流程复验.md) · [上一轮 PDF 后端迁移验收](2026-11-28-离线验收.md)

source 只含业务代码和资源；lite 增加纯 Python wheel；完整包包含解释器、原生 PDF 组件和 OCR 模型。请按实际产物分别复核。历史 Windows 发布包包含 PyMuPDF，复核该历史包时可在评审机使用项目修复版 SkillSpector。安装命令、同包对照结果及仍需复核的发现见 [PyMuPDF 归档扫描修复](skillspector-zip/README.md)。

在评审机的 PowerShell 7.2 或更新版本中，从仓库根目录调用 `qa/run-tier1.ps1`。评审机需有 SkillEvaluator、SkillSpector 和 Gitleaks。已验证 SkillEvaluator 1.2.1、Gitleaks 8.30.1、上游 SkillSpector 2.13.6 / 2.13.7 和本项目的修复版。入口支持指定工具绝对路径。未指定扫描器时使用 PATH 中的 skillspector。正式评审应指定官方发行版的绝对路径，记录版本。历史归档补丁可通过 -SkillSpector 显式选择作诊断。静态评审使用 `--no-llm`。

## 评审发布 ZIP

发布 ZIP 与其同名 `.sha256` 文件放在一起。若持有单独取得的发布摘要，可加 `-PackageSha256 <64位摘要>`。入口先校验外包和内层 `payload.zip`，再展开真实交付内容。外层启动器和文档会与内层对应文件逐一比对。没有对应文件及内容不同的文件一并纳入扫描。

```powershell
$qa = @{
    OutputDirectory = (Join-Path $HOME 'Documents/doc-integrator-reviews')
    Evaluator = (Get-Command skillevaluator -ErrorAction Stop).Source
    SkillSpector = (Get-Command skillspector -ErrorAction Stop).Source
    Gitleaks = (Get-Command gitleaks -ErrorAction Stop).Source
}
& ./qa/run-tier1.ps1 @qa -Package ../release/traced-template-fill-offline-win64.zip
$LASTEXITCODE
```

工具装在独立环境时，将上面相应的工具值写成绝对路径。工具均在 PATH 中时也可以运行简写。

```powershell
& ./qa/run-tier1.ps1 -Package ../release/traced-template-fill-offline-win64.zip
```

## 评审已部署的技能

传入含 `SKILL.md` 的实际技能目录，可用项目或用户安装位置。入口识别含 `payload.zip` 的启动目录，也支持含 `runtime/python/` 的已展开目录。它在独立位置建立快照，保留已部署的规则和本地文件。输出目录应位于技能目录外。若部署树内部使用链接，请选用完整物理文件树，或评测对应的发布 ZIP。

```powershell
& ./qa/run-tier1.ps1 @qa -InstalledDirectory 'D:\项目\.claude\skills\traced-template-fill'
$LASTEXITCODE
```

每次评审在 `-OutputDirectory` 下创建独立子目录。默认根目录为系统临时目录中的评审专用文件夹。控制台输出本次报告的绝对路径。快照位于 `stage/<技能名>/`，审核过的豁免文件位于同级的 `stage/pii-waivers.yaml`。SkillEvaluator 以豁免文件的父目录解析文件位置，重跑时应保持这两个相对位置。

## Windows 完整包的三项 CPython 许可证豁免

入口核对实际许可证的 SHA-256 和审核过的 YAML 摘要，按 UTC 日期检查 2026-11-27 至 2027-05-27 的有效期，包括首尾两天。任一项失配时，继续执行全套评审，保留全部 PII 发现并返回非零退出码。评审完成后还会逐个核对实际应用的三个豁免 ID。

| 许可证行号 | 精确豁免 ID | 原因 |
| --- | --- | --- |
| 359 | `CPYTHON-31410-LICENSE-BZIP2` | bzip2 版权署名中的公开邮件地址 |
| 631 | `CPYTHON-31410-LICENSE-DFARS-7014` | DFARS 条款编号 252.227-7014 |
| 672 | `CPYTHON-31410-LICENSE-DFARS-7013` | DFARS 条款编号 252.227-7013 |

三个豁免都限定在对应版本的 `<技能名>/runtime/python/LICENSE.txt` 的指定行、类型和匹配值摘要。负责人、理由和日期见 [pii-waivers.yaml](pii-waivers.yaml)。该文件的 UTF-8、LF 规范化摘要为 `205636c123e99cb8ed23711f610f0702f989044c4dfa4e039555cf04b68e60b5`。更新 CPython、证据或有效期时，应先对新的原件和原始 PII 命中做复核，再更新对应记录及入口中的校验值。

上游原件为官方 `python-3.14.10-embed-amd64.zip`，SHA-256 为 `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`。已逐字节比对其 LICENSE.txt，35,407 字节，SHA-256 为 `935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68`。入口使用这一固定摘要进行离线核对。[CPython 3.14.10 官方发布页](https://www.python.org/downloads/release/python-31410/)

## 报告和退出码

- `skillevaluator-tier1.md` 与 `skillevaluator-tier1.json` 包含原始完整 Tier 1 结果，`tier1.log` 保存运行日志。
- `evidence.json` 记录源路径、独立扫描目录、已校验的摘要、工具版本、实际应用的豁免 ID、PII 状态、整体状态、未完成项和退出码。
- 默认调用完整静态 Tier 1 一次，包含 SkillSpector、Gitleaks 和 PII 等检查。评审器的非零退出码会原样返回。只有评审器通过、PII 通过且三条豁免得到验证时，入口才返回 0。预检失败或门禁自检失败时返回非零。
- 需要定位 SkillSpector 时，在同一命令追加 `-ScannerDiagnostics`。这会额外运行一次独立 SkillSpector，保存 `skillspector.json`、`skillspector.stderr.txt` 和对应退出码。此诊断选项独立于正式门禁。

```powershell
& ./qa/run-tier1.ps1 @qa -Package ../release/traced-template-fill-offline-win64.zip -ScannerDiagnostics
```

历史包 `8d5df8e6…` 的上游扫描器与项目修复版对照见 [固定输入的归档扫描记录](skillspector-zip/README.md)。不同 SHA-256 的报告各自独立。每次评审都以实际产物生成 `evidence.json` 及带包摘要的 `release-review.md`；表中的安全结论仅适用于报告标明的源码包或完整包。

维护入口时，可从仓库根目录执行 `python -m unittest discover -s qa -v`，检查双层摘要、重复运行的证据保留和部署树的快照边界。

## 封存的新变体与独立复评

生成器需要 `python-docx` 和 `openpyxl`。从仓库根目录运行下面的命令。公开目录仅含合成来件、程序清单和审核委托。种子、逐格答案和 SHA-256 封存在另一目录。正式评测前将各题的 SHA-256 单独交给保管人；评测后再公开种子。指定 `--seed 整数` 可复现读数、列序与题目，Office 文件的保存时间可能变化。

```text
python qa/blind_variants.py generate public-inputs operator-key --cases 2
python qa/run_blind_variants.py public-inputs operator-key trials --kit FROZEN_SKILL --repeats 5 --jobs 2
```

拆分包安装方式使用 `--manifest SHA256SUMS.txt --installer deploy.py`。可用 `--model 模型名` 统一各次评测模型。每次使用新项目和 Claude Code 会话，操作员提供预先写好的合成委托。首次答复停在已有依据的问题时，仅再提醒一次。原始会话日志、耗时、工具调用、实际草稿及各次评分均保存在 `trials`，包括失败的运行。复核工具记录可确认代理实际读取的材料。此工作流将答案移出项目，同一操作系统账户仍可能访问同一磁盘。

两个基准各有 33 个实测格，其中 18 格有依据可填写，15 格需留空。比较器重新打开生成的 DOCX，只接受数值相等的写法。错填、漏填、正确填写和正确留空分别计数。有效填写率为正确填写数除以应填数。一份空模板的有效填写率为零。每次要求错填为零、有效填写率至少 95%、观察到最后一次成功出稿后最多五次工具调用；未交稿或缺少调用证据会在汇总中单列。正式报告应列最差一次。

独立审核人可用相同表形的自备材料取代合成用例。先复制模板，在副本中填好实测值，将测量表放在正文首表，使用两行表头，首行含合并的“实测值”组，次行含唯一的样品编号。助手执行任务前，由审核人独立封存答案，保管人保存摘要。只把原始空模板、来源和审核约定交给代理。评测后在操作员目录运行下列命令。使用生成题时可加 `--inputs 来件目录` 同时验证输入文件摘要。

```text
python qa/blind_variants.py seal 审核人填写的副本.docx 私存/case.json --name case
python qa/blind_variants.py score 私存/case.json 保管人副本/case.sha256 实际草稿.docx
```
