"""
語音轉錄模組 —— faster-whisper 包裝。
輸出詞級時間戳,這是整個系統的「唯一真相來源」,只轉一次。

依賴:pip install faster-whisper
第一次執行會自動下載模型(large-v3 約 3GB),下載後快取,之後離線可用。
"""

from __future__ import annotations
from core.models import Word
import config.settings as cfg
import json


def transcribe(audio_path: str, cache_json: str | None = None) -> list[Word]:
    """
    對音訊做詞級轉錄。
    若提供 cache_json 且檔案存在,直接讀快取(省下重複轉錄的時間)。
    """
    if cache_json:
        import os
        if os.path.exists(cache_json):
            print(f"  讀取轉錄快取:{cache_json}")
            return _load_cache(cache_json)

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
        initial_prompt=cfg.WHISPER_INITIAL_PROMPT,
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

    print(f"  轉錄完成:{len(words)} 個詞")
    if cache_json:
        _save_cache(words, cache_json)
    return words


def _save_cache(words: list[Word], path: str) -> None:
    data = [{"text": w.text, "start": w.start, "end": w.end} for w in words]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache(path: str) -> list[Word]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Word(**d) for d in data]
