"""
面板 UI ↔ 設定 的橋樑。

  python ui_settings.py dump   印出 JSON:UI 欄位定義 + 目前的值 + 教學類型清單

面板讀這份 JSON 自動產生表單;使用者調整後,面板把改動寫進
config/settings_local.json(見 config/settings.py 尾端的 JSON 覆寫),
下次跑 pipeline 就生效。

欄位新增只要改這裡的 FIELDS,面板 UI 會自動長出對應控制項。

每個欄位可用的鍵:
  key      設定名(對應 config.settings 的全大寫變數)
  label    白話標題
  type     控制項型別:
             select(下拉,配 options) / number(數字滑條,配 min/max/step) /
             bool(勾選) / list(逗號分隔清單) / category(教學類型複選) /
             vstlist(VST 檔路徑清單)
           註:不要加「可打字下拉」那種型別。它得靠 <datalist>,
           而那個元素在 CEP 的舊瀏覽器核心不可靠(實測會整個不顯示),
           下拉是空的而且看不出原因。要打字就用 list,要選就用 select。
  tier     common=基本頁 / advanced=進階頁
  group    同一頁裡的分組標題(進階頁的分組預設折疊,見 COLLAPSED_GROUPS)
  hint     欄位下方的白話說明
  options  select/combo 的選項
  min/max/step  number 用(default 不用寫,dump 時自動從 config.settings
                的預設值快照填入,不會有「兩邊抄不一樣」的問題)
  soft     True=這個數字沒有真正的上下限,滑條維持 min/max,但輸入框可超出
  show_if  只有在別的欄位等於指定值時才顯示,例如
           {"SILENCE_ACTION": ["speed"]} = 只有停頓處理選 speed 時才顯示
"""
from __future__ import annotations
import sys, json, os

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config.settings as cfg

# 進階頁裡「預設折疊」的分組(基本頁的分組一律展開)
# 註:降噪外掛(VST)刻意不折疊,讓使用者一眼看到降噪設定
COLLAPSED_GROUPS = [
    "冗詞與口頭禪", "字幕", "審閱標記", "音樂/音效保護", "畫面活動", "其他微調",
    "辨識效能",
]

