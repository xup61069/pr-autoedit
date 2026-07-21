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


def read_audio(wav_path: str) -> tuple[np.ndarray, int]:
    """讀 WAV 檔給偵測用。回傳 (樣本陣列, 取樣率)。

    刻意指定 float32:soundfile 預設是 float64,而這條管線要整支影片一次
    讀進記憶體。單聲道 48kHz 的話,34 分鐘的片 float64 是 783MB、
    float32 只要 392MB;一個半小時的片是 2.1GB 對 1.0GB。
    來源是 16-bit PCM,float32 的位數綽綽有餘,精度上沒有任何損失
    (真正需要精度的是後面的平方與累加,那一步仍然用 float64 算)。
    """
    import soundfile as sf
    audio, sr = sf.read(wav_path, dtype="float32")
    return np.asarray(audio), sr


def _window_db(audio: np.ndarray, sr: int,
            window_sec: float = WINDOW_SEC) -> tuple[np.ndarray, int]:
    """把音訊切窗、回傳每窗的音量(dBFS)與窗的樣本數"""
    if audio.ndim > 1:                      # 立體聲 -> 單聲道
        audio = audio.mean(axis=1)
    hop = max(1, int(sr * window_sec))
    n_win = max(1, len(audio) // hop)
    trimmed = audio[: n_win * hop].reshape(n_win, hop)
    # dtype=np.float64 是叫 np.mean「用 float64 累加」,不是先把整個陣列
    # 轉成 float64。差別在記憶體:以前寫 trimmed.astype(np.float64) ** 2,
    # 那會複製出兩份跟音檔一樣大的暫存陣列(34 分鐘的片就是多 1.5GB)。
    # 這樣寫精度一樣(累加仍在 float64),但少掉那兩份複製。
    rms = np.sqrt(np.mean(np.square(trimmed), axis=1, dtype=np.float64))
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
    """讀 WAV 檔並回傳有聲區間(幀)。

    註:pipeline 兩種偵測都要做,會改用 read_audio 讀一次、餵給兩個
    *_from_array 函式,不走這裡(同一個檔讀兩次是白花時間也白吃記憶體)。
    這個入口留給單獨呼叫的場合。"""
    audio, sr = read_audio(wav_path)
    return audible_regions_from_array(audio, sr, fps)


# ---------------------------------------------------------------------------
# 能量微剪:找出「真正沒聲音」的小停頓
# ---------------------------------------------------------------------------
# 跟上面的音樂偵測相反——那個找「有聲音的地方」拿來保護,
# 這個找「沒聲音的地方」拿來剪掉。用比較細的窗(20ms)才抓得到短停頓。
#
# 門檻取法也不同:音樂偵測用「底噪 + N dB」,但螢幕錄影常有整段數位全靜音
# (真正的 0),會把底噪算成 -200dB 而失真。這裡改用「說話音量 - N dB」,
# 說話音量取第 90 百分位,不受那些 0 影響。

QUIET_WINDOW_SEC = 0.02    # 20ms:比音樂偵測細,才抓得到 0.2 秒級的停頓
SPEECH_PERCENTILE = 90     # 用第 90 百分位的窗音量代表「說話音量」


def quiet_regions_from_array(audio: np.ndarray, sr: int,
                            fps: float) -> list[tuple[int, int]]:
    """核心邏輯(不碰檔案,方便測試):回傳可以剪掉的安靜區間
    [(起始幀, 結束幀), ...]。已經扣掉頭尾要保留的緩衝。"""
    db, hop = _window_db(audio, sr, window_sec=QUIET_WINDOW_SEC)

    speech = float(np.percentile(db, SPEECH_PERCENTILE))
    below = float(getattr(cfg, "MICRO_TRIM_DB_BELOW_SPEECH", 22.0))
    threshold = speech - below

    quiet = db < threshold
    win_sec = hop / sr

    # 連續的安靜窗連成區間
    runs: list[tuple[int, int]] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(quiet)))

    # 頭尾各留一點緩衝(不要剪得太死),剩下的長度夠久才值得剪
    keep = float(getattr(cfg, "MICRO_TRIM_KEEP_SEC", 0.06))
    min_cut = float(getattr(cfg, "MICRO_TRIM_MIN_SEC", 0.25))
    out: list[tuple[int, int]] = []
    for a, b in runs:
        a_sec = a * win_sec + keep
        b_sec = b * win_sec - keep
        if b_sec - a_sec >= min_cut:
            out.append((round(a_sec * fps), round(b_sec * fps)))
    return out


def detect_quiet_regions(wav_path: str, fps: float) -> list[tuple[int, int]]:
    """讀 WAV 檔並回傳可剪掉的安靜區間(幀)。見 detect_audible_regions 的註。"""
    audio, sr = read_audio(wav_path)
    return quiet_regions_from_array(audio, sr, fps)
