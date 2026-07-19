# 給 AI 助手的專案交接文件

如果你是被找來修改這個專案的 AI 助手,先讀完這份再動手。這份文件的目的是讓你
在沒有前後文的情況下,幾分鐘內理解整個專案並安全地改動。

---

## 這個專案在做什麼

一個 Premiere Pro 教學影片的半自動剪輯工具。輸入一支錄好的影片,自動:
1. 清理音訊(降噪 + 響度標準化)
2. 語音轉錄(faster-whisper,詞級時間戳)
3. 判定並移除冗詞(嗯、呃、就是、然後…)
4. 把長靜音段落快轉或剪掉
5. 產生繁體字幕
6. 輸出一個「已剪好」的 Premiere 專案(XML)+ 審閱 marker + HTML 報告

使用者不會讓程式直接輸出成品,而是拿到剪好的 PR 專案,自己跳著確認每個切點再輸出。
這叫「審閱模式」,是整個專案的設計核心。

## 使用者是誰

會跑 Python 腳本、但不常寫程式的個人創作者。作業系統是 Windows + NVIDIA GPU。
改動時,任何面向使用者的說明都要寫得像 SETUP.md 那樣手把手,不要假設對方懂術語。

---

## 架構與資料流

```
影片
 → audio_clean.clean_audio   清理音訊(響度標準化;VST_BAKE=True 才把降噪烘進去)
 → transcribe.py             詞級轉錄(唯一真相來源,只轉一次,有快取;引擎可換)
 → audio_probe.py            能量偵測「沒講話但有聲音」的區間(音樂/音效,用原始音訊)
 → decision.py               判定每一段 keep / delete / speed / 音樂保護,輸出 Segment 清單
 → audio_clean.gate+mux_back 把快轉段音訊抹靜音(僅 baked 模式),混回影片成 mp4
 → remap.py                  建立時間戳映射表,字幕和 marker 都從這裡衍生
 → premiere_xml.py / subtitles.py / report.py   產生交付檔案
```

**兩種交付方式(config.DELIVERY_MODE)**:
- `baked`(預設,直接剪好):auto-editor 產 XML,決策直接烘進去(cut/timeremap)。
  「隨時可調」靠面板的「重算剪輯」鈕:pipeline --skip-audio 幾秒重算、匯入新序列。
- `live`(備選,活專案):`premiere_xml.export_live_xml` 自製 FCP7 XML,所有段落
  切開但全保留(start==in、end==out),用標籤色分類(靜音=Rose、音樂=Caribbean、
  冗詞=Violet)。決策只是「建議」,使用者在 Premiere 裡批次處理、隨時反悔。
  剪完後可用 `modules/live_subs.py` 依序列實際版面重新對位字幕(P5)。
  ⚠️ 實測:大量小片段+標籤在 Premiere 是效能地雷(長片會卡),所以不是預設;
  設計新功能時要控制 clip 數量。

⚠️ **混音刻意排在決策之後**:baked 模式要先知道哪些是「靜音快轉段」,才能在混回
影片前把那幾段的聲音抹成無聲(config.MUTE_SPEED_AUDIO),避免 Premiere 快轉播放
時的尖聲。live 模式不抹(快轉還沒發生)。不要把混音移回清理階段。

轉錄引擎可切換(config.ASR_ENGINE:faster-whisper / funasr),各引擎都回傳一樣的
`list[Word]`,要加新引擎只在 transcribe.py 補一個函式,其餘管線不用動。

模組間全部用 `core/models.py` 定義的 dataclass 溝通,不要傳裸 dict。

## 三個必須守住的設計決定

這些是踩過坑才定下來的,改動時不要違反,否則會產生「靜默飄移」的難查 bug:

1. **時間單位一律用「幀」(frame),不用秒。**
   Premiere 的 FCP7 XML 以幀為時基,用秒換算會累積捨入誤差,30 分鐘的片子字幕會
   飄掉好幾格。只有 Whisper 原生輸出是秒,進系統邊界(models.py 的 Word)後立刻
   用 `start_frame(fps)` 轉幀,之後全程用幀。

2. **字幕和 Premiere marker 共用同一份 RemapTable。**
   兩者絕對不能各自實作時間映射。這是審閱模式最容易錯的地方,共用單一實作把風險
   收斂到一處。要改映射邏輯,改 `remap.py` 一個地方,兩邊自動同步。

