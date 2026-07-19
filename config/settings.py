"""
所有可調參數集中在這裡。調校時只改這個檔案,不用動邏輯程式碼。
第一次使用建議先跑審閱模式,看報告調整這些數字,再考慮全自動。
"""

# ============================================================
# 冗詞規則
# ============================================================

# 無條件刪除的填充音(語氣詞,幾乎不可能有語意)
# 這些信心高,審閱時不需要下 marker
FILLERS_ALWAYS = ["嗯", "呃", "啊", "欸", "唉", "痾", "喔"]

# 有條件刪除的詞(可能是連接詞也可能是口頭禪)
# 只在「句首孤立」或「連續重複」時刪,其餘保留
# 這些信心低,審閱時會下 marker 讓你確認
FILLERS_CONDITIONAL = ["就是", "然後", "那個", "這個", "所以說", "對對對"]

# 有條件冗詞的信心值(越低代表越需要人工確認)
CONDITIONAL_CONFIDENCE = 0.6

# ============================================================
# 交付方式
# ============================================================

# 交給 Premiere 的方式:
#   "baked" = 直接剪好(預設):照決策直接剪掉冗詞、處理靜音,
#             匯入就是剪完的成品。想改靈敏度(門檻、倍率…)不用重跑全部:
#             在面板改完設定按「重算剪輯」,幾秒產出一個新序列,舊的還在。
#   "live"  = 活專案:所有段落都保留、只切開並上顏色標籤
#             (靜音=粉紅、音樂=青綠、冗詞=紫),進 Premiere 再自己批次處理。
#             註:片段和標籤數量很多,長片在 Premiere 裡可能變卡,
#             實測後改為備選。
DELIVERY_MODE = "baked"

# ============================================================
# 靜音規則
# ============================================================

# 相鄰兩詞間隔超過這個秒數,視為靜音空隙
SILENCE_THRESHOLD_SEC = 1.2

# 靜音的處理方式:"delete"=直接剪掉  "speed"=快轉
SILENCE_ACTION = "speed"

# 若 SILENCE_ACTION="speed",快轉倍率
SILENCE_SPEED_FACTOR = 6.0

# 若 SILENCE_ACTION="speed",是否把快轉段的聲音抹成無聲。
# True = 看得到快轉畫面,但那段沒有聲音(避免加速造成的尖聲/花栗鼠音)
# False = 快轉段保留原聲(會變高音)
MUTE_SPEED_AUDIO = True

# 保護緩衝:靜音段前後各保留這麼多秒,避免切太緊把氣音也切掉
SILENCE_PADDING_SEC = 0.15

# ============================================================
# 音樂/音效保護
# ============================================================
# 教學片常會播一段音樂或音效給觀眾聽,那段沒有講話、卻不能被
# 當成靜音剪掉或快轉。開啟後會用音量能量把這種段落找出來保護。

# 是否啟用音樂/音效偵測
MUSIC_DETECT = True

# 靈敏度:比「底噪」高出多少分貝才算有聲音(音樂)。
# 數字越小越敏感(容易把呼吸聲、椅子聲也當成音樂留下來);
# 越大越保守(小聲的音樂可能漏掉)。一般 8~16 之間。
MUSIC_DB_ABOVE_FLOOR = 12.0

# 短於這個秒數的聲響不算音樂(咳嗽、滑鼠喀一聲之類)
MUSIC_MIN_SEC = 0.4

# ============================================================
# 字幕
# ============================================================

SUBTITLE_MAX_CHARS = 18          # 每行最多中文字數
SUBTITLE_MAX_GAP_SEC = 0.5       # 詞間隔超過這個秒數就換行
CONVERT_TO_TRADITIONAL = True    # OpenCC 簡轉繁

# ============================================================
# 審閱 marker 過濾
# ============================================================

# 只有刪除長度 >= 這個毫秒數的切點才下 marker(太短的不值得看)
MARKER_MIN_DURATION_MS = 200

# 只有信心 < 這個值的切點才下 marker(高信心必刪的不用看)
MARKER_MAX_CONFIDENCE = 0.9

# ============================================================
# 語音辨識(ASR)
# ============================================================

# 使用哪個辨識引擎:
#   "faster-whisper" = 目前實作(Whisper,泛用、支援多語)
#   "funasr"         = 預留:阿里 FunASR / Paraformer,中文通常更準(尚未實作)
# 要接新引擎,只需在 modules/transcribe.py 加一個對應的函式,
# 輸出一樣的 list[Word] 即可,其餘管線完全不用動。
ASR_ENGINE = "faster-whisper"

# --- FunASR 專用參數(ASR_ENGINE="funasr" 時生效)---
FUNASR_MODEL = "paraformer-zh"   # 目前支援 paraformer-zh(純中文較準)

# --- Whisper 專用參數(ASR_ENGINE="faster-whisper" 時生效)---
WHISPER_MODEL = "large-v3"       # 準確度優先;GPU 不夠力可改 "medium"
WHISPER_LANGUAGE = "zh"
WHISPER_DEVICE = "cuda"          # 你有 NVIDIA GPU
WHISPER_COMPUTE_TYPE = "float16" # GPU 用 float16;若報錯改 "int8_float16"

