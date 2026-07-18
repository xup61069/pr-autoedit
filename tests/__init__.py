"""測試套件。

用 `python -m tests.test_remap`(或 test_decision / test_e2e_smoke)執行時,
Python 會先載入這個 __init__.py。這裡把訊息輸出改成 UTF-8,
避免 Windows 中文命令列(cp950)印中文或 ✓ 時亂碼甚至當掉。
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
