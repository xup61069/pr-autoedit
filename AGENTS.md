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
 → audio_clean.py   清理音訊,輸出乾淨 WAV + 混回影片的 mp4
 → transcribe.py    Whisper 詞級轉錄(唯一真相來源,只轉一次,有快取)
 → decision.py      判定每一段 keep / delete / speed,輸出 Segment 清單
 → remap.py         建立時間戳映射表,字幕和 marker 都從這裡衍生
 → premiere_xml.py / subtitles.py / report.py   產生審閱檔案
```

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
   之後調門檻重跑只重算決策,不重轉。任何改動都不要破壞這個快取機制。

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

## 常見的修改請求與對應位置

| 使用者想要 | 改哪裡 |
|-----------|--------|
| 冗詞判太多/太少 | `config/settings.py` 的 FILLERS_*,或 `decision.py` 的 `_is_isolated_or_repeated` |
| 靜音切太碎/太鬆 | `config/settings.py` 的 SILENCE_THRESHOLD_SEC |
| 字幕斷句不好 | `remap.py` 的 `build_subtitles`,或 config 的 SUBTITLE_* |
| 加新的音訊處理路線 | `modules/audio_clean.py`,仿照現有兩個 clean_* 函數 |
| marker 太多/太少 | `config/settings.py` 的 MARKER_MIN_DURATION_MS / MARKER_MAX_CONFIDENCE |
| 全自動輸出(不進 PR) | 需新增 modules/render.py,用 ffmpeg 依 timeline.json 切段串接 |

## 尚未實作(未來可能的請求)

- 全自動渲染模式(4a):目前只做審閱模式(4b)。要做的話新增 render.py。
- 監看資料夾:錄完丟進資料夾自動觸發,可用 watchdog 套件。
- Gradio 網頁介面:給非工程協作者用。
