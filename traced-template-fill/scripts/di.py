#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档整合助手 命令行入口：python di.py <命令> ...   （python di.py -h 查看全部命令）"""
import sys

sys.dont_write_bytecode = True          # 不在技能目录生成 __pycache__，便于整体拷贝给其他机器/模型


MIN_PYTHON = (3, 10)
INTERRUPTED_EXIT = 130  # Standard shell exit status for SIGINT.


def main(argv=None):
    if sys.version_info < MIN_PYTHON:
        print("traced-template-fill 需要 Python 3.10 或更高版本。", file=sys.stderr)
        return 2
    from dilib.cli import main as run_cli
    from dilib.runtime_maintenance import RuntimeUnavailable, using_runtime

    try:
        with using_runtime():
            return run_cli(argv)
    except KeyboardInterrupt:
        print("操作已中断。", file=sys.stderr)
        return INTERRUPTED_EXIT
    except RuntimeUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
