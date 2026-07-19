"""
音訊能量偵測 —— 找出「沒有人講話、但有聲音」的段落(音樂、音效、預覽 demo)。

為什麼需要:決策引擎原本只靠辨識出的「詞」判斷,兩個詞之間的長空隙
一律當成靜音刪掉或快轉。但教學片常會播一段音樂或音效給觀眾聽,
那段沒有詞、卻絕對不能剪。這個模組用「音量能量」把這種段落找出來,
決策引擎會把它們標成受保護的音樂段(不刪、不快轉)。

做法(簡單版,依使用者決策 D3):
  1. 把整條音訊切成 50ms 的小窗,算每窗的音量(RMS,換算成 dB)。
  2. 用「底噪」當基準:取所有窗音量的低分位數當底噪,
     比底噪高出 MUSIC_DB_ABOVE_FLOOR 分貝的窗就算「有聲」。
     (用相對值而非絕對值,錄音大小聲不同的影片都適用)
  3. 把相鄰的有聲窗連成區間,縫合太短的間隙、丟掉太短的碎片。

注意:要用「原始」音訊(01_raw.wav)偵測,不能用降噪後的——
AI 降噪是衝著「保留人聲」設計的,音樂可能被它當噪音削掉,
拿降噪後的檔來偵測會漏掉音樂段。
"""

from __future__ import annotations
import numpy as np
import config.settings as cfg

WINDOW_SEC = 0.05          # 每個分析窗的長度(50ms)
FLOOR_PERCENTILE = 10      # 用第 10 百分位的窗音量當「底噪」
MERGE_GAP_SEC = 0.6        # 兩個有聲區間隔小於這個秒數就縫成一段
THRESHOLD_DB_MIN = -60.0   # 門檻下限:再怎麼安靜的錄音,低於這個就是真的無聲
THRESHOLD_DB_MAX = -20.0   # 門檻上限:避免底噪很吵時把門檻抬到剪不到任何東西


def _window_db(audio: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """把音訊切窗、回傳每窗的音量(dBFS)與窗的樣本數"""
    if audio.ndim > 1:                      # 立體聲 -> 單聲道
        audio = audio.mean(axis=1)
    hop = max(1, int(sr * WINDOW_SEC))
    n_win = max(1, len(audio) // hop)
    trimmed = audio[: n_win * hop].reshape(n_win, hop)
    rms = np.sqrt(np.mean(trimmed.astype(np.float64) ** 2, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    return db, hop


def audible_regions_from_array(audio: np.ndarray, sr: int,
                               fps: float) -> list[tuple[int, int]]:
    """核心邏輯(不碰檔案,方便測試):回傳有聲區間清單 [(起始幀, 結束幀), ...]"""
    db, hop = _window_db(audio, sr)

    floor = float(np.percentile(db, FLOOR_PERCENTILE))
    above = float(getattr(cfg, "MUSIC_DB_ABOVE_FLOOR", 12.0))
    threshold = min(max(floor + above, THRESHOLD_DB_MIN), THRESHOLD_DB_MAX)

    audible = db >= threshold
    win_sec = hop / sr

    # 相鄰有聲窗連成 (起, 迄) 秒數區間
    regions: list[list[float]] = []
    start = None
    for i, on in enumerate(audible):
        if on and start is None:
            start = i * win_sec
        elif not on and start is not None:
            regions.append([start, i * win_sec])
            start = None
    if start is not None:
        regions.append([start, len(audible) * win_sec])

    # 縫合太近的區間
    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] < MERGE_GAP_SEC:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    # 丟掉太短的碎片(單一個咳嗽聲、滑鼠喀一下不算音樂)
    min_sec = float(getattr(cfg, "MUSIC_MIN_SEC", 0.4))
    return [(round(a * fps), round(b * fps))
            for a, b in merged if b - a >= min_sec]


def detect_audible_regions(wav_path: str, fps: float) -> list[tuple[int, int]]:
    """讀 WAV 檔並回傳有聲區間(幀)。給 pipeline 呼叫的入口。"""
    import soundfile as sf
    audio, sr = sf.read(wav_path)
    return audible_regions_from_array(np.asarray(audio), sr, fps)
