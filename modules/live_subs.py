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
from modules.seq_layout import load_clips, speed_is_trustworthy
from modules.subtitles import write_srt
from modules.workspace import wpath
import config.settings as cfg


def build_from_layout(layout_json: str, work_dir: str) -> str:
    """layout JSON -> 對位後的 SRT。回傳輸出路徑。"""
    clips, _layout = load_clips(layout_json)
    if not clips:
        raise SystemExit("序列版面是空的(時間軸上沒有片段),無法產字幕。")

    # fps 與詞級轉錄都來自當初處理這支影片時的產物
    tl_path = wpath(work_dir, "03_timeline.json")
    tr_path = wpath(work_dir, "02_transcript.json")
    for p in (tl_path, tr_path):
        if not os.path.exists(p):
            raise SystemExit(f"找不到 {os.path.basename(p)}。\n"
                             "「用目前序列產生字幕」只能用在本工具處理過的影片,"
                             "且序列裡的素材要是它產生的那份。")
    fps = Timeline.from_json(tl_path).fps
    words = load_cached_words(tr_path)

    # 序列版面 -> 映射表:(原始起幀, 原始迄幀, 時間軸起幀, 速度)
    #
    # 哪些片段的來源時間點可信,判斷在 seq_layout.speed_is_trustworthy
    # (「重新辨識」那條路也用同一份判斷,兩邊不能各寫一套)。
    #
    # 不擋掉的下場:那些假的區間會跟真的區間重疊,一個詞同時對到兩個
    # 相距很遠的位置,字幕的結束時間就被拉到幾十秒甚至幾分鐘之後
    # ——看起來就是「字幕的時間點整個壞掉」。
    #
    # 跳過的代價很小:本工具只對「靜音」加速,那裡本來就沒有字。
    # (試過用前後鄰居的來源範圍去重建,133 個只重建對 15 個 ——
    #  因為被刪掉的片段也夾在中間,那個縫隙不等於加速的那一段。)
    spans = []
    untrusted = 0
    for c in clips:
        src_in, src_out = c["in"], c["out"]
        tl_start, tl_end, speed = c["start"], c["end"], c["speed"]

        if not speed_is_trustworthy(src_in, src_out, tl_start, tl_end, speed):
            untrusted += 1
            continue

        a, b = round(src_in * fps), round(src_out * fps)
        if b > a:
            spans.append((a, b, round(tl_start * fps), speed))

    if untrusted:
        print(f"  略過 {untrusted} 個變速片段:Premiere 回報的來源時間點對不上"
              f"(來源長度 ≠ 時間軸長度 × 倍率),拿來對位會把字幕的時間弄亂。\n"
              f"    這些是被加速帶過的停頓,本來就沒有字,不影響字幕內容。")
    if not spans:
        raise SystemExit(
            "序列裡沒有任何『來源時間點可信』的片段,無法對位字幕。\n"
            "  這通常代表序列裡的片段幾乎都被改過速度。\n"
            "  可以改用主流程產生的 04_subtitles.srt。")
    table = RemapTable.from_spans(spans, fps)

    subs = table.build_subtitles(
        words,
        max_chars=cfg.SUBTITLE_MAX_CHARS,
        max_gap_frames=round(cfg.SUBTITLE_MAX_GAP_SEC * fps),
        max_chars_no_punct=getattr(cfg, "SUBTITLE_MAX_CHARS_NO_PUNCT", None),
        min_chars=getattr(cfg, "SUBTITLE_MIN_CHARS", 0),
        hard_gap_frames=round(getattr(cfg, "SUBTITLE_HARD_GAP_SEC", 4.0) * fps),
    )
    # 使用者可能移動過片段順序,字幕行依時間軸時間重排、重新編號
    subs.sort(key=lambda ln: ln.start_frame)
    for i, ln in enumerate(subs, 1):
        ln.index = i

    out_srt = wpath(work_dir, "05_subtitles_final.srt")
    write_srt(subs, fps, out_srt)
    return out_srt


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法:python -m modules.live_subs <layout.json> <output資料夾>",
              file=sys.stderr)
        sys.exit(1)
    path = build_from_layout(sys.argv[1], sys.argv[2])
    print(f"完成 ✓ {path}")
