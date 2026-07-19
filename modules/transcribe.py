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


def effective_vocab() -> list[str]:
    """合併『教學類型詞庫』(VOCAB_CATEGORIES 選到的)與個人額外術語
    (CUSTOM_VOCAB),去重、保序。這是辨識提示詞/熱詞的實際用詞。"""
    terms: list[str] = []
    presets = getattr(cfg, "VOCAB_PRESETS", {}) or {}
    for cat in getattr(cfg, "VOCAB_CATEGORIES", []) or []:
        terms += presets.get(cat, [])
    terms += getattr(cfg, "CUSTOM_VOCAB", []) or []
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_initial_prompt() -> str:
    """組出給辨識引擎的開場提示詞。
    優先用完全自訂的 WHISPER_INITIAL_PROMPT;否則用教學類型 + 個人術語自動組。"""
    if getattr(cfg, "WHISPER_INITIAL_PROMPT", None):
        return cfg.WHISPER_INITIAL_PROMPT
    base = "以下是一段中文教學影片的口白。"
    vocab = effective_vocab()
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
    lang = getattr(cfg, "WHISPER_LANGUAGE", "zh")
    if lang in ("auto", "", None):          # auto/空白 -> 交給 Whisper 自動偵測
        lang = None
    segments, info = model.transcribe(
        audio_path,
        language=lang,
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


_funasr_model = None   # 模型快取,避免同一次執行重複載入


def _transcribe_funasr(audio_path: str) -> list[Word]:
    """引擎 B:阿里 FunASR / Paraformer-zh。

    適合『純中文、少英文』的內容。注意:對中英夾雜(大量英文術語)的教學片,
    實測不如 Whisper(英文/數字容易出錯,如 F6→f六、MIDI→谜dy),
    這類內容建議仍用 faster-whisper。

    做法:用 paraformer + VAD(不掛標點模型,讓 token 與時間戳乾淨 1:1 對齊),
    再用 OpenCC 簡轉繁,讓後段的繁體詞表與字幕一致。CUSTOM_VOCAB 會當熱詞餵入。
    依賴:pip install funasr(首次執行自動下載模型約 2GB)。"""
    from funasr import AutoModel
    global _funasr_model
    if _funasr_model is None:
        print("  載入 FunASR/Paraformer 模型(首次會下載約 2GB)...")
        _funasr_model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad",
                                  disable_update=True, log_level="ERROR")

    hotword = " ".join(effective_vocab())
    print("  轉錄中...")
    res = _funasr_model.generate(input=audio_path, batch_size_s=300,
                                 hotword=hotword)

    tokens = (res[0].get("text", "") if res else "").split()
    stamps = (res[0].get("timestamp") if res else None) or []

    # 簡轉繁(Paraformer 輸出簡體;轉成繁體讓決策引擎詞表與字幕一致)
    try:
        from opencc import OpenCC
        _cc = OpenCC("s2tw")
        convert = _cc.convert
    except ImportError:
        print("  (未安裝 opencc,FunASR 輸出維持簡體)")
        convert = lambda s: s

    words: list[Word] = []
    for tok, span in zip(tokens, stamps):
        if not span or len(span) < 2:
            continue
        words.append(Word(text=convert(tok),
                          start=span[0] / 1000.0,     # 毫秒 -> 秒
                          end=span[1] / 1000.0))
    return words


def _save_cache(words: list[Word], path: str) -> None:
    data = [{"text": w.text, "start": w.start, "end": w.end} for w in words]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache(path: str) -> list[Word]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Word(**d) for d in data]
