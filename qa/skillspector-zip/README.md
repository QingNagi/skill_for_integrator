# PyMuPDF 归档扫描修复

本目录供发布评审机使用。技能在内网安装、OCR 和文档处理时沿用原来的离线包。扫描器基于 NVIDIA SkillSpector `v2.13.7`，提交 `c323ad4c43e3c8f2de051572cc25b2711d11e648`。本项目使用三段数字版本号 `2.13.7001`，以兼容 SkillEvaluator 1.2.1 的报告解析。原始 JSON 中的 `metadata.distribution_build` 标明本仓库、修订号 `zipreview.1` 和上游提交。[上游源码](https://github.com/NVIDIA/SkillSpector/tree/c323ad4c43e3c8f2de051572cc25b2711d11e648)

## 在评审机运行

在仓库根目录打开 PowerShell 7.2 或更新版本。已验证 Python 3.13，另需可执行的 `skillevaluator` 和 `gitleaks`。首次运行安装脚本会核验工具摘要、创建独立环境并安装依赖，后续运行复用同一版本。可用 `-Python '完整的 python.exe 路径'` 选择解释器。

```powershell
$scanner = & ./qa/skillspector-zip/install.ps1
& ./qa/run-tier1.ps1 -Package ./dist/doc-integrator-offline-win64.zip -SkillSpector $scanner
```

首次在**完全离线的评审机**上安装时，先在相同系统、CPU 架构和 Python 版本的联网机器使用 `python -m pip download --only-binary=:all: --dest review-wheels ./qa/skillspector-zip/skillspector-2.13.7001-py3-none-any.whl`。带入全部 `review-wheels/` 后执行 `$scanner = & ./qa/skillspector-zip/install.ps1 -Wheelhouse ./review-wheels`，然后运行上例评审命令。SkillEvaluator 与 Gitleaks 沿用评审机已有安装。

评审入口优先使用显式指定的 `-SkillSpector`，其次为 `SKILLEVALUATOR_SKILLSPECTOR_PATH`。两者都未设置时，入口自动查找上述用户目录中的安装，再回退到 PATH。默认命令执行一次完整 Tier 1。需要逐文件的原始报告时，加上 `-ScannerDiagnostics`，入口会额外执行一次独立扫描。每轮报告保存在控制台打印的目录中。

## 修复范围

原版把 wheel 的前 16 MiB 传给 ZIP 解析器。正式包中的 PyMuPDF wheel 共 19,826,532 字节，中央目录从偏移 19,816,122 开始，原版据此报告 `archive_malformed`。修复后，识别出的 ZIP 在现有 25 MiB 归档输入上限和剩余总字节预算内，从同一个文件句柄取得完整归档。普通静态内容与模型可见的单文件前缀继续使用原上限。

归档尾部计入整包及递归扫描的字节预算。受预算限制只取得前缀时，扫描器记录实际大小和可用字节数；完整读取后仍然损坏的 ZIP 会保留解析失败。成员展开上限、检测规则、告警和发布门禁沿用上游设置。[上游资源限制](https://github.com/NVIDIA/SkillSpector/blob/v2.13.7/docs/ANALYSIS_RESOURCE_BOUNDS.md)

## 同一 Windows 离线包的验证

对照输入的 SHA-256 为 `8d5df8e65e7cc82c81342056b14dd8c61cc38b7dffd468f7e542ce1e944f55cf`。两次独立扫描使用相同的完整部署目录及 `--no-llm`。其中 PyMuPDF wheel 的 SHA-256 为 `ebd244918798502d7b4504c90410d1711a4d7675a32584ca30f1bab419ecbffe`。119 个成员全部通过 ZIP CRC 核对，`RECORD` 中 118 项带摘要的记录全部通过哈希与长度核对，剩余一项为 `RECORD` 自身。

| 整包扫描指标 | 上游 2.13.7 | 项目修复版 |
| --- | ---: | ---: |
| 原始扫描器退出码 | 2 | 1 |
| PyMuPDF 成员进入报告 | 0 / 119 | 119 / 119 |
| 致命分析异常 | 1 | 0 |
| 完整分析的组件 / 报告总组件 | 163 / 201 | 231 / 320 |
| HIGH 发现 | 660 | 678 |
| 抑制的发现 | 0 | 0 |

修复版展开 wheel 后，整包多识别 119 个组件，多完整分析 68 个。覆盖率的分母随之从 201 增至 320，百分比从 81.1% 变为 72.2%。46 个 wheel 成员触及解包预算，原生可执行内容仍只有有限的静态分析。原始建议为 **CRITICAL / DO_NOT_INSTALL**。

正式 SkillEvaluator 1.2.1 复核得到 **12 项通过、1 项失败**。失败项为 Security Scan。扫描器成功返回报告，Tier 1 收录了 678 条 HIGH，同时因部分组件覆盖不足将 SkillSpector 列入 `incomplete_scans`。发布资格为 `incomplete`。PII 已通过，质量评分为 100/A。与上游同包报告逐项对比，原有发现全部保留，新增 18 条 HIGH、9 条 MEDIUM 和 7 条 LOW。

5 个新增回归用例全部通过。在同一 Windows 主机运行上游上下文与归档测试，原版为 428 通过、6 失败、10 跳过，修复版为 433 通过、6 失败、10 跳过。两次失败的用例相同，5 项涉及 Windows 行尾，1 项涉及创建符号链接的权限。对干净的上游检出应用仓库中的补丁，重新构建得到的 wheel 与随附文件逐字节一致，SHA-256 为 `e88bd2b4caec0b1b42e31192458a16292b8d028a718917ed5765f7e5576ef28b`。

## 从源码复核

在仓库根目录记录补丁绝对路径，再检出指定的上游提交。以下命令使用已安装 Git、Python 3.13 的评审开发机和一个新目录。

```powershell
$patch = (Resolve-Path ./qa/skillspector-zip/zip-cache.patch).Path
git clone https://github.com/NVIDIA/SkillSpector.git skillspector-review-src
Set-Location skillspector-review-src
git checkout --detach c323ad4c43e3c8f2de051572cc25b2711d11e648
git apply --check $patch
if ($LASTEXITCODE) { throw '上游版本或补丁不匹配' }
git apply $patch
if ($LASTEXITCODE) { throw '应用补丁失败' }
py -3.13 -m venv ../review-build-env
$build = (Resolve-Path ../review-build-env/Scripts/python.exe).Path
& $build -m pip install hatchling==1.33.0 pytest .
& $build -m pytest -o addopts= -q tests/nodes/test_zip_cache_extent.py
& $build -m pip wheel --no-deps --no-build-isolation . -w ../review-dist
```

补丁包含扫描器源码变更、项目版本号和 5 个针对归档缓存的回归测试。wheel 内附上游 Apache-2.0 许可证，依赖声明见[上游第三方声明](UPSTREAM_THIRD_PARTY_NOTICES.md)。
