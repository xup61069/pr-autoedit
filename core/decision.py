"""
決策引擎 —— 系統的大腦。
輸入:Whisper 的詞級轉錄結果。
輸出:剪輯段落清單(哪些保留、哪些刪除、哪些快轉)。

這是唯一需要針對你個人說話習慣調校的模組,
所有門檻和詞表都在 config/settings.py,這裡只放邏輯。
"""

from __future__ import annotations
from core.models import Word, Segment
import config.settings as cfg


def _is_isolated_or_repeated(words: list[Word], i: int) -> bool:
    """判斷 words[i] 這個有條件冗詞是否該刪:
    句首孤立出現,或與前後詞重複。"""
    w = words[i].text
    # 連續重複(對對對、然後然後)
    if i > 0 and words[i - 1].text == w:
        return True
    if i + 1 < len(words) and words[i + 1].text == w:
        return True
    # 句首孤立:前一個詞距離較遠(像是換句),且本身是連接詞
    if i == 0:
        return True
    gap = words[i].start - words[i - 1].end
    if gap > 0.4:                    # 前面有停頓,像是新句子的開頭語助
        return True
    return False


def build_segments(words: list[Word], fps: float,
                total_frames: int) -> list[Segment]:
    """
    主流程:掃過所有詞,產生連續的 Segment 清單。
    保證輸出的段落首尾相連、覆蓋整支影片(0 到 total_frames)。
    """
    segments: list[Segment] = []
    cursor = 0                       # 目前處理到的原始幀位置

    def emit_keep(start_f: int, end_f: int):
        if end_f > start_f:
            segments.append(Segment(start_f, end_f, "keep"))

    def emit_silence(start_f: int, end_f: int):
        if end_f <= start_f:
            return
        if cfg.SILENCE_ACTION == "delete":
            segments.append(Segment(start_f, end_f, "delete",
                                    reason="silence", confidence=0.95))
        else:
            segments.append(Segment(start_f, end_f, "speed",
                                    factor=cfg.SILENCE_SPEED_FACTOR,
                                    reason="silence", confidence=0.95))

    pad = round(cfg.SILENCE_PADDING_SEC * fps)
    silence_gap = cfg.SILENCE_THRESHOLD_SEC

    for i, w in enumerate(words):
        ws = w.start_frame(fps)
        we = w.end_frame(fps)

        # --- 1. 處理這個詞之前的空隙(可能是靜音)---
        if ws > cursor:
            gap_sec = (ws - cursor) / fps
            if gap_sec >= silence_gap:
                # 空隙夠長 -> 靜音處理,但前後留 padding
                emit_keep(cursor, min(cursor + pad, ws))
                emit_silence(min(cursor + pad, ws), max(ws - pad, cursor + pad))
                emit_keep(max(ws - pad, cursor + pad), ws)
            else:
                emit_keep(cursor, ws)      # 短空隙,正常保留
        cursor = max(cursor, ws)

        # --- 2. 判斷這個詞本身是不是冗詞 ---
        text = w.text.strip()
        if text in cfg.FILLERS_ALWAYS:
            segments.append(Segment(ws, we, "delete", reason="filler",
                                    text=text, confidence=1.0))
        elif text in cfg.FILLERS_CONDITIONAL and _is_isolated_or_repeated(words, i):
            segments.append(Segment(ws, we, "delete", reason="filler",
                                    text=text,
                                    confidence=cfg.CONDITIONAL_CONFIDENCE))
        else:
            emit_keep(ws, we)              # 正常的詞,保留
        cursor = max(cursor, we)

    # --- 3. 收尾:最後一個詞到影片結尾 ---
    if cursor < total_frames:
        gap_sec = (total_frames - cursor) / fps
        if gap_sec >= silence_gap:
            emit_silence(cursor, total_frames)
        else:
            emit_keep(cursor, total_frames)

    return _merge_adjacent(segments)


def _merge_adjacent(segments: list[Segment]) -> list[Segment]:
    """合併相鄰的同類段落,讓時間軸更乾淨(減少 PR 裡的碎片 clip)"""
    if not segments:
        return []
    merged = [segments[0]]
    for s in segments[1:]:
        last = merged[-1]
        same = (last.action == s.action and last.end == s.start
                and last.factor == s.factor and last.reason == s.reason
                and s.action != "delete")   # 刪除段各自獨立,保留 marker 資訊
        if same:
            last.end = s.end
        else:
            merged.append(s)
    return merged
