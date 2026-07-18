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

# --- Whisper 專用參數(ASR_ENGINE="faster-whisper" 時生效)---
WHISPER_MODEL = "large-v3"       # 準確度優先;GPU 不夠力可改 "medium"
WHISPER_LANGUAGE = "zh"
WHISPER_DEVICE = "cuda"          # 你有 NVIDIA GPU
WHISPER_COMPUTE_TYPE = "float16" # GPU 用 float16;若報錯改 "int8_float16"

# 你常講的專有名詞/術語/軟體名/人名,列在這裡可提高辨識準確度。
# 例:剪 Premiere 教學就放剪輯詞;剪 FL Studio 就放編曲詞。兩種都放也可以。
CUSTOM_VOCAB = [
    "Premiere", "Pro", "FL Studio", "Pattern", "Mixer", "MIDI",
    "VST", "EQ", "時間軸", "字幕", "渲染", "外掛",
]

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

# VST 模式:你的 .vst3 檔案路徑,依序套用(降噪->EQ->壓縮->limiter)
VST_CHAIN = [
    # NVIDIA AI 降噪。注意:這個外掛要指到「內層」的 .vst3 檔,不是外層資料夾
    r"C:\Program Files\Common Files\VST3\TonPlugIns\VoiceFX.vst3\Contents\x86_64-win\VoiceFX.vst3",
    # 之後想加 EQ / 壓縮,再把 .vst3 路徑接在這後面
]

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
