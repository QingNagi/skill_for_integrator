# 第三方组件声明

核对日期：2026-11-27。版本、文件名、大小、SHA256 来自 PyPI 官方元数据（https://pypi.org/pypi/<包名>/<版本>/json），下载后逐个重新计算过 SHA256。以下组件均为 PyPI 原样文件，未做任何修改。

## 1. PyMuPDF 1.28.2（不进 git 树，随离线包分发或用 tools/fetch_vendor.py 下载）

- 用途：读取 PDF 的文字、表格、图。可选；缺失时降级到 pypdf，再缺失则把 PDF 登记为“未读取”。
- 许可：AGPL-3.0 或 Artifex 商业许可，二选一（PyPI 元数据原文：Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License）。本技能附带的轮子按 AGPL-3.0 提供。
- 许可全文：vendor/licenses/PyMuPDF-AGPL-3.0.txt，取自 https://www.gnu.org/licenses/agpl-3.0.txt。轮子里自带的 COPYING 只有 64 字节的一句话，不是许可全文，所以另附。
- 对应源码（PyPI 源码包）：https://files.pythonhosted.org/packages/a3/fb/b6761fa2d5266f2cdb24c3b91f4023070ab7848381417678e7a289a1d52a/pymupdf-1.28.2.tar.gz ，87903557 字节，SHA256 5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249
- 项目页：https://pypi.org/project/PyMuPDF/1.28.2/
- 要求 Python 3.10 及以上（轮子标签 cp310-abi3）；3.9 上可使用随包 pypdf，3.8 的 PDF 读取需部署方提供兼容后端。
- 未做的核查：没有逐项核对轮子内捆绑的其他第三方库（如 MuPDF 自带组件）各自的许可。
- 本技能自身的许可如何与 AGPL 衔接，本文件不作结论，需要由发布者确认（见 vendor/README.md“许可”一节）。

| 平台目录 | 文件名 | 字节数 | SHA256 |
|---|---|---|---|
| win_amd64 | pymupdf-1.28.2-cp310-abi3-win_amd64.whl | 19826532 | ebd244918798502d7b4504c90410d1711a4d7675a32584ca30f1bab419ecbffe |
| manylinux_x86_64 | pymupdf-1.28.2-cp310-abi3-manylinux_2_28_x86_64.whl | 25762500 | 397d6715c1f0df7548a92d0afd8ce370fc48fa47aeefac16be2bc04a16a8227f |
| manylinux_aarch64 | pymupdf-1.28.2-cp310-abi3-manylinux_2_28_aarch64.whl | 25095554 | 3050a233dde1211efe89ada74e2add6238436434159f46097a1423aad2842545 |
| macosx_x86_64 | pymupdf-1.28.2-cp310-abi3-macosx_10_15_x86_64.whl | 24645079 | 5fc315b425ff1f7afdd1ea2f348205cb19b806767daae7ce4d64115799c2bae1 |
| macosx_arm64 | pymupdf-1.28.2-cp310-abi3-macosx_11_0_arm64.whl | 23875605 | 7113846b35dbf0a033f088e4f4fb543dabeb4b0b12c112966a1ca1ee2d5eacae |

## 2. pypdf 6.21.4

- 用途为纯 Python PDF 文本降级读取。完整 wheel 位于 `vendor/py/`，包含发行元数据、源码、上游署名与 BSD-3-Clause 许可，许可副本位于 `vendor/licenses/pypdf-LICENSE.txt`。
- 发行文件为 `pypdf-6.21.4-py3-none-any.whl`，410430 字节，SHA256 为 `33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c`。[PyPI 发行记录](https://pypi.org/pypi/pypdf/6.21.4/json)
- 支持 Python 3.9 及以上。已移除的 5.9.0 是支持 Python 3.8 的最后一个 5.x 版本，PyPI 在核对日列出 115 条影响该版本的公告记录，其中包括同一问题的不同标识。[5.9.0 公告](https://pypi.org/pypi/pypdf/5.9.0/json)
- 随包环境未包含可选 AES 解密库。读文本时的加密支持取决于部署环境。

## 3. typing_extensions 4.16.0

- 用于 Python 3.9 和 3.10 上的 pypdf 类型兼容。完整 wheel 位于 `vendor/py/`，许可为 PSF-2.0，副本位于 `vendor/licenses/typing_extensions-LICENSE.txt`。
- 发行文件为 `typing_extensions-4.16.0-py3-none-any.whl`，45571 字节，SHA256 为 `481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8`。[PyPI 发行记录](https://pypi.org/pypi/typing_extensions/4.16.0/json)

## 4. Windows x64 自部署套装

完整套装随附 Python Software Foundation 发布的 CPython 3.14.10 嵌入式发行版。许可证与来源记录保存在 `runtime/python/LICENSE.txt`、`runtime/python/SOURCE.md`。简体中文和英文模型来自 Tesseract 的 tessdata_fast 项目，Apache-2.0 许可证、源码提交与逐文件摘要在 `runtime/tessdata/LICENSE` 和 `runtime/tessdata/SOURCE.md`。两者位于部署载荷内，安装后可直接查阅。识别引擎使用随 PyMuPDF 发行的原生组件和相应许可信息。[CPython 发行页](https://www.python.org/downloads/release/python-31410/) · [Tesseract 模型来源](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd)

## 5. 独立核查记录

2026-11-27 使用 `tools/audit_vendor.py --platform all --check-pypi` 对照 PyPI 核实全部七个 wheel。上述三个版本的 PyPI 版本记录当日均未列出安全公告。该查询覆盖 PyPI 记录；原生二进制内部组件的漏洞与许可仍需要独立核查。

将 Windows x64 原生 wheel 和两个纯 Python wheel 原样展开，使用 Bandit 1.9.4 扫描 Python 源码，使用 SkillEvaluator 1.2.1 单独检查 PII。未添加扫描豁免。

| 组件 | Bandit 高 / 中 / 低 | 需保留的审核信息 |
|---|---:|---|
| pypdf | 11 / 2 / 116 | 高、中告警集中在 PDF 加解密模块，涉及旧格式使用的散列、流密码、分组模式和可选密码库导入。技能调用读取 API，密码实现由上游提供。|
| typing_extensions | 0 / 2 / 11 | 中告警来自解析字符串类型注解和前向引用的求值逻辑。|
| PyMuPDF（Windows wheel） | 1 / 0 / 199 | 高告警在 `get_tessdata()` 中的 shell 调用，命令参数来自固定的两个可执行文件名。技能的常规文本读取不调用该 OCR 配置函数。|

PII 扫描报告 1 个高级别分组（pypdf 源文件公开的作者署名联系方式），以及 5 个中级别分组（PDF 规范章节编号及 UTC 时区偏移量）。上游文件与许可保持原样。上述结果仅覆盖指定的展开文件与扫描器规则，不构成全平台二进制审计。复核命令见 [vendor 说明](vendor/README.md)。
