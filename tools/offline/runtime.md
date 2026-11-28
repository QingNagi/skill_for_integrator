# 平台离线运行时

正式部署需要同一构建交付的技能 ZIP 和与目标 CPU、操作系统匹配的运行时包。由 IT 从受信任的渠道核对两个外层包的 SHA-256，并将两者解到同一技能父目录。技能目录内应同时有 SKILL.md、run.cmd 或 run.sh，以及 offline-runtime/。安装包路径不要包含其他来源的同名文件。

从项目目录调用技能目录中的 run.cmd（Windows）或 bash run.sh（Linux），参数照常传入。启动器首次核验载荷及配套技能的逐文件 SHA-256，在用户私有缓存中组装 Python、PDF 和 OCR 并运行双语自检。同一载荷之后直接复用，工具无需联网或管理员权限。若技能和运行时版本不匹配，首次组装会中止，重新取得成对的交付件即可。更换版本时解到新的干净目录。

默认缓存为 Windows 的 %LOCALAPPDATA%/traced-template-fill/offline 或 Linux 的 $XDG_DATA_HOME/traced-template-fill/offline（默认 ~/.local/share）。DI_RUNTIME_DIR 可指定私有缓存父目录。命令从原工作目录执行，使用者的项目、草稿和来源无需搬入缓存。

运行时清单在 runtime-bom.cdx.json，完整许可和原始 wheel 在已校验的载荷中。运行时、依赖各走软件供应链审批。合并的 offline 套装仅用于本地验收。
