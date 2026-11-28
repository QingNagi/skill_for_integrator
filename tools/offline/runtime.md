# 平台离线运行时

正式部署需要同一构建的技能 ZIP、目标平台的运行时包、安装脚本、deploy.py 和摘要清单。核验摘要后，Windows 使用 `install.ps1 -ProjectRoot <项目>`，Linux 使用 `sh install.sh --project-root <项目>`。安装器保留旧配置备份，将技能放在 .agents/skills，建立 .claude/skills 入口，并运行 doctor。两个入口都使用同一份技能和私有运行时。

默认运行时位置为 Windows 的 %LOCALAPPDATA%/traced-template-fill/offline 或 Linux 的 $XDG_DATA_HOME/traced-template-fill/offline（默认 ~/.local/share）。DI_RUNTIME_DIR 可指向技能目录之外允许执行程序的目录。来源、模板与输出保留在业务工作区。

独立验收时仍可将技能和运行时解到同一个临时父目录，从技能的 run.cmd 或 run.sh 启动。首次组装验证配套技能的逐文件 SHA-256，并实际识别中英文扫描样张。

各平台提供 runtime-bom.cdx.json。完整许可、依赖 wheel 和 OCR 模型在已核验的载荷中。
