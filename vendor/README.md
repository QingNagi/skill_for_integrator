# vendor/：离线依赖

目的：内网无法 pip 下载时，技能部署后即可读 PDF。两个纯 Python 依赖以 PyPI 发布的完整 wheel 分发，文件内保留上游源码、元数据和许可证。

## 目录

```text
vendor/
  README.md          本文件
  SHA256SUMS         所有固定轮子的 SHA256；目录只装了部分平台时请用下文的校验命令
  wheels.lock.json   打包、运行和审计共用的文件名、长度、SHA256 和 PyPI 路径
  py/                随所有离线包分发的 pypdf 6.21.4 与 typing_extensions 4.16.0 轮子
  licenses/          许可全文：PyMuPDF（AGPL-3.0）、pypdf（BSD-3-Clause）、typing_extensions（PSF-2.0）
  wheels/<平台>/     PyMuPDF 1.28.2 轮子，不进 git；用 tools/fetch_vendor.py 下载，或随离线包提供
  _unpacked/         轮子解压的备用位置，运行时自动生成，不入库
```

固定 `pymupdf==1.28.2`。PyPI 上更新的 2.x 依赖 numpy、onnxruntime、pymupdf-layout 等一串包，本技能不用。

## 平台与验证状态

| 平台目录 | 适用 | 验证状态 |
|---|---|---|
| win_amd64 | Windows 64 位（x86-64） | 已实机验证（2026-11-27，Python 3.15.2，用 `-S` 去掉系统 site-packages，只靠 vendor 读合成 PDF，PyMuPDF 与 pypdf 均读到文字） |
| manylinux_x86_64 | Linux x86-64，glibc 2.28 及以上 | 未实机验证，只下载并校验了 SHA256 |
| manylinux_aarch64 | Linux ARM64，glibc 2.28 及以上 | 未实机验证，只下载并校验了 SHA256 |
| macosx_x86_64 | macOS 10.15 及以上，Intel | 未实机验证，只下载并校验了 SHA256 |
| macosx_arm64 | macOS 11.0 及以上，Apple 芯片 | 未实机验证，只下载并校验了 SHA256 |

不在其中的平台（Alpine 等 musl 系 Linux、Windows ARM64、32 位系统）没有内置轮子，PDF 降级用 pypdf。PyPI 上有 musllinux 和 win_arm64 的轮子，本批没有收。

文件名、字节数、SHA256 见 `THIRD_PARTY_NOTICES.md` 和 `SHA256SUMS`。

## Python 版本

| Python | 使用随包分发的 PDF 引擎 |
|---|---|
| CPython 3.10 及以上 | 完整包优先用对应平台的 PyMuPDF 1.28.2。lite 包用 pypdf 6.21.4 读文本。|
| Python 3.9 | 用 pypdf 6.21.4 和 typing_extensions 4.16.0 读文本。|
| Python 3.8 | 核心文档功能可用。若需读 PDF，请升级 Python 或由部署方提供经评估的兼容后端。|

最后支持 Python 3.8 的 pypdf 5.9.0 有多条解析器安全公告，已从分发包移除。本次运行验证使用 Windows x64 上的 Python 3.15。其他版本的要求据 PyPI 元数据与 wheel 标签填写。

## 取得轮子

```text
python tools/fetch_vendor.py                     下载本机平台的轮子（默认先清华镜像，再 PyPI 官方，断点续传，SHA256 不符即丢弃）
python tools/fetch_vendor.py --platform all      下载全部 5 个平台
python tools/fetch_vendor.py --from-dir D        不联网：在目录 D（含子目录）里找同名轮子，校验后复制
python tools/fetch_vendor.py --verify            只校验
```

生成离线完整包（每个平台一个 zip，另有含两个纯 Python 轮子的 lite 包和 SHA256SUMS-<tag>.txt）：

```text
python tools/build_offline_bundle.py --out <目录> --tag v1.0
python tools/build_offline_bundle.py --pub <PUB 目录> --tag v1.0     输出到 PUB 同级的 dist/，供 GitHub Release 使用
```

发布时使用干净的技能目录。若只制作纯 Python 包，可用 `--platform lite`。构建会复核包内轮子的 SHA256。

## 加载顺序（scripts/dilib/_bootstrap.py）

1. 启动时不导入第三方库。读 PDF 时优先使用已安装的库；这些库及其版本由部署方管理。
2. 使用随附 PyMuPDF 时，先核对平台、解释器、文件名、长度和 SHA256，再从已验证的轮子解压。解压位置依次尝试 `DI_CACHE_DIR`、用户缓存目录和 `vendor/_unpacked`。复用前核对文件集与原始轮子 RECORD 的哈希，遇到损坏的缓存会记录原因并尝试其他位置。
3. 纯 Python 降级路径按需校验 wheel，直接从 wheel 加载 pypdf。在 Python 3.9 和 3.10 上还会核验 typing_extensions。缺失或校验失败会进入登记流程，可通过 `_bootstrap.notes()` 查看原因。
4. 环境变量 `DI_NO_VENDOR=1` 可跳过内置依赖。

## 独立审计

`python tools/audit_vendor.py --platform all --check-pypi` 对照 PyPI 的版本记录复核每个固定文件的长度、SHA256 和已发布公告。仅需离线校验时省略 `--check-pypi`。范围可选 `lite`、某个平台或 `all`，缺少选定轮子会返回失败。

独立静态分析应检查轮子展开后的代码。将下面的 `<新目录>` 换成技能目录之外尚不存在的目录。

```text
python tools/audit_vendor.py --platform win_amd64 --extract-to <新目录>
bandit -r <新目录>
skillevaluator pii-scan <新目录>
```

核查范围和告警说明见 [第三方声明](../THIRD_PARTY_NOTICES.md)。更新任何依赖时，重新获取发布元数据、复核摘要与公告，并在目标平台运行离线 PDF 读取。

## 许可

- PyMuPDF：AGPL-3.0 或 Artifex 商业许可，二选一。随附许可全文（`licenses/PyMuPDF-AGPL-3.0.txt`）、PyPI 源码包链接和说明（`THIRD_PARTY_NOTICES.md`）。轮子自带的 COPYING 只有一句话，不是许可全文，所以另附。
- 本文不是法律意见。本技能自己的许可如何与 AGPL 衔接，轮子随 Release 分发时是否还要随包附源码，需要发布者确认；不确定时的保守做法是只发 lite 包、让使用者自己运行 `fetch_vendor.py` 取轮子，或取得商业许可。
- pypdf 为 BSD-3-Clause，typing_extensions 为 PSF-2.0，许可文件在 `licenses/`。

## Windows x64 自部署套装

`tools/build_win64_agent_kit.py` 生成完整的独立技能 ZIP。包内含 Windows x64 嵌入式 CPython、已固定版本的 PDF 依赖，以及 Tesseract 简体中文和英文模型。目标机无需联网下载或调用 pip；旧格式转换和 PDF 导出使用目标机已安装的 Office/WPS/LibreOffice。首次识别通过 PyMuPDF 的本地 OCR 接口执行，模型位于技能的 `runtime/tessdata`。缺少模型或显式设置 `DI_OCR=off` 时，扫描内容登记为未读。使用步骤见完整套装根目录的 README.md。
