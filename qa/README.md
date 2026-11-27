# Windows 离线技能的完整 Tier 1 复核

复核包含 PyMuPDF 的 Windows 发布包时，可在评审机使用项目修复版 SkillSpector。安装命令、同包对照结果及仍需复核的发现见 [PyMuPDF 归档扫描修复](skillspector-zip/README.md)。下文保留上游原版扫描器的历史结果。

在评审机的 PowerShell 7.2 或更新版本中，从仓库根目录调用 `qa/run-tier1.ps1`。评审机需有 SkillEvaluator、SkillSpector 和 Gitleaks。已验证 SkillEvaluator 1.2.1、Gitleaks 8.30.1、上游 SkillSpector 2.13.6 / 2.13.7 和本项目的修复版。入口支持指定工具绝对路径。未指定扫描器时，优先使用安装在用户目录的项目修复版，再查找 PATH。静态评审使用 `--no-llm`。

**2026-11-27 实测。** Windows ZIP 的 SHA-256 为 `6650f0a6ecb429360b4ca152485d6947d21a2b57da091ce65a7c2a491f6dbfe2`。13 项验证中 12 项通过，PII 精确应用 3 条许可证豁免，质量分 100/100（A）。Security Scan 的 SkillSpector 退出码为 2，发布资格为 `incomplete`。PyMuPDF wheel 的完整字节数为 19,826,532，超过扫描器单文件缓存的 16 MiB，其 ZIP 中央目录从偏移 19,816,122 开始。完整 wheel 的 119 个条目通过 CRC 校验。

同一发布包的 SkillSpector 2.13.6 原始结论为 `CRITICAL / DO_NOT_INSTALL`，扫描覆盖率 77.3%。660 条 HIGH 中，592 条定位到运行时，67 条定位到第三方依赖，1 条定位到 `SKILL.md` 对解释器的引用。扫描器对该解释器只做了部分分析。发布审核仍需处理扫描覆盖与安全告警。[性能与新环境迁移验收](性能与迁移.md)

## 评审发布 ZIP

发布 ZIP 与其同名 `.sha256` 文件放在一起。若持有单独取得的发布摘要，可加 `-PackageSha256 <64位摘要>`。入口先校验外包和内层 `payload.zip`，再展开真实交付内容。外层启动器和文档会与内层对应文件逐一比对。没有对应文件及内容不同的文件一并纳入扫描。

```powershell
$qa = @{
    OutputDirectory = (Join-Path $HOME 'Documents/doc-integrator-reviews')
    Evaluator = (Get-Command skillevaluator -ErrorAction Stop).Source
    SkillSpector = (Get-Command skillspector -ErrorAction Stop).Source
    Gitleaks = (Get-Command gitleaks -ErrorAction Stop).Source
}
& ./qa/run-tier1.ps1 @qa -Package ./dist/doc-integrator-offline-win64.zip
$LASTEXITCODE
```

工具装在独立环境时，将上面相应的工具值写成绝对路径。工具均在 PATH 中时也可以运行简写。

```powershell
& ./qa/run-tier1.ps1 -Package ./dist/doc-integrator-offline-win64.zip
```

## 评审已部署的技能

传入含 `SKILL.md` 的实际技能目录，可用项目或用户安装位置。入口识别含 `payload.zip` 的启动目录，也支持含 `runtime/python/` 的已展开目录。它在独立位置建立快照，保留已部署的规则和本地文件。输出目录应位于技能目录外。若部署树内部使用链接，请选用完整物理文件树，或评测对应的发布 ZIP。

```powershell
& ./qa/run-tier1.ps1 @qa -InstalledDirectory 'D:\项目\.claude\skills\doc-integrator'
$LASTEXITCODE
```

每次评审在 `-OutputDirectory` 下创建独立子目录。默认根目录为系统临时目录下的 `doc-integrator-reviews`。控制台输出本次报告的绝对路径。快照位于 `stage/doc-integrator/`，审核过的豁免文件位于同级的 `stage/pii-waivers.yaml`。SkillEvaluator 以豁免文件的父目录解析文件位置，重跑时应保持这两个相对位置。

## 三项 CPython 许可证豁免

入口核对实际许可证的 SHA-256 和审核过的 YAML 摘要，按 UTC 日期检查 2026-11-27 至 2027-05-27 的有效期，包括首尾两天。任一项失配时，继续执行全套评审，保留全部 PII 发现并返回非零退出码。评审完成后还会逐个核对实际应用的三个豁免 ID。

