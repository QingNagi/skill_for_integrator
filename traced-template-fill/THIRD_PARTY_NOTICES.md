# 第三方组件声明

本声明对应 2026 年 11 月 28 日核对的离线包。各组件保留上游版权和许可。每个平台的包内清单记录实际交付的文件、来源及 SHA256。文件名、版本、大小和摘要可在 [Python wheel 清单](vendor/wheels.lock.json) 和 [OCR 清单](vendor/ocr.lock.json) 查阅。

## Python 与 PDF 阅读

Windows 包使用 Python Software Foundation 发布的 CPython 3.14.10 amd64 嵌入式发行版。Linux x86_64 和 aarch64 包使用 Astral 发布的 CPython 3.14.10 可迁移发行版。完整包保存上游解释器及随附组件的许可。有关来源请参阅 [Python 官方发行页](https://www.python.org/downloads/release/python-31410/) 和 [Astral 上游发行页](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026)。

| 随附组件 | 许可 |
|---|---|
| pdfplumber 0.11.11、pdfminer.six 20260107、charset-normalizer 3.5.3 | MIT |
| pypdf 6.21.4、pycparser 3.12 | BSD-3-Clause |
| typing_extensions 4.16.0 | PSF-2.0 |
| pypdfium2 5.15.2 | 绑定可选 Apache-2.0 或 BSD-3-Clause；随附 PDFium 及子组件各自的许可 |
| Pillow 13.0.0 | MIT-CMU；随附图像编解码器各自的许可 |
| cryptography 50.0.2 | 可选 Apache-2.0 或 BSD-3-Clause；所含 OpenSSL 许可另附 |
| cffi 2.1.1 | MIT-0；所含 libffi 许可另附 |

每个 Python wheel 以 PyPI 发布的原文件交付，归档内保留作者署名、发行元数据和许可全文。PDFium 的详细声明在其 wheel 的 .dist-info/licenses/BUILD_LICENSES/ 目录中。Pillow 的许可证文件包括其二进制组件的声明。本产品使用 FreeType Project 的软件，按 FreeType License 分发；部分功能基于 Independent JPEG Group 的工作。详见 [依赖及许可索引](vendor/README.md) 和 [原生组件声明](vendor/licenses/Native-runtime-notices.txt)。

## 离线文字识别与模型

识别引擎 Tesseract 5.5.5 使用 Apache-2.0，图像库 Leptonica 1.87.0+git.c493a776 使用 BSD-2-Clause。每个平台的可执行文件由固定的两份源码压缩包和 Zig 0.14.1 编译生成。编译运行库的署名与许可随本包提供。[Tesseract 许可](vendor/licenses/Tesseract-LICENSE.txt) · [Leptonica 许可](vendor/licenses/Leptonica-LICENSE.txt) · [编译运行库索引](vendor/licenses/Native-runtime-notices.txt)

简体中文和英文语言模型 chi_sim.traineddata、eng.traineddata 取自 tessdata_fast 的固定提交 87416418657359cb625c412a48b6e1d6d41c29bd，按 Apache-2.0 分发。模型和上游 LICENSE 位于完整包内的 runtime/tessdata/。[模型上游版本](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd)
