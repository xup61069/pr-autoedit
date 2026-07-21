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


# Whisper 的提示詞有硬性長度上限:它只會保留「最後」這麼多 token,
# 超出的部分從開頭砍掉(faster_whisper transcribe.py 的 max_length//2-1)。
# 這個數字不是我們能調的,是模型本身的限制。
_PROMPT_TOKEN_BUDGET = 223


def _est_tokens(s: str) -> int:
    """估算一段文字大約幾個 token(不必載入模型,估個大概就夠用)。

    刻意「往多的估」。低估的下場是自以為沒超標、實際超標,模型默默把
    詞彙砍掉而我們毫不知情;高估頂多少放幾個術語,代價小得多。
    權重是拿 large-v3 的實際 tokenizer 對六組詞庫 + 示範句校準出來的,
    確認每一組都估得比實際多(比值 1.00~1.44)。"""
    n = 0.0
    for ch in s:
        n += 0.5 if ch.isascii() else 1.4
    return int(n + 0.5)


def effective_vocab() -> list[str]:
    """合併『教學類型詞庫』(VOCAB_CATEGORIES 選到的)與個人額外術語
    (CUSTOM_VOCAB),去重、保序。這是辨識提示詞/熱詞的實際用詞。

    順序上「個人術語」排最前面:詞彙表可能超過提示詞長度上限而被截掉尾巴,
    你自己填的頻道名、人名最不該被犧牲,所以讓它最先進場。"""
    terms: list[str] = list(getattr(cfg, "CUSTOM_VOCAB", []) or [])
    presets = getattr(cfg, "VOCAB_PRESETS", {}) or {}
    for cat in getattr(cfg, "VOCAB_CATEGORIES", []) or []:
        terms += presets.get(cat, [])
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
    現在把示範句寫死在基底,不管有沒有詞彙表都保證有標點。

    ⚠️ 示範句一定要放在「最後面」。Whisper 只保留提示詞的最後 223 個 token,
    超過就從開頭砍。示範句若排在前面,詞彙表一長就會把它整句砍掉——
    標點又會全部消失,而且完全沒有徵兆。所以順序是「詞彙表 → 示範句」,
    真的太長時被犧牲的是詞彙表尾巴(頂多某個術語聽錯),不是標點。"""
    if getattr(cfg, "WHISPER_INITIAL_PROMPT", None):
        return cfg.WHISPER_INITIAL_PROMPT

    demo = ("以下是一段中文教學影片的口白,內容標示標點符號。"
            "例如:今天我們來看這個設定,它會影響聲音的表現,"
            "你可以自己調整看看。")

    vocab = effective_vocab()
    if not vocab:
        return demo

    # 一個一個加進去,每加一個就量「整句組好的樣子」有多長。
    # 不用「總額度減一減」的算法:那樣得自己估分隔符號、開頭、句號的成本,
    # 少算一點就會超標(頓號其實比想像中貴)。直接量成品最不會錯。
    prefix, suffix = "常見詞彙:", "。"
    kept: list[str] = []
    for t in vocab:
        trial = prefix + "、".join(kept + [t]) + suffix + demo
        if _est_tokens(trial) > _PROMPT_TOKEN_BUDGET:
            break
        kept.append(t)

    if not kept:                            # 極端情況:一個詞都塞不下
        return demo

    if len(kept) < len(vocab):
        print(f"  ⚠ 詞彙表太長,這次只用了前 {len(kept)} 個(共 {len(vocab)} 個)。"
              "辨識提示詞有長度上限,超過的部分模型看不到。\n"
              "    想讓某些術語一定生效:減少「教學類型」的勾選數量,"
              "或把最重要的詞填進「我的額外術語」(它排最前面)。")

    return prefix + "、".join(kept) + suffix + demo


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

    # faster-whisper 的 segments 是「產生器」——真正的辨識是在下面這個
    # 迴圈裡一段一段跑出來的。這是整條管線最久的一步(長片好幾分鐘),
    # 以前中間完全沒有輸出,使用者分不出還在跑還是當掉了。
    # 每段都有 end(原始音訊的秒數),拿它跟總長度比就是進度。
    from modules.progress import Reporter
    total = float(getattr(info, "duration", 0.0) or 0.0)
    rep = Reporter("語音轉錄", total, unit="分", scale=1 / 60)

    words: list[Word] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append(Word(
                    text=w.word.strip(),
                    start=w.start,
                    end=w.end,
                ))
        rep.update(getattr(seg, "end", 0.0) or 0.0)
    rep.done()

    # 轉錄完就把模型放掉,不要一路佔著顯示卡記憶體到程式結束。
    # 後面還有混音、產 XML 等步驟(4K 影片可能要跑上一分鐘),
    # 這段期間白白佔著好幾 GB 的 VRAM,會排擠 Premiere 自己的用量。
    _release_gpu(model)
    return words


def _release_gpu(model=None) -> None:
    """放掉模型並歸還顯示卡記憶體。各種釋放手段都可能不存在,所以全部包起來。"""
    import gc
    if model is not None:
        try:
            del model
        except Exception:
            pass
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


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