| 许可证行号 | 精确豁免 ID | 原因 |
| --- | --- | --- |
| 359 | `CPYTHON-31410-LICENSE-BZIP2` | bzip2 版权署名中的公开邮件地址 |
| 631 | `CPYTHON-31410-LICENSE-DFARS-7014` | DFARS 条款编号 252.227-7014 |
| 672 | `CPYTHON-31410-LICENSE-DFARS-7013` | DFARS 条款编号 252.227-7013 |

三个豁免都限定在 `doc-integrator/runtime/python/LICENSE.txt` 的指定行、类型和匹配值摘要。负责人、理由和日期见 [pii-waivers.yaml](pii-waivers.yaml)。该文件的 UTF-8、LF 规范化摘要为 `8b6c47fc52d7400d9b9c67b7b011998eb99f6c4f27d73f449206c48c430e9e2c`。更新 CPython、证据或有效期时，应先对新的原件和原始 PII 命中做复核，再更新对应记录及入口中的校验值。

上游原件为官方 `python-3.14.10-embed-amd64.zip`，SHA-256 为 `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`。已逐字节比对其 LICENSE.txt，35,407 字节，SHA-256 为 `935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68`。入口使用这一固定摘要进行离线核对。[CPython 3.14.10 官方发布页](https://www.python.org/downloads/release/python-31410/)

## 报告和退出码

- `skillevaluator-tier1.md` 与 `skillevaluator-tier1.json` 包含原始完整 Tier 1 结果，`tier1.log` 保存运行日志。
- `evidence.json` 记录源路径、独立扫描目录、已校验的摘要、工具版本、实际应用的豁免 ID、PII 状态、整体状态、未完成项和退出码。
- 默认调用完整静态 Tier 1 一次，包含 SkillSpector、Gitleaks 和 PII 等检查。评审器的非零退出码会原样返回。只有评审器通过、PII 通过且三条豁免得到验证时，入口才返回 0。预检失败或门禁自检失败时返回非零。
- 需要定位 SkillSpector 时，在同一命令追加 `-ScannerDiagnostics`。这会额外运行一次独立 SkillSpector，保存 `skillspector.json`、`skillspector.stderr.txt` 和对应退出码。此诊断选项独立于正式门禁。

```powershell
& ./qa/run-tier1.ps1 @qa -Package ./dist/doc-integrator-offline-win64.zip -ScannerDiagnostics
```

历史 Windows 发布包中，SkillSpector 2.13.6 和 2.13.7 都对 PyMuPDF wheel 报告过 `size_limit` 与 `archive_malformed`。当时 19,826,532 字节的 wheel 超过扫描器的 16,777,216 字节缓存，导致缓存前缀缺少 ZIP 中央目录。具体版本的完整性、告警和风险建议以本次报告为准。

2026-11-27，对 SHA-256 为 `8d5df8e65e7cc82c81342056b14dd8c61cc38b7dffd468f7e542ce1e944f55cf` 的 Windows 完整包运行 SkillEvaluator 1.2.1。Tier 1 共 13 项，12 项通过、0 项失败、1 项未完成。三条上游许可证 PII 豁免精确命中，质量评分为 100/A。独立使用 SkillSpector 2.13.6 对同一快照复核，覆盖率 77.6%，660 个 HIGH，未抑制任何发现，原始建议为 **CRITICAL / DO_NOT_INSTALL**，发布资格仍为 **INCOMPLETE**。

本次 PyMuPDF wheel 与 PyPI 发布的原件 SHA-256 相符。660 个 HIGH 中，592 项关联运行时，67 项关联 vendor 下的依赖，1 项关联 SKILL.md 引用对象的分析不完整。正式部署前，应对未覆盖的原生组件做全量供应链审查，逐项处置高风险发现，按组织的发布标准记录结论。哈希核验和业务测试各自验证了文件身份和运行结果。[NVIDIA 扫描容量说明](https://github.com/NVIDIA/SkillSpector/blob/v2.13.7/docs/ANALYSIS_RESOURCE_BOUNDS.md)

维护入口时，可从仓库根目录执行 `python -m unittest discover -s qa -v`，检查双层摘要、重复运行的证据保留和部署树的快照边界。