3. **只轉錄一次。**
   Whisper 轉錄是最慢的一步(GPU 佔用大)。轉錄結果存成 `02_transcript.json` 快取,
   之後調門檻重跑只重算決策,不重轉。快取帶有「辨識設定指紋」(引擎/模型/語言/
   詞庫),設定變了會自動重轉——不要退回「只認檔案存在」的舊行為(曾造成
   「切了引擎但字幕沒變」的 bug)。任何改動都不要破壞這個快取機制。

## 信心分級機制

決策引擎給每個刪除段一個 confidence(0~1):
- 必刪冗詞(嗯、呃)→ confidence 1.0,不下 marker(不值得人工看)
- 模糊冗詞(就是、然後)→ confidence 0.6,下 marker(要人工確認)

審閱報告和 marker 靠這個分級決定要不要提請使用者注意。改冗詞邏輯時記得維持這個約定。

---

## 改動流程(重要)

**動 core/ 底下任何檔案後,一定要重跑對應測試:**

```
python -m tests.test_remap      # 動了 remap.py 或 models.py
python -m tests.test_decision   # 動了 decision.py 或 config/settings.py
python -m tests.test_e2e_smoke  # 動了任何東西,跑這個確認主幹沒斷
```

這些測試不需要 GPU、ffmpeg 或影片就能跑,是快速回歸檢查。改完沒跑測試就交付,
等於沒改完。

**新增功能時**,若牽涉時間計算或段落處理,先在 tests/ 加一個手算得出答案的測試案例,
再寫實作。這個專案的 bug 幾乎都是時間映射錯位,測試是唯一防線。

## 什麼該進 config,什麼該進程式碼

任何「使用者可能想調」的數值(門檻、詞表、倍率、Whisper 模型)都放 `config/settings.py`,
不要寫死在邏輯裡。使用者調校時只會動這個檔案,不該碰 core/。

`config/settings.py` 尾端有個人覆寫機制:若存在 `config/settings_local.py`,其中的
全大寫設定會蓋過預設值(不進版控)。新增設定項時維持這個模式即可,不用特別處理。

## 常見的修改請求與對應位置

| 使用者想要 | 改哪裡 |
|-----------|--------|
| 辨識不準/術語錯 | `config/settings.py` 的 VOCAB_CATEGORIES / CUSTOM_VOCAB(當提示詞/熱詞);改完直接重跑即可,快取偵測到辨識設定變更會自動重轉 |
| 換辨識引擎 | `config/settings.py` 的 ASR_ENGINE;新引擎在 `transcribe.py` 補 `_transcribe_xxx()` |
| 冗詞判太多/太少 | `config/settings.py` 的 FILLERS_*,或 `decision.py` 的 `_is_isolated_or_repeated` |
| 靜音切太碎/太鬆 | `config/settings.py` 的 SILENCE_THRESHOLD_SEC |
| 快轉段有尖聲 | `config/settings.py` 的 MUTE_SPEED_AUDIO(抹靜音);邏輯在 `audio_clean.gate_speed_audio` |
| 字幕斷句不好 | `remap.py` 的 `build_subtitles`,或 config 的 SUBTITLE_* |
| 加新的音訊處理路線 | `modules/audio_clean.py`,仿照現有 clean_* 函數 |
| 處理後影片不能播 | 多半是特殊 HEVC 複製失敗;`audio_clean.mux_back` 已有「複製→驗證→GPU 重編碼」退路 |
| marker 太多/太少 | `config/settings.py` 的 MARKER_MIN_DURATION_MS / MARKER_MAX_CONFIDENCE |
| 在 Premiere 內一鍵跑 | `premiere-panel/`(CEP 面板,薄薄一層:啟動 pipeline + 匯入結果) |
| 全自動輸出(不進 PR) | 需新增 modules/render.py,用 ffmpeg 依 timeline.json 切段串接 |

## 已有雛形 / 尚未實作

- **Premiere CEP 面板**:已成熟,在 `premiere-panel/`。功能:選影片→跑 pipeline→
  匯入;設定表單(由 `ui_settings.py` dump 自動生成,加欄位面板自動長出);
  剪輯後工具(開報告 / 重算剪輯匯入新序列 / QE 掛降噪 / 依序列版面產字幕)。
  待做:自動抓目前選取素材路徑、進度條。面板刻意做薄,
  真正邏輯全在 Python,日後要換 UXP 影響面小。
- **FunASR 引擎**:已實作為可選(`transcribe.py`)。對中英夾雜內容不如 Whisper,
  故非預設;純中文內容可切換。
- 全自動渲染模式(4a):目前只做審閱模式(4b)。要做的話新增 render.py。
- 監看資料夾:錄完丟進資料夾自動觸發,可用 watchdog 套件。
- Gradio 網頁介面:給非工程協作者用。
