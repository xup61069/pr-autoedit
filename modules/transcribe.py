"""
語音轉錄模組 —— 把音訊轉成「詞級時間戳」,這是整個系統的唯一真相來源。

支援可切換的辨識引擎(見 config.ASR_ENGINE):
  "faster-whisper" —— 預設(Whisper,泛用、多語,中英夾雜表現較好)
  "funasr"         —— 備選:阿里 FunASR / Paraformer(純中文可試;
                      中英夾雜實測不如 Whisper,且逐字輸出無標點)

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


def _asr_fingerprint() -> dict:
    """目前「會影響辨識結果」的設定組合。

    快取檔會記下轉錄當時的組合;之後任何一項變了(例如引擎從 funasr
    切回 whisper、換模型、改教學類型詞庫),就自動重新轉錄——
    不會再拿舊引擎的結果充數(這曾造成「切了引擎但字幕沒變」)。"""
    engine = getattr(cfg, "ASR_ENGINE", "faster-whisper")
    if engine == "faster-whisper":
        return {"engine": engine,
                "model": getattr(cfg, "WHISPER_MODEL", ""),
                "language": getattr(cfg, "WHISPER_LANGUAGE", "zh"),
                "prompt": _build_initial_prompt()}
    return {"engine": engine,
            "model": getattr(cfg, "FUNASR_MODEL", "paraformer-zh"),
            "hotword": " ".join(effective_vocab())}


def transcribe(audio_path: str, cache_json: str | None = None) -> list[Word]:
    """對音訊做詞級轉錄。
    若提供 cache_json 且檔案存在、且當時的辨識設定跟現在一致,
    直接讀快取(省下重複轉錄的時間);設定變了就自動重轉。"""
    fp = _asr_fingerprint()
    if cache_json and os.path.exists(cache_json):
        try:
            with open(cache_json, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (ValueError, OSError):
            raw = None
        if isinstance(raw, dict) and raw.get("fingerprint") == fp:
            print(f"  讀取轉錄快取:{cache_json}")
            return [Word(**d) for d in raw.get("words", [])]
        if raw is not None:
            print("  辨識設定已變更(引擎/模型/語言/詞庫),重新轉錄…")

    engine = getattr(cfg, "ASR_ENGINE", "faster-whisper")
    if engine == "faster-whisper":
        words = _transcribe_faster_whisper(audio_path)
    elif engine == "funasr":
        words = _transcribe_funasr(audio_path)
    else:
        raise ValueError(f"未知的 ASR_ENGINE:{engine!r}("
                         "目前支援 'faster-whisper' 與 'funasr')")

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
    優先用完全自訂的 WHISPER_INITIAL_PROMPT;否則用教學類型 + 個人術語自動組。

    ⚠️ 提示詞裡一定要有「帶標點的示範句」。Whisper 會模仿提示詞的書寫風格:
    提示詞沒標點,它就吐出一整片沒有標點的字,字幕斷行只能靠停頓和字數硬切,
    句子會被切得很怪。實測同一段音訊、同一個模型:
        「以下是一段中文教學影片的口白。」        -> 0 個句號、2 個逗號
        加上帶標點的示範句                       -> 10 個句號、24 個逗號
    以前詞彙表那串「A、B、C。」剛好起了示範作用,所以詞彙表一清空就破功。
    現在把示範句寫死在基底,不管有沒有詞彙表都保證有標點。"""
    if getattr(cfg, "WHISPER_INITIAL_PROMPT", None):
        return cfg.WHISPER_INITIAL_PROMPT
    base = ("以下是一段中文教學影片的口白,內容標示標點符號。"
            "例如:今天我們來看這個設定,它會影響聲音的表現,"
            "你可以自己調整看看。")
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
        model_name = getattr(cfg, "FUNASR_MODEL", "paraformer-zh")
        print(f"  載入 FunASR 模型 {model_name}(首次會下載約 2GB)...")
        _funasr_model = AutoModel(model=model_name, vad_model="fsmn-vad",
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
    data = {
        "fingerprint": _asr_fingerprint(),   # 記下這批詞是用什麼設定轉的
        "words": [{"text": w.text, "start": w.start, "end": w.end}
                  for w in words],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cached_words(path: str) -> list[Word]:
    """讀快取裡的詞(不管當時用什麼引擎轉的;新舊兩種快取格式都吃)。
    給 live_subs 這類「後段工具」用——它們要的是『當初剪輯時用的那批詞』,
    跟現在面板選什麼引擎無關。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("words", [])
    return [Word(**d) for d in data]
