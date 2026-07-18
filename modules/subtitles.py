"""
字幕輸出 —— 從重映射後的字幕行產生 SRT。
簡轉繁用 OpenCC(可選)。

依賴:pip install opencc-python-reimplemented  (若要簡轉繁)
"""

from __future__ import annotations
from core.models import SubtitleLine
import config.settings as cfg


def _frame_to_srt_time(frame: int, fps: float) -> str:
    total_sec = frame / fps
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)
    s = int(total_sec % 60)
    ms = int((total_sec - int(total_sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _convert_traditional(lines: list[SubtitleLine]) -> list[SubtitleLine]:
    if not cfg.CONVERT_TO_TRADITIONAL:
        return lines
    try:
        from opencc import OpenCC
        cc = OpenCC("s2twp")            # 簡體 -> 台灣正體(含慣用詞轉換)
        for ln in lines:
            ln.text = cc.convert(ln.text)
    except ImportError:
        print("  (未安裝 opencc,略過簡轉繁)")
    return lines


def write_srt(lines: list[SubtitleLine], fps: float, out_path: str) -> str:
    lines = _convert_traditional(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(f"{ln.index}\n")
            f.write(f"{_frame_to_srt_time(ln.start_frame, fps)} --> "
                    f"{_frame_to_srt_time(ln.end_frame, fps)}\n")
            f.write(f"{ln.text}\n\n")
    print(f"  字幕輸出:{len(lines)} 行 -> {out_path}")
    return out_path
