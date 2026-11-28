# 1.2.0 源码 ZIP 的静态安全复核

2026-11-28，`traced-template-fill-1.2.0-source.zip` 的 13 项 Tier 1 检查全部通过，门禁退出码为 0。包的 SHA-256 为 `eb6238b03ed26faffbbaa8a6c270f94bda224eb0864bf7129f206cc3bb506064`。独立 SkillSpector 仍建议 `CAUTION`。结论只覆盖这个源码 ZIP。

| 指标 | 官方报告 |
| --- | --- |
| SkillSpector 分析完整性 | `complete`，62/62，100%，未分析项、例外和缺失引用均为 0 |
| 无基线严重性 | CRITICAL 0，HIGH 0，MEDIUM 14，LOW 0 |
| 无基线风险分 / Tier 1 路径适用性前 / 后 | 45 / 45 / 45（满分 100） |
| Tier 1 保留 / 依路径过滤 / 基线抑制 | 14 / 0 / 0 |
| 质量与宿主 | A，97.5 分。6 个静态加载配置均无 `not_loaded`。OpenClaw 配置有一个 `degraded`，小上下文时读取长说明可能截断 |
| 发布资格 | `incomplete`，缺 Tier 2 和 Tier 3 所需证据 |

评审运行使用官方 SkillEvaluator 1.2.1、SkillSpector 2.13.7 和 Gitleaks 8.30.1，指定前一版本 1.1.2、关闭 LLM，仅从 ZIP 内容暂存并扫描。已将本地评估器的 382 个包文件与 PyPI wheel 逐一比对，扫描器的 169 个包文件与上游标签比对（统一 CRLF），并将 Gitleaks 可执行文件与官方 ZIP 比对。工具来源、每份报告的哈希和技能包内的定位见索引。[1.2.0 审计索引](source-review.json) · [评审入口](../../run-tier1.ps1)

### 本轮的 14 条发现

| 规则 | 数量 | 现场核查 |
| --- | ---: | --- |
| AST4 | 6 | 批量草稿进程、LibreOffice 版本探测、PowerShell 转换、LibreOffice 转换、Tesseract OCR、Wordconv 转换。调用确实启动子进程，参数列表和超时均保留在源码中 |
| TT2 | 1 | Wordconv 从 `os.environ` 取 12 个命名变量，并重设工作目录的 TEMP/TMP 和系统 PATH。扫描器检测到真实的环境传递 |
| LP3 | 1 | SKILL.md 声明本地文件和命令范围，缺少扫描器识别的预授权工具字段。实际权限由宿主与用户决定 |
| AE4 | 6 | 文件级混合文字命中位于电气量别名、说明和匹配字符集。逐项列出原符号、行号、文件摘要与上游工具生成的精确指纹，待独立复核 |

上述 14 条均保留在正式报告内。6 条 AE4 的候选记录没有签署人、负责人或授权到期日。其余 8 条反映真实功能或宿主权限边界，没有提出豁免。[逐项证据与复审条件](source-review.json)

## 1.1.2 的三个覆盖缺口

原始扫描器的分析台账将三个第三方文件标为部分分析。三者都是静态规则分析器 `static_patterns_tool_misuse` 的 `static_parse_limit`，具体为命令词边界无法解析（`boundary_unresolved / command_word_boundary`）。解析器报告的跨度为每个文件的第 186–311 行。文件大小和字符编码均未被列为原因。

| 1.1.2 源码包文件 | 1.2.0 处理 |
| --- | --- |
| `vendor/licenses/LLVM-libcxx-LICENSE.txt` | 许可随含对应第三方组件的交付件分发 |
| `vendor/licenses/LLVM-libcxxabi-LICENSE.txt` | 同上 |
| `vendor/licenses/LLVM-libunwind-LICENSE.txt` | 同上 |

另外五条 `reference_missing` 来自旧 SKILL.md 第 26、40、42、43、78 行。旧包的图片另记为分析器范围外项目。以上记录可按原件摘要复查。[1.1.2 逐项索引](../second-review/official-scan-index.json)

## 原始发现和豁免

1.1.2 版索引中已核对语境的 39 条为 1 条 P6（数值精度注释）、1 条 AST3（用固定模块名查 xlrd）和 37 条 EA3（第三方许可措辞）。原始报告中 AST3 为 MEDIUM，37 条 EA3 为 LOW。1.2.0 已修改精度注释并用静态导入检查 xlrd，第三方许可随相应的软件组件交付。源码 ZIP 的活跃基线不需要沿用这 39 条。[历史审阅记录](../second-review/official-security.md)

仓库外部基线的两张列表均为空。第三轮保留每个命中的真实路径、严重性及上下文。列为待复核的项目不产生豁免。这次源码评审中，既有的三条例外只适用于历史运行时的 CPython 许可证 PII 检查，实际应用数为零。[当前基线](../../skillspector-baseline.yaml)

凡提出接受某一命中，应选定证据绑定的指纹，在登记表中补全两位独立复核人的意见、负责人、有效期，另行提交基线和摘要变更。文件内容或扫描器版本变化时复核失效。涉及子进程和权限的项目还需重新确认程序定位、调用参数和宿主权限。覆盖缺口和宿主授权独立核查。[上游指纹规范](https://github.com/NVIDIA/SkillSpector/blob/c323ad4c43e3c8f2de051572cc25b2711d11e648/docs/SUPPRESSION.md)

## 评分口径

`skillspector.json` 来自独立的无基线扫描。`skillevaluator-tier1.json` 中 Security Scan 的 `skillspector_baseline` 记录显式基线被加载、匹配和压制了多少发现。`skillspector_applicability` 随后按文件角色判定适用性，`raw_risk_score` 是该次扫描的报告分数，`applicable_risk_score` 是评估器用于门禁的分数。两者的差值不表示人工接受数。SkillEvaluator 1.2.1 在适用分超过 50 且没有原生 HIGH 或 CRITICAL 时，增加一条聚合风险 HIGH。Tier 1 只给出静态检查结论，NVIDIA 发布资格单列。[Tier 1 参考](https://docs.nvidia.com/skills/skillevaluator/tier1-reference) · [发布资格](https://docs.nvidia.com/skills/skillevaluator/reports)