# 教學類型詞庫:選了對應類型,該領域常見術語會自動加進辨識提示,大幅減少
# 專有名詞被聽錯(例如 MIDI 被聽成「謎底」)。可複選(見 VOCAB_CATEGORIES)。
VOCAB_PRESETS = {
    "剪輯": ["Premiere", "Pro", "時間軸", "序列", "轉場", "關鍵影格",
             "字幕", "渲染", "輸出", "調色", "遮罩", "軌道", "外掛"],
    "編曲": ["FL Studio", "Pattern", "Mixer", "MIDI", "Playlist",
             "Piano Roll", "Snare", "Kick", "Clap", "Hi-hat", "Sample",
             "BPM", "VST", "EQ", "Reverb", "Compressor", "取樣"],
    "特效": ["After Effects", "AE", "圖層", "遮罩", "關鍵影格", "合成",
             "表達式", "預合成", "軌道遮罩", "父級", "錨點"],
    "遊戲": ["實況", "角色", "關卡", "地圖", "裝備", "技能", "副本",
             "連段", "血量", "魔王", "攻略", "支線"],
    "程式": ["Python", "函式", "變數", "參數", "迴圈", "字串", "陣列",
             "編譯", "除錯", "套件", "終端機"],
    "攝影": ["光圈", "快門", "ISO", "白平衡", "焦距", "景深",
             "構圖", "曝光", "後製", "RAW"],
}

# 你這支/這批影片屬於哪些教學類型(可複選)。面板 UI 會做成勾選。
VOCAB_CATEGORIES = ["剪輯"]

# 你個人的額外術語(頻道名、人名、慣用詞…),補充在教學類型之外。
CUSTOM_VOCAB = []

# 提示詞(給辨識引擎的開場提示)。
#   None = 自動用上面的 CUSTOM_VOCAB 組出一句(建議,平常只要改詞表就好)
#   填一段話 = 完全自訂,忽略 CUSTOM_VOCAB
WHISPER_INITIAL_PROMPT = None

# ============================================================
# 音訊清理
# ============================================================

# 走哪條路:
#   "vst"        = 載入你的 VST 鏈(需先填 VST_CHAIN)
#   "opensource" = DeepFilterNet 降噪(需另外安裝 Rust 才能裝這個套件)
#   "none"       = 不處理聲音,直接用原始音訊(第一次測試整條管線用這個最快)
AUDIO_MODE = "vst"

# 降噪要不要「烘進」音檔:
#   False = 不烘(建議,活專案理念):交出去的聲音保持原始錄音
#           (只做響度標準化),降噪交給 Premiere——在音軌混音器
#           對 A1 軌掛一次 VoiceFX,整軌生效、隨時可調可關。
#   True  = 烘進去(舊行為):先用下面的 VST 鏈把聲音處理好再交接,
#           之後在 Premiere 裡改不了。
VST_BAKE = False

# VST 模式:你的 .vst3 檔案路徑,依序套用(降噪->EQ->壓縮->limiter)
VST_CHAIN = [
    # NVIDIA AI 降噪。注意:這個外掛要指到「內層」的 .vst3 檔,不是外層資料夾
    r"C:\Program Files\Common Files\VST3\TonPlugIns\VoiceFX.vst3\Contents\x86_64-win\VoiceFX.vst3",
    # 之後想加 EQ / 壓縮,再把 .vst3 路徑接在這後面
]

# VoiceFX(NVIDIA AI 降噪)的兩個參數,直接用面板滑條/下拉控制,不必開外掛視窗。
# 只有當 VST_CHAIN 裡有 VoiceFX 這類外掛時才有作用;其他外掛沒有這兩個參數會自動略過。
#   VOICEFX_MODE      要消除什麼:"消噪音" / "消回音" / "兩者都消"
#   VOICEFX_INTENSITY 降噪強度 0~100(越大清得越乾淨,但太大可能讓人聲失真)
VOICEFX_MODE = "消噪音"
VOICEFX_INTENSITY = 100.0

# 目標響度(YouTube 標準)
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK = -1.0


# ============================================================
# 個人覆寫(選用)
# ============================================================
# 若在 config/ 底下建立一個 settings_local.py,裡面所有「全大寫」的設定
# 會蓋掉上面的預設值。這個檔不進版控(見 .gitignore),
# 方便你保留自己的門檻、詞表、VST 路徑,而不動到共用設定、
# 也不會在更新專案時被覆蓋。
#
# 範例 settings_local.py:
#     CUSTOM_VOCAB = ["我的頻道名", "常用術語"]
#     SILENCE_SPEED_FACTOR = 8.0
try:
    from config import settings_local as _local
    for _name in dir(_local):
        if _name.isupper():
            globals()[_name] = getattr(_local, _name)
except ImportError:
    pass

# JSON 覆寫:Premiere 面板 UI 會把使用者調整的設定寫進 settings_local.json
# (JSON 比 Python 安全好寫)。同樣只認全大寫鍵,且在 .py 覆寫之後套用。
import os as _os, json as _json
_json_path = _os.path.join(_os.path.dirname(__file__), "settings_local.json")
if _os.path.exists(_json_path):
    try:
        with open(_json_path, "r", encoding="utf-8") as _f:
            for _k, _v in _json.load(_f).items():
                if _k.isupper():
                    globals()[_k] = _v
    except (ValueError, OSError):
        pass