FIELDS = [
    # ================= 基本頁 =================
    # --- 分組:辨識 ---
    {"key": "VOCAB_CATEGORIES", "label": "教學類型", "type": "category",
     "tier": "common", "group": "辨識",
     "hint": "載入該領域術語,提升專有名詞辨識率。可複選,選太多會超出長度上限被截斷"},
    # 放在教學類型正下方:它跟教學類型是同一件事的兩半(現成的 vs 你自己的),
    # 分開放使用者根本找不到。以前它在「進階頁 > 冗詞與口頭禪」而且預設折疊,
    # 名字又跟冗詞無關,等於藏了三層。
    {"key": "CUSTOM_VOCAB", "label": "我的額外術語", "type": "list",
     "tier": "common", "group": "辨識",
     "hint": "頻道名、人名、慣用詞,逗號分隔。優先權最高、不會被截斷,"
             "不能聽錯的詞放這裡"},
    {"key": "ASR_ENGINE", "label": "辨識引擎", "type": "select",
     "tier": "common", "group": "辨識", "options": ["faster-whisper", "funasr"],
     "hint": "faster-whisper 通用,中英夾雜較佳;funasr 純中文備選"},
    {"key": "WHISPER_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["large-v3", "medium", "small", "base"],
     "show_if": {"ASR_ENGINE": ["faster-whisper"]},
     "hint": "large-v3 最準但吃顯卡,不夠力改 medium"},
    {"key": "FUNASR_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["paraformer-zh"],
     "show_if": {"ASR_ENGINE": ["funasr"]},
     "hint": "目前僅支援 paraformer-zh"},
    {"key": "WHISPER_LANGUAGE", "label": "影片主要語言", "type": "select",
     "tier": "common", "group": "辨識",
     "options": ["auto", "zh", "en", "ja", "ko", "yue", "de", "fr", "es"],
     "hint": "auto 自動偵測"},
    # 這兩個放進面板的理由很實際:出事的時候要調的就是它們。
    # 以前面板的錯誤說明會叫使用者「編輯 config/settings_local.json,
    # 加一行 WHISPER_DEVICE: cpu」——那是整個面板唯一一處叫零程式基礎的人
    # 去手改 JSON 的地方,而且改錯一個引號整支程式就起不來。
    {"key": "WHISPER_DEVICE", "label": "用什麼跑辨識", "type": "select",
     "tier": "advanced", "group": "辨識效能",
     "options": ["cuda", "cpu"],
     "show_if": {"ASR_ENGINE": ["faster-whisper"]},
     "hint": "cuda 用 NVIDIA 顯卡(快很多)。顯卡出問題時改 cpu,慢但一定會動"},
    {"key": "WHISPER_COMPUTE_TYPE", "label": "辨識運算精度", "type": "select",
     "tier": "advanced", "group": "辨識效能",
     "options": ["float16", "int8_float16", "int8", "float32"],
     "show_if": {"ASR_ENGINE": ["faster-whisper"]},
     "hint": "顯卡用 float16。報 float16 相關錯誤改 int8_float16;"
             "選 cpu 跑要改 int8 或 float32"},

    # --- 分組:剪輯 ---
    {"key": "AUDIO_MODE", "label": "聲音處理方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["vst", "none", "opensource"],
     "hint": "vst 走降噪外掛;none 不處理,測流程最快"},
    {"key": "DELIVERY_MODE", "label": "交付方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["baked", "live"],
     "hint": "baked 直接剪好(建議),改設定按「重算剪輯」幾秒出新序列;"
             "live 全保留只上色標,自行批次處理——片段多,長片會卡"},
    {"key": "SILENCE_ACTION", "label": "停頓處理方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["auto", "speed", "delete"],
     "hint": "auto 看畫面決定(建議):畫面在動快轉帶過、靜止才剪掉,"
             "默默示範的畫面不會消失;"
             "speed 一律快轉,不刪東西;delete 一律剪掉,最兇"},
    {"key": "SILENCE_SPEED_FACTOR", "label": "快轉倍率", "type": "number",
     "tier": "common", "group": "剪輯", "min": 1, "max": 20, "step": 0.5,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto", "speed"]},
     "hint": "停頓段加速幾倍"},
    {"key": "MUTE_SPEED_AUDIO", "label": "快轉段消音", "type": "bool",
     "tier": "common", "group": "剪輯",
     "show_if": {"SILENCE_ACTION": ["auto", "speed"]},
     "hint": "建議開啟,避免加速產生的變調尖聲"},
    {"key": "SILENCE_THRESHOLD_SEC", "label": "靜音判定門檻", "type": "number",
     "tier": "common", "group": "剪輯", "min": 0.1, "max": 5, "step": 0.1,
     "soft": True,
     "hint": "間隔超過幾秒才算停頓。講話慢調高"},
    {"key": "MUSIC_DETECT", "label": "音樂/音效保護", "type": "bool",
     "tier": "common", "group": "剪輯",
     "hint": "保護沒講話但有聲音的段落(示範音樂、音效),不被當停頓剪掉"},
    {"key": "NOISE_TRIM", "label": "剪掉短促雜音", "type": "bool",
     "tier": "common", "group": "剪輯", "show_if": {"MUSIC_DETECT": [True]},
     "hint": "建議開啟。咳嗽、清喉嚨、滑鼠聲這類短促聲響直接剪掉,"
             "夠長的才當示範音樂保護。講話中途咳的分不出來,剪不掉"},
    # 註:「無語音時依畫面判定」以前是這裡的一個獨立勾選,現在併進上面的
    # 「停頓處理方式 = auto」。兩個設定並存時會互相蓋掉(選了快轉,畫面靜止的
    # 停頓還是被剪掉),而且報告看不出來,所以做成三選一。
    {"key": "MICRO_TRIM", "label": "能量微剪", "type": "bool",
     "tier": "common", "group": "剪輯",
     "hint": "建議開啟,剪更兇的主力。連句中的小空檔也剪掉,"
             "17 分鐘的片再省 2~3 分鐘,不損失內容"},
    {"key": "MICRO_TRIM_KEEP_SEC", "label": "微剪:保留緩衝", "type": "number",
     "tier": "common", "group": "剪輯", "min": 0, "max": 0.3, "step": 0.01,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "每個停頓頭尾各留幾秒。太急促調大,還是拖調小"},

    # --- 分組:完成後 ---
    {"key": "AUTO_OPEN_REPORT", "label": "自動開啟報告", "type": "bool",
     "tier": "common", "group": "完成後",
     "hint": "報告一分鐘看完剪了哪裡、省多少時間"},

    # ================= 進階頁 =================
    # --- 分組:降噪外掛(VST)---
    {"key": "VST_BAKE", "label": "降噪烘進音檔", "type": "bool",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": "建議關閉。關閉時聲音保持原樣,降噪在 Premiere 掛、隨時可調;"
             "開啟則先烘進音檔,之後改不了"},
    {"key": "VST_CHAIN", "label": "VST 外掛路徑", "type": "vstlist",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": ".vst3 完整路徑,依序套用。要指到內層那顆檔,不是外層資料夾"},
    {"key": "VOICEFX_MODE", "label": "降噪:消除對象", "type": "select",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "options": ["消噪音", "消回音", "兩者都消"],
     "show_if": {"AUDIO_MODE": ["vst"], "VST_BAKE": [True]},
     "hint": "房間有回音選「兩者都消」。僅烘進音檔時有作用"},
    {"key": "VOICEFX_INTENSITY", "label": "降噪:強度", "type": "number",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "min": 0, "max": 100, "step": 1,
     "show_if": {"AUDIO_MODE": ["vst"], "VST_BAKE": [True]},
     "hint": "越大清得越乾淨,過大會讓人聲變悶"},

    # --- 分組:冗詞與口頭禪 ---
    {"key": "FILLERS_ALWAYS", "label": "無條件刪除語氣詞", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "嗯、呃、啊這類,看到就刪。逗號分隔"},
    {"key": "FILLERS_CONDITIONAL", "label": "口頭禪清單", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "只在句首孤立或連續重複時刪,其餘保留。逗號分隔"},
    {"key": "CONDITIONAL_CONFIDENCE", "label": "口頭禪判定把握度", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0, "max": 1, "step": 0.05,
     "hint": "越低越沒把握,標記請你確認的也越多"},
    {"key": "RETAKE_DETECT", "label": "重講偵測", "type": "bool",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "建議關閉。實測效益很小,又容易把排比句(左側是…右側是…)"
             "當成重講砍掉。開啟時每處都會下標記,務必看報告"},
    {"key": "RETAKE_SIMILARITY", "label": "重講相似度門檻", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0.6, "max": 1.0, "step": 0.05,
     "show_if": {"RETAKE_DETECT": [True]},
     "hint": "0.9 只抓一字不差的,最保守;0.7 抓得多但易誤砍排比句"},
    {"key": "FILLER_PAUSE_SEC", "label": "語氣詞刪除的停頓要求", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0, "max": 0.5, "step": 0.05,
     "soft": True,
     "hint": "0 看到就刪(建議)。只有 funasr 要設 0.1——它把「好啊」拆成兩字,"
             "不設限會誤刪句尾的「啊」"},
    {"key": "FILLER_ISOLATED_GAP_SEC", "label": "口頭禪孤立判定門檻", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0.05, "max": 1.0, "step": 0.05,
     "soft": True,
     "hint": "前方停頓超過幾秒,就把「然後、就是」當句首語助詞刪掉。"
             "調小刪更多,誤刪會下標記"},
    {"key": "MICRO_TRIM_MIN_SEC", "label": "微剪:最短長度", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 0.1, "max": 1.0, "step": 0.05,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "至少能剪掉這麼久才動手,太小會把說話節奏剁碎"},
    {"key": "MICRO_TRIM_DB_BELOW_SPEECH", "label": "微剪:靜音門檻", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 10, "max": 40, "step": 1,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "低於說話音量幾分貝算無聲。調大連氣音也剪,調小較保守"},
    # 這是防「微剪把整個字吃掉」的安全閥。它預設就開著、也很少需要關,
    # 但它直接決定「字幕會不會缺字」——曾經有 14.4% 的字從字幕消失就是
    # 這件事。看得到才確認得了它是開的,所以放進面板。
    {"key": "MICRO_TRIM_PROTECT_WORDS", "label": "微剪:不要剪掉整個字",
     "type": "bool", "tier": "advanced", "group": "其他微調",
     "show_if": {"MICRO_TRIM": [True]},
     "hint": "保持開啟。輕聲的短字(你、它、的)音量本來就低,"
             "整個被剪掉會少一個字、字幕也缺字"},
    # --- 分組:字幕 ---
    {"key": "SUBTITLE_MAX_CHARS", "label": "字幕行長上限", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 8, "max": 40, "step": 1,
     "soft": True,
     "hint": "超過幾個字換行"},
    {"key": "SUBTITLE_MAX_GAP_SEC", "label": "字幕斷行停頓門檻", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 0.1, "max": 2, "step": 0.1,
     "soft": True,
     "hint": "停頓超過幾秒換行"},
    {"key": "SUBTITLE_MAX_CHARS_NO_PUNCT", "label": "字幕行長上限(逐字稿沒標點時)",
     "type": "number", "tier": "advanced", "group": "字幕",
     "min": 8, "max": 40, "step": 1, "soft": True,
     "hint": "辨識結果幾乎沒標點時改用這個較短的上限。"
             "沒標點只能靠停頓和字數斷行,行太長會斷在很怪的地方"},
    {"key": "CONVERT_TO_TRADITIONAL", "label": "簡體轉繁體", "type": "bool",
     "tier": "advanced", "group": "字幕",
     "hint": "辨識偶爾會吐簡體字,開著保險"},

    # --- 分組:審閱標記 ---
    {"key": "MARKER_MIN_DURATION_MS", "label": "標記最短長度", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 2000, "step": 50,
     "soft": True,
     "hint": "短於這個長度的切點不下標記,單位毫秒"},
    {"key": "MARKER_MAX_CONFIDENCE", "label": "標記把握度門檻", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 1, "step": 0.05,
     "hint": "只有低於這個把握度的切點才下標記"},

    # --- 分組:畫面活動(只有停頓處理方式選 auto 時才有作用)---
    {"key": "MOTION_SENSITIVITY", "label": "畫面活動靈敏度", "type": "number",
     "tier": "advanced", "group": "畫面活動", "min": 0.1, "max": 5, "step": 0.1,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto"]},
     "hint": "越小越敏感。示範被剪掉調小,滑鼠晃一下就不剪調大"},
    {"key": "MOTION_MIN_SEC", "label": "畫面判定最短長度", "type": "number",
     "tier": "advanced", "group": "畫面活動", "min": 0.2, "max": 3, "step": 0.1,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto"]},
     "hint": "短於幾秒的停頓不看畫面,一律快轉"},
    {"key": "MOTION_SAMPLE_FPS", "label": "畫面取樣頻率", "type": "number",
     "tier": "advanced", "group": "畫面活動", "min": 1, "max": 10, "step": 1,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto"]},
     "hint": "每秒比對幾張畫面。4 張已足夠,調高只會變慢"},

    # --- 分組:音樂/音效保護 ---
    {"key": "MUSIC_DB_ABOVE_FLOOR", "label": "音樂偵測靈敏度", "type": "number",
     "tier": "advanced", "group": "音樂/音效保護", "min": 3, "max": 30, "step": 1,
     "soft": True,
     "hint": "高於底噪幾分貝才算音樂。音樂被誤剪調小,呼吸聲被誤留調大"},
    {"key": "MUSIC_MIN_SEC", "label": "音樂最短長度", "type": "number",
     "tier": "advanced", "group": "音樂/音效保護", "min": 0.1, "max": 3, "step": 0.1,
     "soft": True,
     "hint": "短於幾秒的聲響(咳嗽、滑鼠)不算音樂,會被剪掉"},

    # --- 分組:其他微調 ---
    {"key": "SILENCE_PADDING_SEC", "label": "停頓邊界緩衝", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 0, "max": 1, "step": 0.05,
     "soft": True,
     "hint": "停頓前後各留幾秒,避免切掉氣音"},
    {"key": "VIDEO_ENCODER", "label": "重新編碼格式", "type": "select",
     "tier": "advanced", "group": "其他微調",
     "options": ["auto", "av1_nvenc", "hevc_nvenc", "h264_nvenc",
                 "libx265", "libx264"],
     "hint": "多數影片用不到——畫面是無損複製,只有少數複製後會壞才需重編。"
             "auto 自動挑最省空間的(建議);指定的不可用會自動換,不會失敗"},
    {"key": "VIDEO_QUALITY", "label": "重新編碼畫質", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 15, "max": 35, "step": 1,
     "soft": True,
     "hint": "數字越小畫質越好、檔案越大。常用 20~28"},
    {"key": "TARGET_LUFS", "label": "目標響度", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": -30, "max": -6, "step": 0.5,
     "soft": True,
     "hint": "整體音量標準,YouTube 建議 -14"},
]


def fields_with_defaults() -> list[dict]:
    """把每個欄位的「內建預設值」補上去(給面板的『點兩下恢復預設』用)。

    預設值一律取自 config.settings 的 DEFAULTS 快照,不在這裡另抄一份 ——
    以前兩邊各寫一份,改了 settings.py 忘了改這裡,點兩下就會恢復成早就
    不用的舊值(音樂最短秒數就發生過:這裡寫 0.4、實際預設已是 1.2)。"""
    defaults = getattr(cfg, "DEFAULTS", {})
    out = []
    for f in FIELDS:
        g = dict(f)
        if f["key"] in defaults:
            g["default"] = defaults[f["key"]]
        out.append(g)
    return out


def _all_presets() -> tuple[dict, list]:
    """內建組合 + 你自己存的組合。回傳(全部組合, 哪些是你存的)。

    你存的放在 config/presets_local.json(不進版控)。同名時以你的為準 ——
    你顯然比較清楚自己要什麼。"""
    presets = dict(getattr(cfg, "SETTING_PRESETS", {}))
    mine: list[str] = []
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "config", "presets_local.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                local = json.load(f)
            if isinstance(local, dict):
                presets.update(local)
                mine = sorted(local.keys())
        except (ValueError, OSError):
            pass
    return presets, mine


# 面板的程式邏輯要用、但不做成表單控制項的設定。
#
# 不是每個設定都適合做成控制項:人聲效果鏈是巢狀清單(沒有對應的控制項型別)、
# 片段數上限是安全閥而不是日常會調的東西。但面板還是得「讀得到」它們,
# 否則使用者在 settings_local.json 改了卻完全沒作用,而且從畫面上看不出來
# ——DENOISE_PER_CLIP_MAX 以前就是這樣,面板永遠讀不到、只能吃寫死的 20。
#
# 放這裡是安全的:面板存檔時只收「表單控制項」上的值(見 main.js 的
# collectValues),所以這些鍵不會被反寫進 settings_local.json 釘死。
PANEL_EXTRA_KEYS = ["PREMIERE_VOICE_FX", "DENOISE_PER_CLIP_MAX"]

# 「刻意不放進面板」的設定,每一個都要寫理由。
#
# 為什麼要有這張表:設定加在 config/settings.py 很容易,忘了回來加進 FIELDS
# 也很容易——結果就是「程式在用、但面板上找不到」,使用者只能去手改
# settings_local.json,而且從畫面上完全看不出來有這個東西。
# 曾經一次累積了十個(DENOISE_PER_CLIP_MAX、WHISPER_DEVICE… )才被發現。
#
# 現在 tests/test_e2e_smoke.py 會檢查:每一個設定都必須「在 FIELDS 裡」、
# 「在 PANEL_EXTRA_KEYS 裡」、或「在這張表裡並寫明理由」,三者選一。
# 新增設定時你會被逼著做這個決定,而不是默默漏掉。
PANEL_OMITTED_KEYS = {
    # --- 關於設定本身的設定,不是使用者會調的東西 ---
    "PRESET_KEYS": "定義「設定組合會動到哪些欄位」,是機制不是參數",
    "SETTING_PRESETS": "內建組合的內容;面板有專用的組合選單可以套用/另存",
    "VOCAB_PRESETS": "教學類型詞庫;面板有專用的「✎ 編輯類型」編輯器",

    # --- 有意識地不做成控制項 ---
    "WHISPER_INITIAL_PROMPT":
        "填了會整個蓋掉自動提示詞,連尾巴那句「標點示範句」一起換掉,"
        "字幕標點會全部消失而且毫無徵兆。做成輸入框等於把陷阱擺在使用者面前。"
        "要加術語請用 CUSTOM_VOCAB(面板上的「我的額外術語」)",
    "TARGET_TRUE_PEAK":
        "真峰值上限 -1.0 dBTP 是串流平台的通用值,調它沒有實際好處",

    # --- 重講偵測的細部參數 ---
    # 這功能預設關閉、實測效益很小(4 支片 55 分鐘只抓到 12 秒有效的),
    # 面板已經有主開關和相似度兩顆。再擺四顆旋鈕只會讓進階頁更難找東西,
    # 而它們的調整價值接近零。真的要調的人有能力去改 settings.py。
    "RETAKE_MIN_CHARS":
        "重講偵測的細部門檻(至少重複幾個字才算)。功能預設關閉、實測效益很小,"
        "面板已有主開關與相似度兩顆,再擺旋鈕只會讓進階頁更難找東西",
    "RETAKE_MAX_CHARS":
        "重講偵測一次最多砍幾個字。同樣是細部門檻,調它的價值遠低於"
        "「要不要開這個功能」,而那顆面板上有",
    "RETAKE_CONFIDENCE":
        "重講刪除的信心值,它的作用只是「保證低於標記門檻所以一定下 marker」,"
        "調高反而會讓誤砍的地方不再被標記出來——不該讓人不小心關掉這個保護",
    "RETAKE_BOUNDARY_GAP_SEC":
        "重講交界要求的停頓長度。這是擋掉排比句誤判的主力,實測調鬆之後"
        "抓到的幾乎都是「左側是…右側是…」這種正常修辭,放出來只會鼓勵調壞它",
}

# 有標題的設定,報告的「本次設定」表才印得出人看得懂的名字。
# 詞庫不是表單欄位(它有自己的編輯器),但改了要看得出來,所以補一個標題。
EXTRA_LABELS = {"VOCAB_PRESETS": "教學類型詞庫"}


def _vocab_budget() -> dict:
    """提示詞的長度預算,交給面板即時試算用。

    刻意從 transcribe 把數字「借」過來而不是在面板那邊抄一份:
    抄一份的話,哪天調了權重或示範句,面板算出來的就跟實際不一樣,
    而使用者只會看到「明明說放得下,結果術語沒生效」。"""
    from modules.transcribe import (_est_tokens, _PROMPT_TOKEN_BUDGET,
                                    _build_initial_prompt)
    old = cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB
    cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB = [], []
    demo = _build_initial_prompt()          # 沒有詞彙表時 = 只剩示範句
    cfg.VOCAB_CATEGORIES, cfg.CUSTOM_VOCAB = old
    return {
        "total": _PROMPT_TOKEN_BUDGET,
        "demo": _est_tokens(demo),
        "wrapper": _est_tokens("常見詞彙:。"),
        # _est_tokens 的權重:ASCII 0.5、其餘 1.4,最後四捨五入
        "ascii": 0.5,
        "cjk": 1.4,
    }


def dump() -> None:
    values = {f["key"]: getattr(cfg, f["key"], None) for f in FIELDS}
    for _k in PANEL_EXTRA_KEYS:
        values[_k] = getattr(cfg, _k, None)
    presets, mine = _all_presets()
    builtin_vocab = getattr(cfg, "DEFAULTS", {}).get("VOCAB_PRESETS", {})
    vocab = getattr(cfg, "VOCAB_PRESETS", {})
    out = {
        "fields": fields_with_defaults(),
        "categories_available": list(vocab.keys()),
        # 教學類型編輯器要用的三份資料:
        #   vocab_presets  現在實際生效的詞(內建 + 你改過的)
        #   builtin_vocab  原廠那份,「還原成內建」要靠它
        #   vocab_budget   提示詞的長度上限與算法,讓面板可以「邊打邊算」。
        #                  這個上限是 Whisper 的硬限制,超過的詞模型直接看不到,
        #                  而且沒有任何徵兆——所以要在使用者打字的當下就講。
        "vocab_presets": vocab,
        "builtin_vocab": builtin_vocab,
        "vocab_budget": _vocab_budget(),
        "collapsed_groups": COLLAPSED_GROUPS,
        "values": values,
        # 設定組合:presets=全部、my_presets=你自己存的(只有這些可以刪)、
        # preset_keys=套用組合時「只會」動到的欄位
        "presets": presets,
        "my_presets": mine,
        "preset_keys": list(getattr(cfg, "PRESET_KEYS", [])),
        # 面板存檔時用來判斷「這個值跟預設一樣嗎」——一樣的就不寫進
        # settings_local.json,讓你以後吃得到程式改良過的預設值
        "defaults": {f["key"]: getattr(cfg, "DEFAULTS", {}).get(f["key"])
                     for f in FIELDS},
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        print("用法:python ui_settings.py dump", file=sys.stderr)
        sys.exit(1)
