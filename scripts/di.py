#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档整合助手 命令行入口：python di.py <命令> ...   （python di.py -h 查看全部命令）"""
import sys

sys.dont_write_bytecode = True          # 不在技能目录生成 __pycache__，便于整体拷贝给其他机器/模型


def main(argv=None):
    if sys.version_info < (3, 8):
        print("doc-integrator 需要 Python 3.8 或更高版本。", file=sys.stderr)
        return 2
    from dilib.cli import main as run_cli

    try:
        return run_cli(argv)  # argparse validates subcommands and required arguments.
    except KeyboardInterrupt:
        print("操作已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
