# 官方静态安全评审

2026-11-28，1.1.2 交付目录的三个 ZIP 均未通过 Security Scan。每包 13 项 Tier 1 校验中有 12 项通过。三个报告的发布资格均为 `incomplete`，原始安装建议均为 `DO_NOT_INSTALL`。本次人工分类没有变更严重性或批准发布。

| ZIP | SHA-256 | HIGH / MEDIUM / LOW | 风险分 | 完整分析组件 / 分母 | 覆盖率 |
|---|---|---:|---:|---:|---:|
| `traced-template-fill-review-source.zip` | `359d121e897c5ade85ad8e2e6de884e153cc81214ec1ead7cb5ea91f66fa7a29` | 1 / 15 / 38 | 92/100 | 75 / 78 | 96.2% |
| `traced-template-fill-review-lite.zip` | `9694f347f8f5662833776cfb7d4431bccfb12ac0e9ff9436cbc6d80d83cf8482` | 68 / 19 / 99 | 100/100 | 147 / 150 | 98.0% |
| `traced-template-fill-offline-win64.zip` | `954050d4c79275b701fa28e29f32fa21a049dc25ad9bca41fbffeaff4d9c4ae1` | 480 / 17 / 46 | 100/100 | 104 / 927 | 11.2% |

逐项索引保留包哈希、SkillSpector 发现 ID、规则、严重性、包内路径和行号，并关联 Tier 1 的发现 ID。相同规则在不同位置重复触发时逐条计入。原始报告摘要用于核对留存副本，报告中的本机路径和时间戳会随复跑变化。[逐条 JSON 索引](official-scan-index.json)

## 源码包

源码 ZIP 中有 79 个条目。qa、tests、tools、dist 和 .git 目录共有 0 个条目。54 条原始发现的人工分类如下。

| 分类 | 数量 | 核查结果 |
|---|---:|---|
| 技能运行代码 | 13 | 子进程调用、环境传递、动态属性访问及文件级字符告警仍待复核。索引给出各条命中用途。 |
| 文档措辞 | 2 | 工具权限声明及规则说明中的混合字符仍待复核。 |
| 开发与发布工具 | 0 | 依据发布 ZIP 的实际内容统计。 |
| 经命中上下文核对的误报 | 39 | 数值显示精度注释触发 P6；固定模块的可用性查询触发 AST3；第三方许可中的法律措辞触发 EA3。 |

源码的 HIGH 位于 `scripts/dilib/testdata.py` 的 Decimal 显示精度处理。其上下文记录实测值的小数位数。部分 AE4 只给出文件级位置，索引记下可见的兼容字符及仍待复核的状态。上述判断保留原始告警；本轮没有新增安全风险接受记录。

## 精简包和 Windows 包

精简包包含第三方 wheel。Windows 包的 480 条 HIGH 中，475 条来自嵌套可执行内容标记 SC9，3 条来自未完整分析的引用 AE1。YR4 匹配本地 Tesseract 可执行文件的可读字符串，另有一条源码 P6。其中 440 条 SC9 位于 CPython 标准库归档内。二进制命中及分析缺口保留为待复核。三个包均有 `skillspector` 被列入未完成扫描。

1.1.2 与上一份 Windows 候选的 runtime/、vendor/py/ 共 49 个文件逐个 SHA-256 相同。两次诊断在同一个 python314.zip 内分别记下 440 和 439 条 SC9。覆盖率和组件数量见各自报告。

PII 校验在三个包中均通过。Windows 原样应用既有的三条 CPython 许可文字例外，有效期至 2027-05-27。这些例外仅用于 PII 检查。三个独立 SkillSpector 诊断均保留原始发现。[许可例外及有效期](../../pii-waivers.yaml)

## 本轮较早候选

下列较早候选也以官方 2.13.7 扫过，各包均未通过 Security Scan。1.1.2 的结论见文首对应哈希。

