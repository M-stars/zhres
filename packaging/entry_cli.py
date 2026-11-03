"""PyInstaller 入口：zhres 命令行。"""
import sys

from zhres.cli import main

if __name__ == "__main__":
    sys.exit(main())
