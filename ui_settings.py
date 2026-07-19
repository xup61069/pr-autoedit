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
     "hint": "vst=用你的降噪外掛;none=不處理"},
    {"key": "VOCAB_CATEGORIES", "label": "教學類型(改善辨識)", "type": "category",
     "tier": "common", "hint": "選你影片的領域,自動載入該領域術語"},
    {"key": "CUSTOM_VOCAB", "label": "我的額外術語", "type": "list",
     "tier": "common", "hint": "頻道名、人名、慣用詞,用逗號分隔"},
    {"key": "SILENCE_ACTION", "label": "停頓處理", "type": "select",
     "tier": "common", "options": ["speed", "delete"],
     "hint": "speed=快轉;delete=直接剪掉"},
    {"key": "SILENCE_SPEED_FACTOR", "label": "快轉倍率", "type": "number",
     "tier": "common", "min": 1, "max": 20, "step": 0.5},
    {"key": "MUTE_SPEED_AUDIO", "label": "快轉段靜音(避免尖聲)", "type": "bool",
     "tier": "common"},
    {"key": "SILENCE_THRESHOLD_SEC", "label": "靜音門檻(秒)", "type": "number",
     "tier": "common", "min": 0.3, "max": 5, "step": 0.1},
    {"key": "FILLERS_CONDITIONAL", "label": "口頭禪清單", "type": "list",
     "tier": "common", "hint": "孤立/重複時才刪的詞"},
    {"key": "WHISPER_MODEL", "label": "辨識模型(準確度/速度)", "type": "select",
     "tier": "common", "options": ["large-v3", "medium", "small", "base"]},

    # --- 進階 ---
    {"key": "FILLERS_ALWAYS", "label": "必刪語氣詞", "type": "list", "tier": "advanced"},
    {"key": "CONDITIONAL_CONFIDENCE", "label": "口頭禪信心", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05},
    {"key": "SILENCE_PADDING_SEC", "label": "靜音保護緩衝(秒)", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05},
    {"key": "SUBTITLE_MAX_CHARS", "label": "字幕每行字數", "type": "number",
     "tier": "advanced", "min": 8, "max": 40, "step": 1},
    {"key": "SUBTITLE_MAX_GAP_SEC", "label": "字幕換行間隔(秒)", "type": "number",
     "tier": "advanced", "min": 0.1, "max": 2, "step": 0.1},
    {"key": "MARKER_MIN_DURATION_MS", "label": "marker 最短長度(ms)", "type": "number",
     "tier": "advanced", "min": 0, "max": 2000, "step": 50},
    {"key": "MARKER_MAX_CONFIDENCE", "label": "marker 信心門檻", "type": "number",
     "tier": "advanced", "min": 0, "max": 1, "step": 0.05},
    {"key": "TARGET_LUFS", "label": "目標響度(LUFS)", "type": "number",
     "tier": "advanced", "min": -30, "max": -6, "step": 0.5},
    {"key": "ASR_ENGINE", "label": "辨識引擎", "type": "select",
     "tier": "advanced", "options": ["faster-whisper", "funasr"]},
    {"key": "CONVERT_TO_TRADITIONAL", "label": "簡轉繁", "type": "bool", "tier": "advanced"},
    {"key": "WHISPER_LANGUAGE", "label": "辨識語言", "type": "select",
     "tier": "advanced", "options": ["zh", "en", "ja", "ko"]},
    {"key": "VST_CHAIN", "label": "VST 外掛(.vst3 路徑)", "type": "vstlist",
     "tier": "advanced"},
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
