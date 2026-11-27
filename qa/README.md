# Windows 离线技能的完整 Tier 1 复核

[本轮五个交付包的 SHA-256、功能验收和三个 ZIP 的评审结果](2026-11-28-离线验收.md)

source 只含业务代码和资源；lite 增加纯 Python wheel；完整包包含解释器、原生 PDF 组件和 OCR 模型。请按实际产物分别复核。历史 Windows 发布包包含 PyMuPDF，复核该历史包时可在评审机使用项目修复版 SkillSpector。安装命令、同包对照结果及仍需复核的发现见 [PyMuPDF 归档扫描修复](skillspector-zip/README.md)。

在评审机的 PowerShell 7.2 或更新版本中，从仓库根目录调用 `qa/run-tier1.ps1`。评审机需有 SkillEvaluator、SkillSpector 和 Gitleaks。已验证 SkillEvaluator 1.2.1、Gitleaks 8.30.1、上游 SkillSpector 2.13.6 / 2.13.7 和本项目的修复版。入口支持指定工具绝对路径。未指定扫描器时，优先使用安装在用户目录的项目修复版，再查找 PATH。静态评审使用 `--no-llm`。

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
