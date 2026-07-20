"""
依「Premiere 目前序列的實際版面」產生字幕 —— 字幕最後做(P5)。

為什麼:活專案模式下你會在 Premiere 裡自己刪片段、改速度,
事先產的字幕會跟你剪完的時間軸對不上。這裡反過來:
面板的 ExtendScript 把目前序列每個片段的
(時間軸位置、來源入出點、速度)寫成 layout JSON,
本模組把快取的詞級轉錄(02_transcript.json)透過這份版面重新對位——
被你刪掉的片段裡的詞自動消失、快轉片段裡的詞時間自動壓縮,
字幕永遠對準你「剪完當下」的樣子。不用重新轉錄、不用匯出音訊,幾秒完成。

用法(面板「用目前序列產生字幕」按鈕呼叫;也可手動):
    python -m modules.live_subs <layout.json> <output資料夾>
輸出:<output資料夾>/05_subtitles_final.srt
"""

from __future__ import annotations
import json, os, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.models import Timeline
from core.remap import RemapTable
from modules.transcribe import load_cached_words
from modules.subtitles import write_srt
import config.settings as cfg


def build_from_layout(layout_json: str, work_dir: str) -> str:
    """layout JSON -> 對位後的 SRT。回傳輸出路徑。"""
    with open(layout_json, "r", encoding="utf-8") as f:
        layout = json.load(f)
    clips = layout.get("clips", [])
    if not clips:
        raise SystemExit("序列版面是空的(時間軸上沒有片段),無法產字幕。")

    # fps 與詞級轉錄都來自當初處理這支影片時的產物
    tl_path = os.path.join(work_dir, "03_timeline.json")
    tr_path = os.path.join(work_dir, "02_transcript.json")
    for p in (tl_path, tr_path):
        if not os.path.exists(p):
            raise SystemExit(f"找不到 {os.path.basename(p)}。\n"
                             "「用目前序列產生字幕」只能用在本工具處理過的影片,"
                             "且序列裡的素材要是它產生的那份。")
    fps = Timeline.from_json(tl_path).fps
    words = load_cached_words(tr_path)

    # 序列版面 -> 映射表:(原始起幀, 原始迄幀, 時間軸起幀, 速度)
    spans = []
    for c in sorted(clips, key=lambda c: float(c.get("start", 0))):
        src_in = round(float(c["in"]) * fps)
        src_out = round(float(c["out"]) * fps)
        tl_start = round(float(c["start"]) * fps)
        speed = float(c.get("speed") or 1.0)
        if src_out > src_in:
            spans.append((src_in, src_out, tl_start, abs(speed) or 1.0))
    table = RemapTable.from_spans(spans, fps)

    subs = table.build_subtitles(
        words,
        max_chars=cfg.SUBTITLE_MAX_CHARS,
        max_gap_frames=round(cfg.SUBTITLE_MAX_GAP_SEC * fps),
        max_chars_no_punct=getattr(cfg, "SUBTITLE_MAX_CHARS_NO_PUNCT", None),
    )
    # 使用者可能移動過片段順序,字幕行依時間軸時間重排、重新編號
    subs.sort(key=lambda ln: ln.start_frame)
    for i, ln in enumerate(subs, 1):
        ln.index = i

    out_srt = os.path.join(work_dir, "05_subtitles_final.srt")
    write_srt(subs, fps, out_srt)
    return out_srt


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法:python -m modules.live_subs <layout.json> <output資料夾>",
              file=sys.stderr)
        sys.exit(1)
    path = build_from_layout(sys.argv[1], sys.argv[2])
    print(f"完成 ✓ {path}")
