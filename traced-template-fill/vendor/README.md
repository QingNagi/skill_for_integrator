# 离线依赖与许可证

完整包随附 CPython 3.14、当前平台的 PDF 依赖、Tesseract OCR 可执行文件及简体中文和英文模型。Python 库在首次使用 PDF 功能时从已校验的 wheel 解压到用户缓存。阅读文件、渲染与识别均在本机执行。Linux 的 Pillow wheel 要求 glibc 2.27 或以上，其他系统库要求还取决于所用的 CPython 发行包。

| 组件 | 固定版本 | 用途和上游许可 |
|---|---|---|
| pdfplumber、pdfminer.six | 0.11.11、20260107 | 坐标、版面及表格。两者均为 MIT |
| pypdfium2 | 5.15.2 | 页面栅格化。Python 绑定为 Apache-2.0 或 BSD-3-Clause，PDFium 及其依赖的独立声明随 wheel 提供 |
| Pillow | 13.0.0 | 图像转换。MIT-CMU；wheel 内保留编解码器的各自声明 |
| pypdf、typing_extensions | 6.21.4、4.16.0 | 纯文本后备。BSD-3-Clause 与 PSF-2.0。lite 包包含这两项 |
| charset-normalizer、pycparser | 3.5.3、3.12 | 文本编码与 C 类型解析。MIT 与 BSD-3-Clause |
| cryptography、cffi | 50.0.2、2.1.1 | 加密 PDF 所需组件。Apache-2.0 或 BSD-3-Clause，以及 MIT-0；原生依赖声明见 licenses/ |
| Tesseract、Leptonica | 5.5.5、1.87.0+git.c493a776 | 本地 OCR 和像素读取。Apache-2.0 与 BSD-2-Clause |
| tessdata_fast | 87416418657359cb625c412a48b6e1d6d41c29bd | chi_sim、eng 语言模型。Apache-2.0 |

pypdfium2 和 Pillow 的上游 wheel 含有完整的第三方声明，以及上游多许可项目的可选条款。FreeType 在本发行包中按 FreeType License 分发。Python wheel 保持原样，PDFium 的第三方声明在其 wheel 的 *.dist-info/licenses/BUILD_LICENSES/ 目录。FreeType、Independent JPEG Group、OpenSSL、libffi 和 OCR 编译运行库的声明见 licenses/Native-runtime-notices.txt 和同目录的许可文件。

## 打包输入

wheels.lock.json 固定原始 PyPI 文件名、字节数、SHA256 和下载路径。SHA256SUMS 使用相对于 vendor/ 的文件名。ocr.lock.json 固定可执行文件、模型、源码和本次使用的编译工具包。完整包内的 runtime/ocr/tesseract 或 tesseract.exe 含识别引擎和 Leptonica，runtime/tessdata/ 保存模型及来源。SBOM 随各完整包提供。

维护者在有网构建机的仓库根目录执行以下命令。交付给用户的发行包已经包含这些输入。

```text
python tools/fetch_vendor.py --platform all --mirror pypi
python tools/audit_vendor.py --platform all --check-pypi
python tools/build_ocr_runtime.py --platform win_amd64 --zig PATH_TO_ZIG_0_14_1
python tools/build_ocr_runtime.py --platform manylinux_x86_64 --zig PATH_TO_ZIG_0_14_1
python tools/build_ocr_runtime.py --platform manylinux_aarch64 --zig PATH_TO_ZIG_0_14_1
```

构建脚本从固定标签获取并校验 Tesseract 与 Leptonica 源码。构建机 Python 需安装 Pillow 13.0.0，将九张内嵌 TIFF 字体图转换为 OCR 引擎支持的 PBM，逐像素核对后用 Zig 0.14.1 交叉编译。也可预置已校验的两个源码压缩包至 .build/ocr-build/。模型和解释器由对应平台打包工具准备。wheel 与 OCR 程序分别放在 .build/vendor/py/、.build/vendor/wheels/ 和 .build/vendor/ocr/。打包工具校验发布清单，拒绝摘要不符的输入。

[PDFium 绑定的许可说明](https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing) · [Tesseract](https://github.com/tesseract-ocr/tesseract/tree/5.5.5) · [Leptonica](https://github.com/DanBloomberg/leptonica/tree/c493a776e6bc6cfb1d8d15152a0b74f3d15b33fd) · [tessdata_fast 模型](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd)