| 历史候选 ZIP | 历史 SHA-256 | HIGH / MEDIUM / LOW | 完整分析 / 分母 |
|---|---|---:|---:|
| `traced-template-fill-review-source.zip` | `f9118cc4f950a55a00f932bee9a2a6865f2710d72399739d132d05eb924b7abc` | 1 / 14 / 40 | 77 / 80 |
| `traced-template-fill-review-lite.zip` | `1409106495f2629c149d05eef941c90be3d540fccc2fc9912e8037b00856dfbe` | 68 / 18 / 101 | 149 / 152 |
| `traced-template-fill-offline-win64.zip` | `0052f3e799abcdc95aba75beae249ecbc825bfdfe66612fb3f847cca51371154` | 473 / 16 / 48 | 105 / 936 |
| `traced-template-fill-review-source.zip` | `03c3ac2dc9c9131ca04753618ea4f4ca5d6c71a3670bfff64edcf009b70ce1bb` | 1 / 15 / 38 | 75 / 78 |
| `traced-template-fill-review-lite.zip` | `11460d881f739c6ce8ccc419dbe021f0ec4e3217812de76f410cbe51c8f8ddc6` | 68 / 19 / 99 | 147 / 150 |
| `traced-template-fill-offline-win64.zip` | `206e6b5dbb668ecc4dade532b1d852d45bb0a09c57969a760cbe25c9deed40af` | 485 / 17 / 46 | 104 / 923 |
| `traced-template-fill-review-source.zip` | `b3ae463a9b27d8accfbd87cfb7d3e2818382fd52e4d5e66bd07575b2e268aa99` | 1 / 15 / 38 | 75 / 78 |
| `traced-template-fill-review-lite.zip` | `81baf86a2e98a957f831cf5697f4dd7837f487d88c8e76eed28def37dbc51582` | 68 / 19 / 99 | 147 / 150 |
| `traced-template-fill-offline-win64.zip` | `eae3e6e00ead532f0b1bed88dca639ca2da88c12391e67b431236f5d8eb486ce` | 480 / 17 / 46 | 105 / 928 |

## 历史 Windows 覆盖率

| 对照包 SHA-256 | 扫描器 | 完整分析 / 分母 | 覆盖率 |
|---|---|---:|---:|
| `8d5df8e65e7cc82c81342056b14dd8c61cc38b7dffd468f7e542ce1e944f55cf` | 项目修复版 2.13.7001 | 231 / 320 | 72.2% |
| `b41419b16958dc316fa8b141cb0ba5f63369830f660e3834f8b666548efc285d` | 项目修复版 2.13.7001 | 97 / 918 | 10.6% |
| `c7b2e28fe4f3c977921dc40c3243aa8ed207634d2f1c164d1b00825b5285745f` | 项目修复版 2.13.7001 | 96 / 918 | 10.5% |

较早的包带 3 个 wheel。后续的包从 PyMuPDF 切换到 pdfplumber、pdfminer、PDFium 等 10 个 wheel，并加入原生 OCR。两个最终历史报告来自同一修复版扫描器。按不同路径计数，达到归档大小上限的条目由 46 个增加到 783 个，标为不透明内容的路径由 47 个增加到 784 个。两类标志可以落在同一条路径。后一个报告中，pdfminer 的 188 条、Pillow 的 119 条、cryptography 的 119 条等均受到归档大小限制；这使进入统计的组件增多，完整分析的数量下降。

97/918 对应中间包的 10.6%。前一轮最终发布评审所列 10.5% 对应另一哈希的 96/918。表首的本轮测量使用官方 2.13.7，并列出当前包新的分子、分母。早期 PyMuPDF 包在同一输入上用上游 2.13.7 测得 163/201，归档解析曾出现致命异常。[早期同包扫描对照](../../skillspector-zip/README.md) [前一轮发布记录](../../2026-11-28-数据核对与全流程复验.md)

## 复核方法

平台为 Windows 10 19045、PowerShell 7.6.9。使用 SkillEvaluator 1.2.1（Python 3.14.4）、SkillSpector 2.13.7（Python 3.13.18）和 Gitleaks 8.30.1。SkillSpector 安装目录中的 169 个包文件与上游标签对应文件逐一核对，统一 CRLF 后均一致，且没有额外的非缓存文件。[上游标签提交](https://github.com/NVIDIA/SkillSpector/commit/c323ad4c43e3c8f2de051572cc25b2711d11e648)

从项目根目录指定本机安装的官方工具，对照上表选择 ZIP 及其 SHA-256。三个 ZIP 分别执行一次。脚本先验证外层哈希；Windows 套装还校验内部 payload，再纳入部署代码和额外的启动包装文件。Tier 1 和独立诊断均禁用 LLM。

```powershell
$evaluator = (Get-Command skillevaluator).Source
$inspector = (Get-Command skillspector).Source
$gitleaks = (Get-Command gitleaks).Source
& $inspector --version
& ./qa/run-tier1.ps1 -Package $zip -PackageSha256 $sha256 -Evaluator $evaluator -SkillSpector $inspector -Gitleaks $gitleaks -ScannerDiagnostics
```

本轮三个 Tier 1 退出码均为 1。复核运行生成的 `skillevaluator-tier1.json` 和 `skillspector.json` 可按规则、路径、行号及发现 ID 与公开索引交叉核对。[评审入口](../../run-tier1.ps1)
