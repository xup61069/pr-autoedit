"""
核心資料結構。整個系統的所有模組都用這裡定義的物件溝通,
避免各模組各自用 dict 造成欄位名稱不一致的 bug。

所有時間單位一律用「幀」(frame),不用秒。
原因:Premiere 的 FCP7 XML 以幀為時基,用秒換算會累積捨入誤差,
30 分鐘的片子累積下來字幕會飄掉好幾格。
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
import json


# ---------------------------------------------------------------------------
# 轉錄結果:Whisper 輸出的每一個「詞」
# ---------------------------------------------------------------------------
@dataclass
class Word:
    text: str          # 詞的文字內容,例如「就是」
    start: float       # 起始時間(秒)—— Whisper 原生是秒,進系統後才轉幀
    end: float         # 結束時間(秒)

    def start_frame(self, fps: float) -> int:
        return round(self.start * fps)

    def end_frame(self, fps: float) -> int:
        return round(self.end * fps)


# ---------------------------------------------------------------------------
# 剪輯段落:決策引擎輸出的基本單位
# ---------------------------------------------------------------------------
Action = Literal["keep", "delete", "speed"]

@dataclass
class Segment:
    start: int                      # 原始影片的起始幀
    end: int                        # 原始影片的結束幀(不含)
    action: Action                  # keep=保留 / delete=刪除 / speed=快轉
    factor: float = 1.0             # 僅 action=speed 時有意義,例如 6.0 = 六倍速
    reason: str = ""                # "filler"(冗詞)/ "silence"(靜音)/ ""
    text: str = ""                  # 被處理的文字(冗詞才有),供 marker 顯示
    confidence: float = 1.0         # 決策信心 0~1,低信心的才需要人工審閱

    @property
    def duration(self) -> int:
        return self.end - self.start


# ---------------------------------------------------------------------------
# 切點:重映射後,供 Premiere marker 使用的資訊
# ---------------------------------------------------------------------------
@dataclass
class Cut:
    timeline_frame: int             # 在剪輯後時間軸上的幀位置
    orig_frame: int                 # 原始影片的幀位置(供對照)
    reason: str                     # filler / silence
    text: str                       # 被刪的詞
    duration_ms: int                # 刪除長度(毫秒),供判斷是否需要下 marker
    confidence: float


# ---------------------------------------------------------------------------
# 字幕行:重映射後的最終字幕
# ---------------------------------------------------------------------------
@dataclass
class SubtitleLine:
    index: int
    start_frame: int                # 剪輯後時間軸上的起始幀
    end_frame: int
    text: str


# ---------------------------------------------------------------------------
# 整個管線的產物容器,方便序列化成 JSON 做為各階段的中繼檔
# ---------------------------------------------------------------------------
@dataclass
class Timeline:
    fps: float
    source: str                     # 已混回乾淨音訊的影片檔路徑
    segments: list[Segment] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        data = {
            "fps": self.fps,
            "source": self.source,
            "segments": [asdict(s) for s in self.segments],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, path: str) -> "Timeline":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segs = [Segment(**s) for s in data["segments"]]
        return cls(fps=data["fps"], source=data["source"], segments=segs)
