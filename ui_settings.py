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
             vstlist(VST 檔路徑清單) / combo(可打字下拉,配 options)
  tier     common=基本頁 / advanced=進階頁
  group    同一頁裡的分組標題(進階頁的分組預設折疊,見 COLLAPSED_GROUPS)
  hint     欄位下方的白話說明
  options  select/combo 的選項
  min/max/step/default  number 用
  soft     True=這個數字沒有真正的上下限,滑條維持 min/max,但輸入框可超出
  show_if  只有在別的欄位等於指定值時才顯示,例如
           {"SILENCE_ACTION": ["speed"]} = 只有停頓處理選 speed 時才顯示
"""
from __future__ import annotations
import sys, json

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config.settings as cfg

# 進階頁裡「預設折疊」的分組(基本頁的分組一律展開)
# 註:降噪外掛(VST)刻意不折疊,讓使用者一眼看到降噪設定
COLLAPSED_GROUPS = [
    "冗詞與口頭禪", "字幕", "審閱標記", "音樂/音效保護", "其他微調",
]

FIELDS = [
    # ================= 基本頁 =================
    # --- 分組:辨識 ---
    {"key": "VOCAB_CATEGORIES", "label": "教學類型", "type": "category",
     "tier": "common", "group": "辨識",
     "hint": "選你影片的領域(可複選),會自動載入該領域術語,讓辨識更準"},
    {"key": "ASR_ENGINE", "label": "辨識引擎", "type": "select",
     "tier": "common", "group": "辨識", "options": ["faster-whisper", "funasr"],
     "hint": "faster-whisper 通用、中英夾雜較好;funasr 為純中文內容備選"},
    {"key": "WHISPER_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["large-v3", "medium", "small", "base"],
     "show_if": {"ASR_ENGINE": ["faster-whisper"]},
     "hint": "large-v3 最準但較慢也較吃顯卡;顯卡不夠力可改 medium"},
    {"key": "FUNASR_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["paraformer-zh"],
     "show_if": {"ASR_ENGINE": ["funasr"]},
     "hint": "FunASR 使用的模型。目前支援 paraformer-zh(純中文較準)"},
    {"key": "WHISPER_LANGUAGE", "label": "影片主要語言", "type": "select",
     "tier": "common", "group": "辨識",
     "options": ["auto", "zh", "en", "ja", "ko", "yue", "de", "fr", "es"],
     "hint": "auto=自動偵測、zh=中文、en=英文、ja=日文、ko=韓文、yue=粵語"},

    # --- 分組:剪輯 ---
    {"key": "AUDIO_MODE", "label": "聲音處理方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["vst", "none", "opensource"],
     "hint": "vst=用你的降噪外掛處理聲音;none=完全不處理(最快,適合先試流程)"},
    {"key": "DELIVERY_MODE", "label": "交付方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["live", "baked"],
     "hint": "live=活專案(建議):全部保留、只上顏色標籤,進 Premiere 再決定刪或快轉,隨時可反悔;baked=直接剪好,不能反悔"},
    {"key": "SILENCE_ACTION", "label": "停頓怎麼處理", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["speed", "delete"],
     "hint": "speed=把停頓快轉過去;delete=直接剪掉停頓"},
    {"key": "SILENCE_SPEED_FACTOR", "label": "快轉倍率", "type": "number",
     "tier": "common", "group": "剪輯", "min": 1, "max": 20, "step": 0.5,
     "default": 6.0, "soft": True, "show_if": {"SILENCE_ACTION": ["speed"]},
     "hint": "停頓段用幾倍速快轉(例如 6 = 六倍速)"},
    {"key": "MUTE_SPEED_AUDIO", "label": "快轉段消音", "type": "bool",
     "tier": "common", "group": "剪輯", "show_if": {"SILENCE_ACTION": ["speed"]},
     "hint": "打勾:快轉那幾段沒有聲音,避免加速產生的尖聲(建議打勾)"},
    {"key": "SILENCE_THRESHOLD_SEC", "label": "停頓多久才算靜音", "type": "number",
     "tier": "common", "group": "剪輯", "min": 0.1, "max": 5, "step": 0.1,
     "default": 1.2, "soft": True,
     "hint": "兩句話中間停超過這個秒數,才會被當成要處理的停頓。講話慢的人調高一點"},
    {"key": "MUSIC_DETECT", "label": "保護音樂/音效段", "type": "bool",
     "tier": "common", "group": "剪輯",
     "hint": "打勾:沒講話但有聲音的段落(預覽音樂、示範音效)會保留,不被當停頓剪掉或快轉"},

    # ================= 進階頁 =================
    # --- 分組:降噪外掛(VST)---
    {"key": "VST_CHAIN", "label": "VST 外掛路徑", "type": "vstlist",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": "降噪/EQ 等 .vst3 外掛的完整路徑,一行一個,會依序套用"},
    {"key": "VOICEFX_MODE", "label": "降噪:消除什麼", "type": "select",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "options": ["消噪音", "消回音", "兩者都消"],
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": "VoiceFX(NVIDIA 降噪)要消除的對象。房間有回音就選『兩者都消』"},
    {"key": "VOICEFX_INTENSITY", "label": "降噪:強度", "type": "number",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "min": 0, "max": 100, "step": 1, "default": 100.0,
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": "0~100。越大清得越乾淨,但太大可能讓人聲變悶失真;不確定就留 100"},

    # --- 分組:冗詞與口頭禪 ---
    {"key": "FILLERS_ALWAYS", "label": "一定要刪的語氣詞", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "幾乎不可能有意義、看到就刪的語氣詞(嗯、呃、啊…)。用逗號分隔"},
    {"key": "FILLERS_CONDITIONAL", "label": "口頭禪清單", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "你的個人口頭禪。只有在句首單獨出現、或連續重複時才會被刪,其餘保留。用逗號分隔"},
    {"key": "CONDITIONAL_CONFIDENCE", "label": "口頭禪判定的把握度", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0, "max": 1, "step": 0.05,
     "default": 0.6,
     "hint": "0~1。越低代表程式越沒把握、越常標記出來請你確認"},
    {"key": "CUSTOM_VOCAB", "label": "我的額外術語", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "教學類型以外,你還常講的專有名詞:頻道名、人名、慣用詞。用逗號分隔"},

    # --- 分組:字幕 ---
    {"key": "SUBTITLE_MAX_CHARS", "label": "字幕每行最多幾字", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 8, "max": 40, "step": 1,
     "default": 18, "soft": True,
     "hint": "一行字幕超過這個字數就換行"},
    {"key": "SUBTITLE_MAX_GAP_SEC", "label": "字幕換行的停頓門檻", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 0.1, "max": 2, "step": 0.1,
     "default": 0.5, "soft": True,
     "hint": "兩個字之間停超過這個秒數,字幕就換一行"},
    {"key": "CONVERT_TO_TRADITIONAL", "label": "簡體轉繁體", "type": "bool",
     "tier": "advanced", "group": "字幕",
     "hint": "把辨識出來的簡體字自動轉成繁體字"},

    # --- 分組:審閱標記 ---
    {"key": "MARKER_MIN_DURATION_MS", "label": "太短的切點不標記", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 2000, "step": 50,
     "default": 200, "soft": True,
     "hint": "毫秒。刪除長度短於這個值的切點不下 marker(太短不值得逐一看)"},
    {"key": "MARKER_MAX_CONFIDENCE", "label": "要標記審閱的把握度門檻", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 1, "step": 0.05,
     "default": 0.9,
     "hint": "0~1。只有把握度低於這個值的切點才下 marker;高把握度的必刪詞不用你看"},

    # --- 分組:音樂/音效保護 ---
    {"key": "MUSIC_DB_ABOVE_FLOOR", "label": "音樂偵測靈敏度", "type": "number",
     "tier": "advanced", "group": "音樂/音效保護", "min": 3, "max": 30, "step": 1,
     "default": 12.0, "soft": True,
     "hint": "比環境底噪高幾分貝才算音樂。音樂被誤快轉→調小;呼吸聲被誤留→調大"},
    {"key": "MUSIC_MIN_SEC", "label": "多短的聲響不算音樂", "type": "number",
     "tier": "advanced", "group": "音樂/音效保護", "min": 0.1, "max": 3, "step": 0.1,
     "default": 0.4, "soft": True,
     "hint": "短於這個秒數的聲音(咳嗽、滑鼠聲)不會被當成音樂保護"},

    # --- 分組:其他微調 ---
    {"key": "SILENCE_PADDING_SEC", "label": "停頓前後的保護緩衝", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 0, "max": 1, "step": 0.05,
     "default": 0.15, "soft": True,
     "hint": "停頓段前後各多留這麼多秒,避免切太緊把呼吸聲/氣音也切掉"},
    {"key": "TARGET_LUFS", "label": "目標響度", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": -30, "max": -6, "step": 0.5,
     "default": -14.0, "soft": True,
     "hint": "整體音量標準(LUFS)。YouTube 建議 -14,數字越小越安靜"},
]


def dump() -> None:
    values = {f["key"]: getattr(cfg, f["key"], None) for f in FIELDS}
    out = {
        "fields": FIELDS,
        "categories_available": list(getattr(cfg, "VOCAB_PRESETS", {}).keys()),
        "collapsed_groups": COLLAPSED_GROUPS,
        "values": values,
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        print("用法:python ui_settings.py dump", file=sys.stderr)
        sys.exit(1)
