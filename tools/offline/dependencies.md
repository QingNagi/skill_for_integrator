# 离线 PDF 文本依赖

这两个 wheel 适用于已有 Python 3.10 或更高版本的主机。pypdf 提供 PDF 文本读取，typing_extensions 提供类型兼容支持。每个 wheel 的字节数和 PyPI 来源路径见 wheels.lock.json，SHA256SUMS 记录它们的 SHA-256，两个 LICENSE.txt 文件保留上游许可。

解开后可以从本目录运行以下命令，安装到由 IT 选定的 Python 环境。

```sh
python -m pip install --no-index --find-links ./py --require-hashes -r requirements.txt
```

也可以将 DI_VENDOR_DIR 设为本目录的绝对路径，使用源码技能的启动器。技能会按随技能审查的固定摘要验证 wheel 和解开的缓存。文本模式处理已有文字的 PDF。处理扫描页请使用配套的平台运行时。
