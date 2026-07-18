"""
時間戳重映射引擎 —— 整個系統的地基。

問題:決策引擎刪掉了一些段落、把一些段落加速了。
原本某個詞在「原始影片第 1520 幀」,剪輯後它在時間軸的哪一幀?
字幕要對齊、Premiere marker 要對齊,全靠這裡算出來。

字幕和 marker 共用同一份映射表(RemapTable),
保證兩者永遠一致 —— 這是審閱模式最容易出錯的地方,
把風險收斂到單一實作。
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .models import Segment, Word, Cut, SubtitleLine


@dataclass
class _Span:
    """映射表的一列:原始區間 -> 時間軸區間(含壓縮比)"""
    orig_start: int
    orig_end: int
    timeline_start: int
    factor: float          # 1.0=原速, 6.0=六倍速。刪除段不入表。


class RemapTable:
    def __init__(self, segments: list[Segment], fps: float):
        self.fps = fps
        self._spans: list[_Span] = []
        self._cuts: list[Cut] = []
        self._build(segments)

    def _build(self, segments: list[Segment]) -> None:
        timeline_pos = 0
        for s in segments:
            if s.action == "delete":
                # 刪除段不佔時間軸,但要記錄成一個切點(供 marker)
                self._cuts.append(Cut(
                    timeline_frame=timeline_pos,      # 刪除點落在目前時間軸位置
                    orig_frame=s.start,
                    reason=s.reason,
                    text=s.text,
                    duration_ms=round(s.duration / self.fps * 1000),
                    confidence=s.confidence,
                ))
                continue

            factor = s.factor if s.action == "speed" else 1.0
            self._spans.append(_Span(
                orig_start=s.start,
                orig_end=s.end,
                timeline_start=timeline_pos,
                factor=factor,
            ))
            # 加速後這段在時間軸上佔的長度 = 原長 / 倍率
            timeline_pos += round(s.duration / factor)

        self._total_frames = timeline_pos

    # -----------------------------------------------------------------
    # 核心:把「原始影片幀」映射到「時間軸幀」
    # 落在刪除段的回傳 None(代表這個詞被剪掉了)
    # -----------------------------------------------------------------
    def map_frame(self, orig_frame: int) -> Optional[int]:
        for sp in self._spans:
            if sp.orig_start <= orig_frame < sp.orig_end:
                offset = orig_frame - sp.orig_start
                return sp.timeline_start + round(offset / sp.factor)
        return None

    # -----------------------------------------------------------------
    # 把 Whisper 的詞級時間戳,轉成剪輯後的字幕行
    # 被剪掉的詞自動略過;相鄰的詞聚合成一句
    # -----------------------------------------------------------------
    def build_subtitles(self, words: list[Word],
                        max_chars: int = 18,
                        max_gap_frames: int = 15) -> list[SubtitleLine]:
        lines: list[SubtitleLine] = []
        buf: list[str] = []
        line_start: Optional[int] = None
        line_end: Optional[int] = None
        idx = 1

        def flush():
            nonlocal buf, line_start, line_end, idx
            if buf and line_start is not None:
                lines.append(SubtitleLine(
                    index=idx,
                    start_frame=line_start,
                    end_frame=line_end,
                    text="".join(buf),
                ))
                idx += 1
            buf, line_start, line_end = [], None, None

        def _mid_english_word(prev: str, nxt: str) -> bool:
            """斷點是否落在一個英文/數字單字的中間。
            Whisper 會把 'Pattern1' 切成 'P','atter','n','1' 等碎片,
            若剛好斷在碎片之間會把英文單字攔腰切斷,要避免。"""
            if not prev or not nxt:
                return False
            a, b = prev[-1], nxt[0]
            return a.isascii() and a.isalnum() and b.isascii() and b.isalnum()

        for w in words:
            ts = self.map_frame(w.start_frame(self.fps))
            te = self.map_frame(max(w.start_frame(self.fps),
                                    w.end_frame(self.fps) - 1))
            if ts is None or te is None:
                # 這個詞被剪掉了,順便斷句(避免跨越剪輯點黏在一起)
                flush()
                continue

            # 距離上一個詞太遠,或超過字數上限 -> 換行;
            # 但若正好在英文單字中間,先不斷,等單字結束(避免 Pat|tern 這種切法)
            mid_word = bool(buf) and _mid_english_word(buf[-1], w.text)
            if line_end is not None and not mid_word and (
                ts - line_end > max_gap_frames or
                sum(len(x) for x in buf) >= max_chars
            ):
                flush()

            if line_start is None:
                line_start = ts
            line_end = te
            buf.append(w.text)

        flush()
        return lines

    # -----------------------------------------------------------------
    # 取得切點清單,供 Premiere marker 使用
    # min_duration_ms / need_review 可過濾掉「必刪、不值得看」的切點
    # -----------------------------------------------------------------
    def cuts_for_markers(self, min_duration_ms: int = 0,
                        max_confidence: float = 1.01) -> list[Cut]:
        return [
            c for c in self._cuts
            if c.duration_ms >= min_duration_ms and c.confidence < max_confidence
        ]

    @property
    def total_frames(self) -> int:
        return self._total_frames
