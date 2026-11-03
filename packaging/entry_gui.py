"""PyInstaller 入口：zhres 图形界面（异常写入日志文件便于诊断）。"""
import sys
import tempfile
import traceback
from pathlib import Path

_LOG = Path(tempfile.gettempdir()) / "zhres-gui.log"

try:
    from zhres.gui import main
    if __name__ == "__main__":
        sys.exit(main())
except Exception:
    _LOG.write_text(traceback.format_exc(), encoding="utf-8")
    raise
