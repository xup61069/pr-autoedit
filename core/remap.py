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
    # 替代建構式:直接用「原始區間 -> 時間軸位置」清單建表
    # -----------------------------------------------------------------
    @classmethod
    def from_spans(cls, spans: list[tuple[int, int, int, float]],
                fps: float) -> "RemapTable":
        """給「依 Premiere 目前序列版面產字幕」用(見 modules/live_subs)。

        spans 每列 = (原始起幀, 原始迄幀, 時間軸起幀, 速度倍率)。
        使用者在 Premiere 刪掉的內容不在清單裡,build_subtitles
        會自動略過落在其中的詞 —— 字幕跟著剪完的樣子走。"""
        t = cls.__new__(cls)
        t.fps = fps
        t._spans = [_Span(a, b, s, f if f and f > 0 else 1.0)
                    for a, b, s, f in spans]
        t._cuts = []
        t._total_frames = max(
            (sp.timeline_start + round((sp.orig_end - sp.orig_start) / sp.factor)
             for sp in t._spans), default=0)
        return t

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
    # 把「原始影片的一段區間」映射到時間軸區間
    # -----------------------------------------------------------------
    def map_span(self, orig_start: int, orig_end: int) -> Optional[tuple[int, int]]:
        """回傳 (時間軸起幀, 時間軸迄幀);整段都被剪掉才回 None。

        為什麼不能只用 map_frame 對頭尾兩個點:一個詞的頭或尾常常
        剛好落在被剪掉的地方(能量微剪剪的就是詞邊緣的空白),
        那樣會把「其實還聽得到」的詞整個丟掉,字幕就會莫名其妙缺字。
        改成看「有沒有任何一部分留著」,留著就對到留下來的那一段。"""
        first = last = None
        for sp in self._spans:
            a = max(orig_start, sp.orig_start)
            b = min(orig_end, sp.orig_end)
            if b <= a:
                continue
            ts = sp.timeline_start + round((a - sp.orig_start) / sp.factor)
            te = sp.timeline_start + round((b - sp.orig_start) / sp.factor)
            if first is None:
                first = ts
            last = te
        if first is None:
            return None
        return first, max(first, last)

    # -----------------------------------------------------------------
    # 把 Whisper 的詞級時間戳,轉成剪輯後的字幕行
    # 被剪掉的詞自動略過;相鄰的詞聚合成一句
    # -----------------------------------------------------------------
    # 標點分類:句末(強制斷句)與句中逗號(較長時才斷)
    _SENT_END = "。！？!?…"
    _CLAUSE_END = ",,、;;::"

    def build_subtitles(self, words: list[Word],
                        max_chars: int = 18,
                        max_gap_frames: int = 15,
                        max_chars_no_punct: Optional[int] = None
                        ) -> list[SubtitleLine]:
        """把詞級時間戳轉成字幕行。

        斷行優先順序:句末標點(。!?)> 原始說話的明顯停頓 >
        句中逗號(行已有一定長度時)> 字數上限。盡量讓每行結束在
        自然的語氣停頓,而不是數到字數就硬斷。行尾的逗號會去掉。

        停頓用「原始影片」的時間判斷,不用剪輯後的:
        剪掉一個 0.3 秒的冗詞不該把字幕硬切成兩行(會剁得很碎),
        但剪掉一大段靜音後,前後兩句本來就隔了很久,仍然要分行——
        看原始停頓,兩種情況自然都對。"""
        # 逐字稿幾乎沒有標點時(某些引擎、或提示詞沒示範標點),
        # 唯一能斷行的線索只剩停頓和字數。這時行長上限要收短一點,
        # 否則會出現「一整串三四十個字、結尾還斷在『所以』」的爛斷行。
        if max_chars_no_punct:
            marked = sum(1 for w in words
                        if w.text and w.text[-1] in self._SENT_END + self._CLAUSE_END)
            if words and marked / len(words) < 0.02:
                max_chars = min(max_chars, max_chars_no_punct)

        lines: list[SubtitleLine] = []
        buf: list[str] = []
        line_start: Optional[int] = None
        line_end: Optional[int] = None
        prev_orig_end: Optional[int] = None   # 上一個「保留」詞的原始結束幀
        idx = 1
        soft_break = max(6, max_chars // 2)   # 逗號斷行的最短行長

        def flush():
            nonlocal buf, line_start, line_end, idx
            # 行尾的句中逗號拿掉(斷行本身已代表停頓),句末標點保留
            text = "".join(buf).rstrip(self._CLAUSE_END + " ")
            if text and line_start is not None:
                lines.append(SubtitleLine(
                    index=idx,
                    start_frame=line_start,
                    end_frame=line_end,
                    text=text,
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
            ws_orig = w.start_frame(self.fps)
            we_orig = w.end_frame(self.fps)
            # 只要這個詞還有一部分留在時間軸上,字幕就要留著(見 map_span)。
            # 整個詞都被剪掉才跳過,且不強制斷行。
            span = self.map_span(ws_orig, max(ws_orig + 1, we_orig))
            if span is None:
                continue
            ts, te = span
            te = max(ts, te - 1)

            # 換行時機(加入這個詞之前判斷):原始停頓大,或已達字數上限。
            # 但若正好在英文單字中間,先不斷,等單字結束(避免 Pat|tern)
            mid_word = bool(buf) and _mid_english_word(buf[-1], w.text)
            if line_end is not None and not mid_word and (
                (prev_orig_end is not None and
                 ws_orig - prev_orig_end > max_gap_frames) or
                sum(len(x) for x in buf) >= max_chars
            ):
                flush()

            if line_start is None:
                line_start = ts
            line_end = te
            prev_orig_end = w.end_frame(self.fps)
            buf.append(w.text)

            # 加入這個詞之後:遇到標點就順勢斷句,讓行尾落在自然停頓
            tail = w.text[-1] if w.text else ""
            line_len = sum(len(x) for x in buf)
            if tail in self._SENT_END:
                flush()                                   # 句末 → 一定斷
            elif tail in self._CLAUSE_END and line_len >= soft_break:
                flush()                                   # 逗號且行夠長 → 斷

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
