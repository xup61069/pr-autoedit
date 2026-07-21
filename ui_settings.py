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
]

FIELDS = [
    # ================= 基本頁 =================
    # --- 分組:辨識 ---
    {"key": "VOCAB_CATEGORIES", "label": "教學類型", "type": "category",
     "tier": "common", "group": "辨識",
     "hint": "載入該領域術語,提升專有名詞辨識率。可複選,但提示詞有長度上限,選太多會被截斷(超出時會提示)"},
    {"key": "ASR_ENGINE", "label": "辨識引擎", "type": "select",
     "tier": "common", "group": "辨識", "options": ["faster-whisper", "funasr"],
     "hint": "faster-whisper:通用,中英夾雜較佳。funasr:純中文備選"},
    {"key": "WHISPER_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["large-v3", "medium", "small", "base"],
     "show_if": {"ASR_ENGINE": ["faster-whisper"]},
     "hint": "large-v3 最準但吃顯卡;顯卡吃緊改 medium"},
    {"key": "FUNASR_MODEL", "label": "辨識模型", "type": "select",
     "tier": "common", "group": "辨識", "options": ["paraformer-zh"],
     "show_if": {"ASR_ENGINE": ["funasr"]},
     "hint": "目前僅支援 paraformer-zh"},
    {"key": "WHISPER_LANGUAGE", "label": "影片主要語言", "type": "select",
     "tier": "common", "group": "辨識",
     "options": ["auto", "zh", "en", "ja", "ko", "yue", "de", "fr", "es"],
     "hint": "auto 為自動偵測"},

    # --- 分組:剪輯 ---
    {"key": "AUDIO_MODE", "label": "聲音處理方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["vst", "none", "opensource"],
     "hint": "vst:走降噪外掛。none:不處理,測流程最快"},
    {"key": "DELIVERY_MODE", "label": "交付方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["baked", "live"],
     "hint": "baked:直接剪好(建議),改設定後按「重算剪輯」幾秒出新序列。"
             "live:全部保留、只上顏色標籤,自行批次處理(片段多,長片會卡)"},
    {"key": "SILENCE_ACTION", "label": "停頓處理方式", "type": "select",
     "tier": "common", "group": "剪輯", "options": ["auto", "speed", "delete"],
     "hint": "auto 看畫面決定(建議):畫面在動就快轉帶過、靜止才剪掉,"
             "默默示範操作的那幾秒不會消失。"
             "speed 一律快轉、什麼都不刪。delete 一律剪掉、最兇"},
    {"key": "SILENCE_SPEED_FACTOR", "label": "快轉倍率", "type": "number",
     "tier": "common", "group": "剪輯", "min": 1, "max": 20, "step": 0.5,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto", "speed"]},
     "hint": "停頓段的加速倍率"},
    {"key": "MUTE_SPEED_AUDIO", "label": "快轉段消音", "type": "bool",
     "tier": "common", "group": "剪輯",
     "show_if": {"SILENCE_ACTION": ["auto", "speed"]},
     "hint": "避免加速造成的變調尖聲。建議開啟"},
    {"key": "SILENCE_THRESHOLD_SEC", "label": "靜音判定門檻", "type": "number",
     "tier": "common", "group": "剪輯", "min": 0.1, "max": 5, "step": 0.1,
     "soft": True,
     "hint": "超過幾秒才算停頓。講話慢調高"},
    {"key": "MUSIC_DETECT", "label": "音樂/音效保護", "type": "bool",
     "tier": "common", "group": "剪輯",
     "hint": "保護無語音但有聲音的段落(示範音樂、音效),避免被當停頓剪掉"},
    # 註:「無語音時依畫面判定」以前是這裡的一個獨立勾選,現在併進上面的
    # 「停頓處理方式 = auto」。兩個設定並存時會互相蓋掉(選了快轉,畫面靜止的
    # 停頓還是被剪掉),而且報告看不出來,所以做成三選一。
    {"key": "MICRO_TRIM", "label": "能量微剪", "type": "bool",
     "tier": "common", "group": "剪輯",
     "hint": "剪更兇的主力,建議開啟。連句中無聲的小空檔也剪掉,"
             "17 分鐘的片約再省 2~3 分鐘,不損失任何內容"},
    {"key": "MICRO_TRIM_KEEP_SEC", "label": "微剪:保留緩衝", "type": "number",
     "tier": "common", "group": "剪輯", "min": 0, "max": 0.3, "step": 0.01,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "每個停頓頭尾各保留的秒數。剪完太急促就調大,還是拖就調小"},

    # --- 分組:剪完自動做 ---
    {"key": "AUTO_OPEN_REPORT", "label": "完成後開啟報告", "type": "bool",
     "tier": "common", "group": "剪完自動做",
     "hint": "剪完自動用瀏覽器開啟報告"},

    # ================= 進階頁 =================
    # --- 分組:降噪外掛(VST)---
    {"key": "VST_BAKE", "label": "降噪烘進音檔", "type": "bool",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": "建議關閉。關閉:聲音保持原樣,降噪在 Premiere 掛,隨時可調可關。"
             "開啟:先處理進音檔,之後改不了"},
    {"key": "VST_CHAIN", "label": "VST 外掛路徑", "type": "vstlist",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "show_if": {"AUDIO_MODE": ["vst"]},
     "hint": ".vst3 完整路徑,依序套用"},
    {"key": "VOICEFX_MODE", "label": "降噪:消除對象", "type": "select",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "options": ["消噪音", "消回音", "兩者都消"],
     "show_if": {"AUDIO_MODE": ["vst"], "VST_BAKE": [True]},
     "hint": "房間有回音選「兩者都消」。僅在烘進音檔時有作用"},
    {"key": "VOICEFX_INTENSITY", "label": "降噪:強度", "type": "number",
     "tier": "advanced", "group": "降噪外掛(VST)",
     "min": 0, "max": 100, "step": 1,
     "show_if": {"AUDIO_MODE": ["vst"], "VST_BAKE": [True]},
     "hint": "越大清得越乾淨,過大會讓人聲變悶"},

    # --- 分組:冗詞與口頭禪 ---
    {"key": "FILLERS_ALWAYS", "label": "無條件刪除語氣詞", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "無條件刪除的語氣詞,逗號分隔"},
    {"key": "FILLERS_CONDITIONAL", "label": "口頭禪清單", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "僅在句首孤立或連續重複時刪除,其餘保留。逗號分隔"},
    {"key": "CONDITIONAL_CONFIDENCE", "label": "口頭禪判定把握度", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0, "max": 1, "step": 0.05,
     "hint": "越低代表越沒把握,標記出來請你確認的也越多"},
    {"key": "RETAKE_DETECT", "label": "重講偵測", "type": "bool",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "⚠ 建議關閉。實測效益很小,且容易把排比句(左側是…右側是…)"
             "誤判成重講砍掉。開啟時每一處都會下 marker,務必看報告確認"},
    {"key": "RETAKE_SIMILARITY", "label": "重講相似度門檻", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0.6, "max": 1.0, "step": 0.05,
     "show_if": {"RETAKE_DETECT": [True]},
     "hint": "調高(0.9)只抓一字不差的重講,最保守;調低(0.7)抓得多但易誤砍排比句"},
    {"key": "FILLER_PAUSE_SEC", "label": "語氣詞刪除的停頓要求", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0, "max": 0.5, "step": 0.05,
     "soft": True,
     "hint": "0 = 看到就刪(建議)。只有 funasr 需要設 0.1,"
             "它會把「好啊」拆成兩字,不設限會誤刪句尾的「啊」"},
    {"key": "FILLER_ISOLATED_GAP_SEC", "label": "口頭禪孤立判定門檻", "type": "number",
     "tier": "advanced", "group": "冗詞與口頭禪", "min": 0.05, "max": 1.0, "step": 0.05,
     "soft": True,
     "hint": "前方停頓超過此秒數,即把「然後、就是」視為句首語助詞刪除。"
             "調小刪更多,誤刪會下 marker"},
    {"key": "MICRO_TRIM_MIN_SEC", "label": "微剪:最短長度", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 0.1, "max": 1.0, "step": 0.05,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "至少能剪掉這麼久才動手。太小會把說話節奏剁碎"},
    {"key": "MICRO_TRIM_DB_BELOW_SPEECH", "label": "微剪:靜音門檻", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 10, "max": 40, "step": 1,
     "soft": True, "show_if": {"MICRO_TRIM": [True]},
     "hint": "低於說話音量幾分貝算無聲。調大連氣音也剪(更兇),調小較保守"},
    {"key": "CUSTOM_VOCAB", "label": "自訂術語", "type": "list",
     "tier": "advanced", "group": "冗詞與口頭禪",
     "hint": "教學類型以外的專有名詞:頻道名、人名、慣用詞。逗號分隔。此欄優先權最高,不會被截斷"},

    # --- 分組:字幕 ---
    {"key": "SUBTITLE_MAX_CHARS", "label": "字幕行長上限", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 8, "max": 40, "step": 1,
     "soft": True,
     "hint": "超過字數即換行"},
    {"key": "SUBTITLE_MAX_GAP_SEC", "label": "字幕斷行停頓門檻", "type": "number",
     "tier": "advanced", "group": "字幕", "min": 0.1, "max": 2, "step": 0.1,
     "soft": True,
     "hint": "停頓超過此秒數即換行"},
    {"key": "CONVERT_TO_TRADITIONAL", "label": "簡體轉繁體", "type": "bool",
     "tier": "advanced", "group": "字幕",
     "hint": "辨識結果簡轉繁"},

    # --- 分組:審閱標記 ---
    {"key": "MARKER_MIN_DURATION_MS", "label": "marker 最短長度", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 2000, "step": 50,
     "soft": True,
     "hint": "刪除長度短於此值的切點不下 marker。單位毫秒"},
    {"key": "MARKER_MAX_CONFIDENCE", "label": "marker 把握度門檻", "type": "number",
     "tier": "advanced", "group": "審閱標記", "min": 0, "max": 1, "step": 0.05,
     "hint": "只有把握度低於此值的切點才下 marker"},

    # --- 分組:畫面活動(只有停頓處理方式選 auto 時才有作用)---
    {"key": "MOTION_SENSITIVITY", "label": "畫面活動靈敏度", "type": "number",
     "tier": "advanced", "group": "畫面活動", "min": 0.1, "max": 5, "step": 0.1,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto"]},
     "hint": "越小越敏感。示範被剪掉了調小;滑鼠晃一下就不剪調大"},
    {"key": "MOTION_MIN_SEC", "label": "畫面判定最短長度", "type": "number",
     "tier": "advanced", "group": "畫面活動", "min": 0.2, "max": 3, "step": 0.1,
     "soft": True, "show_if": {"SILENCE_ACTION": ["auto"]},
     "hint": "短於此秒數的停頓不看畫面,一律快轉帶過"},
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
     "hint": "短於此秒數的聲響(咳嗽、滑鼠)不視為音樂"},

    # --- 分組:其他微調 ---
    {"key": "SILENCE_PADDING_SEC", "label": "停頓邊界緩衝", "type": "number",
     "tier": "advanced", "group": "其他微調", "min": 0, "max": 1, "step": 0.05,
     "soft": True,
     "hint": "停頓前後各保留的緩衝秒數,避免切掉氣音"},
    {"key": "VIDEO_ENCODER", "label": "重新編碼格式", "type": "select",
     "tier": "advanced", "group": "其他微調",
     "options": ["auto", "av1_nvenc", "hevc_nvenc", "h264_nvenc",
                 "libx265", "libx264"],
     "hint": "多數影片不會重新編碼(畫面是無損複製),僅少數複製後會壞才需重編。"
             "auto 自動挑最省空間又可用的(建議);指定的不可用時會自動換,不會失敗"},
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


def dump() -> None:
    values = {f["key"]: getattr(cfg, f["key"], None) for f in FIELDS}
    for _k in PANEL_EXTRA_KEYS:
        values[_k] = getattr(cfg, _k, None)
    presets, mine = _all_presets()
    out = {
        "fields": fields_with_defaults(),
        "categories_available": list(getattr(cfg, "VOCAB_PRESETS", {}).keys()),
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
