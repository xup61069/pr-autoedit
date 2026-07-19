"""
面板 UI ↔ 設定 的橋樑。

  python ui_settings.py dump   印出 JSON:UI 欄位定義 + 目前的值 + 教學類型清單

面板讀這份 JSON 自動產生表單;使用者調整後,面板把改動寫進
config/settings_local.json(見 config/settings.py 尾端的 JSON 覆寫),
下次跑 pipeline 就生效。

欄位新增只要改這裡的 FIELDS,面板 UI 會自動長出對應控制項。
"""
from __future__ import annotations
import sys, json

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config.settings as cfg

# 每個欄位:key=設定名, label=白話, type=控制項型別, tier=common/advanced
#   type: select(下拉,配 options) / number(數字,配 min/max/step) /
#         bool(勾選) / list(逗號分隔清單) / category(教學類型複選) /
#         vstlist(VST 檔路徑清單)
FIELDS = [
    # --- 常用 ---
    {"key": "AUDIO_MODE", "label": "聲音處理方式", "type": "select",
     "tier": "common", "options": ["vst", "none", "opensource"],
     "hint": "vst=用你的降噪外掛處理聲音;none=完全不處理(最快,適合先試流程)"},
    {"key": "VOCAB_CATEGORIES", "label": "教學類型", "type": "category",
     "tier": "common",
     "hint": "選你影片的領域(可複選),會自動載入該領域術語,讓辨識更準"},
    {"key": "SILENCE_ACTION", "label": "停頓怎麼處理", "type": "select",
     "tier": "common", "options": ["speed", "delete"],
     "hint": "speed=把停頓快轉過去;delete=直接剪掉停頓"},
    {"key": "SILENCE_SPEED_FACTOR", "label": "快轉倍率", "type": "number",
     "tier": "common", "min": 1, "max": 20, "step": 0.5, "default": 6.0,
     "hint": "停頓段用幾倍速快轉(例如 6 = 六倍速)。停頓處理選 speed 時才有用"},
    {"key": "MUTE_SPEED_AUDIO", "label": "快轉段消音", "type": "bool",
     "tier": "common",
     "hint": "打勾:快轉那幾段沒有聲音,避免加速產生的尖聲(建議打勾)"},
    {"key": "SILENCE_THRESHOLD_SEC", "label": "停頓多久才算靜音", "type": "number",
     "tier": "common", "min": 0.3, "max": 5, "step": 0.1, "default": 1.2,
     "hint": "兩句話中間停超過這個秒數,才會被當成要處理的停頓。講話慢的人調高一點"},
    {"key": "FILLERS_CONDITIONAL", "label": "口頭禪清單", "type": "list",
     "tier": "common",
     "hint": "你的個人口頭禪。只有在句首單獨出現、或連續重複時才會被刪,其餘保留。用逗號分隔"},
    {"key": "WHISPER_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "options": ["large-v3", "medium", "small", "base"],
     "hint": "large-v3 最準但較慢也較吃顯卡;顯卡不夠力可改 medium"},

    # --- 進階 ---
    {"key": "CUSTOM_VOCAB", "label": "我的額外術語", "type": "list",
     "tier": "advanced",
     "hint": "教學類型以外,你還常講的專有名詞:頻道名、人名、慣用詞。用逗號分隔"},
    {"key": "FILLERS_ALWAYS", "label": "一定要刪的語氣詞", "type": "list",
     "tier": "advanced",
     "hint": "幾乎不可能有意義、看到就刪的語氣詞(嗯、呃、啊…)。用逗號分隔"},
    {"key": "CONDITIONAL_CONFIDENCE", "label": "口頭禪判定的把握度", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05, "default": 0.6,
     "hint": "0~1。越低代表程式越沒把握、越常標記出來請你確認"},
    {"key": "SILENCE_PADDING_SEC", "label": "停頓前後的保護緩衝", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05, "default": 0.15,
     "hint": "停頓段前後各多留這麼多秒,避免切太緊把呼吸聲/氣音也切掉"},
    {"key": "SUBTITLE_MAX_CHARS", "label": "字幕每行最多幾字", "type": "number",
     "tier": "advanced", "min": 8, "max": 40, "step": 1, "default": 18,
     "hint": "一行字幕超過這個字數就換行"},
    {"key": "SUBTITLE_MAX_GAP_SEC", "label": "字幕換行的停頓門檻", "type": "number",
     "tier": "advanced", "min": 0.1, "max": 2, "step": 0.1, "default": 0.5,
     "hint": "兩個字之間停超過這個秒數,字幕就換一行"},
    {"key": "MARKER_MIN_DURATION_MS", "label": "太短的切點不標記", "type": "number",
     "tier": "advanced", "min": 0, "max": 2000, "step": 50, "default": 200,
     "hint": "毫秒。刪除長度短於這個值的切點不下 marker(太短不值得逐一看)"},
    {"key": "MARKER_MAX_CONFIDENCE", "label": "要標記審閱的把握度門檻", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05, "default": 0.9,
     "hint": "0~1。只有把握度低於這個值的切點才下 marker;高把握度的必刪詞不用你看"},
    {"key": "TARGET_LUFS", "label": "目標響度", "type": "number",
     "tier": "advanced", "min": -30, "max": -6, "step": 0.5, "default": -14.0,
     "hint": "整體音量標準(LUFS)。YouTube 建議 -14,數字越小越安靜"},
    {"key": "ASR_ENGINE", "label": "辨識引擎", "type": "select",
     "tier": "advanced", "options": ["faster-whisper", "funasr"],
     "hint": "faster-whisper 通用、中英夾雜較好;funasr 為純中文內容備選"},
    {"key": "CONVERT_TO_TRADITIONAL", "label": "簡體轉繁體", "type": "bool",
     "tier": "advanced", "hint": "把辨識出來的簡體字自動轉成繁體字"},
    {"key": "WHISPER_LANGUAGE", "label": "影片主要語言", "type": "combo",
     "tier": "advanced", "options": ["auto", "zh", "en", "ja", "ko"],
     "hint": "可打字也可從清單選。auto=自動偵測、zh=中文、en=英文、ja=日文、ko=韓文"},
    {"key": "VST_CHAIN", "label": "VST 外掛路徑", "type": "vstlist",
     "tier": "advanced",
     "hint": "降噪/EQ 等 .vst3 外掛的完整路徑,一行一個,會依序套用"},
]


def dump() -> None:
    values = {f["key"]: getattr(cfg, f["key"], None) for f in FIELDS}
    out = {
        "fields": FIELDS,
        "categories_available": list(getattr(cfg, "VOCAB_PRESETS", {}).keys()),
        "values": values,
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        print("用法:python ui_settings.py dump", file=sys.stderr)
        sys.exit(1)
