"""
語音轉錄模組 —— 把音訊轉成「詞級時間戳」,這是整個系統的唯一真相來源。

支援可切換的辨識引擎(見 config.ASR_ENGINE):
  "faster-whisper" —— 目前實作(Whisper,泛用、多語)
  "funasr"         —— 預留:阿里 FunASR / Paraformer(中文通常更準,尚未實作)

不論用哪個引擎,對外都回傳一樣的 list[Word](text/start/end,秒),
所以之後要換引擎,其餘管線完全不用改。

依賴:pip install faster-whisper
第一次執行會自動下載模型(large-v3 約 3GB),下載後快取,之後離線可用。
"""

from __future__ import annotations
from core.models import Word
import config.settings as cfg
import json
import os


def transcribe(audio_path: str, cache_json: str | None = None) -> list[Word]:
    """對音訊做詞級轉錄。
    若提供 cache_json 且檔案存在,直接讀快取(省下重複轉錄的時間;
    想改用新引擎或新提示詞重轉,刪掉該快取檔即可)。"""
    if cache_json and os.path.exists(cache_json):
        print(f"  讀取轉錄快取:{cache_json}")
        return _load_cache(cache_json)

    engine = getattr(cfg, "ASR_ENGINE", "faster-whisper")
    if engine == "faster-whisper":
        words = _transcribe_faster_whisper(audio_path)
    elif engine == "funasr":
        words = _transcribe_funasr(audio_path)
    else:
        raise ValueError(f"未知的 ASR_ENGINE:{engine!r}("
                         "目前支援 'faster-whisper';'funasr' 尚未實作)")

    print(f"  轉錄完成:{len(words)} 個詞")
    if cache_json:
        _save_cache(words, cache_json)
    return words


def _build_initial_prompt() -> str:
    """組出給辨識引擎的開場提示詞。
    優先用完全自訂的 WHISPER_INITIAL_PROMPT;否則用 CUSTOM_VOCAB 自動組。"""
    if getattr(cfg, "WHISPER_INITIAL_PROMPT", None):
        return cfg.WHISPER_INITIAL_PROMPT
    base = "以下是一段中文教學影片的口白。"
    vocab = getattr(cfg, "CUSTOM_VOCAB", None)
    if vocab:
        base += "常見詞彙:" + "、".join(vocab) + "。"
    return base


def _transcribe_faster_whisper(audio_path: str) -> list[Word]:
    """引擎 A:faster-whisper。"""
    from faster_whisper import WhisperModel

    print(f"  載入 Whisper 模型 {cfg.WHISPER_MODEL}(首次會下載約 3GB)...")
    model = WhisperModel(
        cfg.WHISPER_MODEL,
        device=cfg.WHISPER_DEVICE,
        compute_type=cfg.WHISPER_COMPUTE_TYPE,
    )

    print("  轉錄中...")
    segments, info = model.transcribe(
        audio_path,
        language=cfg.WHISPER_LANGUAGE,
        word_timestamps=True,               # 關鍵:要詞級時間戳
        initial_prompt=_build_initial_prompt(),
        vad_filter=True,                    # 內建語音活動偵測,幫忙找靜音
    )

    words: list[Word] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append(Word(
                    text=w.word.strip(),
                    start=w.start,
                    end=w.end,
                ))
    return words


def _transcribe_funasr(audio_path: str) -> list[Word]:
    """引擎 B(預留):阿里 FunASR / Paraformer,中文通常更準。

    尚未實作。要接的話,在這裡呼叫 FunASR 取得詞級時間戳,
    轉成 list[Word](text 為詞、start/end 為秒)回傳即可,其餘管線不用動。
    參考:pip install funasr modelscope;Paraformer-zh 有帶時間戳的版本,
    輸出多為簡體,後段已有 OpenCC 簡轉繁可接。"""
    raise NotImplementedError(
        "ASR_ENGINE='funasr' 尚未實作,目前請用 'faster-whisper'。\n"
        "要接 FunASR:在 modules/transcribe.py 的 _transcribe_funasr() 裡實作,"
        "回傳 list[Word] 即可,管線其餘部分完全不用改。")


def _save_cache(words: list[Word], path: str) -> None:
    data = [{"text": w.text, "start": w.start, "end": w.end} for w in words]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache(path: str) -> list[Word]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Word(**d) for d in data]
