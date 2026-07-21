# PR 自動剪輯工具

錄完影片丟進去 → 自動響度標準化、去冗詞、靜音快轉/刪除、保護音樂段、產繁中字幕
→ 輸出一個「已經剪好」的 Premiere 專案。你只要跳著確認每個切點,通過就輸出。
覺得剪太兇或太保守?在 Premiere 面板改個設定按「重算剪輯」,幾秒出一個新序列。
目標是把單支教學片的剪輯時間從 2~3 小時壓到 5~10 分鐘人工。

授權:MIT。歡迎自由使用、修改、散布。

---

## 為什麼做這個工具

在 AI 內容氾濫的時代,我更希望看到「活人」繼續出來分享知識——有溫度、有觀點、
有真實踩過坑的經驗的人。而且我相信,這樣的內容,大家還是愛看。

但做教學影片最累的,往往不是「講」,而是「剪」:降雜訊、砍冗詞、剪停頓、上字幕……
一支片能耗掉兩三個小時。很多人因此懶得做,或做了幾支就累到放棄。

這個工具的想法很簡單:

> **讓 AI 去做那些無聊的剪輯苦工,把時間還給創作者,讓人專心把知識講好。**

AI 在這裡是助手,不是取代者——所以它**不直接輸出成品**,而是幫你剪好一個
「待你確認」的版本,最後拍板的永遠是你這個活人。如果這能讓多一個人願意持續分享,
也讓觀眾更愛看,那就值得。

---

## 它會幫你做什麼

| 功能 | 說明 |
|------|------|
| 🎙️ 音訊處理 | 響度標準化到 YouTube 標準;降噪預設「不烘進音檔」——交給 Premiere 掛 VST 效果,隨時可調可關(想烘死也有選項) |
| 📝 語音轉文字 | 詞級時間戳,是整個系統的唯一真相來源;引擎可切換(faster-whisper / FunASR);改了引擎、模型或詞庫會自動重新辨識,不用手動清快取 |
| ✂️ 去冗詞 | 「嗯、呃」必刪;「就是、然後」這類看語境判斷,低信心的留給你確認 |
| ⏩ 停頓處理 | 靜音自動快轉或刪除;快轉段可自動靜音,避免加速尖聲 |
| 🎵 音樂保護 | 沒講話但有聲音的段落(預覽音樂、示範音效)自動偵測、原樣保留,不會被當靜音剪掉 |
| 💬 繁中字幕 | 標點感知斷行、簡轉繁(OpenCC),英文術語不被切斷;在 Premiere 剪完後還能依實際時間軸重新對位 |
| 🎬 交回 Premiere | 產出帶審閱 marker 的 Premiere 專案 + SRT 字幕 + HTML 審閱報告;也可選「活專案」模式(全保留+顏色標籤,進 Premiere 再決定) |
| 🖱️ Premiere 面板 | 全程不離開 Premiere:選影片→一鍵剪輯→自動匯入;改設定按「重算剪輯」幾秒出新序列 |

## 快速開始

### 完全沒碰過程式?用安裝版

到 [Releases](https://github.com/xup61069/pr-autoedit/releases) 下載壓縮檔 →
解開到沒有中文和空格的路徑(例如 `C:\pr-autoedit`)→ **雙擊「安裝.bat」** →
重開 Premiere → 視窗 > 擴充功能 > PR剪教學一條龍。

安裝程式會自動處理 Python、ffmpeg、顯卡加速版的辨識引擎、還有面板安裝,
中間不用你做任何判斷。手把手說明看 **`新手指南.md`**。

### 會用命令列的話

詳細看 `SETUP.md`。三句話版本:

```
python -m venv venv && venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu128   # RTX 50 系列用 cu128;舊卡見 SETUP.md
pip install -r requirements.txt
python pipeline.py 你的影片.mp4
```

> CUDA 版本要對得上顯卡:RTX 50 系列(5080/5090)用 `cu128`,舊卡(30/20 系列)用 `cu121`。
> 細節看 `SETUP.md` 第 5 步。

跑完後,產物在 `output/影片名/`:先開 `04_report.html` 掃一遍,
再把 `04_project.xml` 匯入 Premiere,`04_subtitles.srt` 拖進字幕軌。

> 推薦裝 **Premiere 面板**(`premiere-panel/`,見其中的 README):
> 之後選影片、調設定、一鍵剪輯、匯入、重算,全部在 Premiere 裡完成,
> 不用再開命令列。

## 設計重點

- **只轉錄一次**:詞級時間戳是唯一真相來源,冗詞、靜音、字幕全從它衍生。
- **共用單一映射表**:字幕和 Premiere marker 用同一份重映射,保證永遠對齊。
- **信心分級**:必刪冗詞不下 marker;模糊判定才下,審閱只看這些。
- **聰明的快取**:調剪輯門檻重跑只重算決策、幾秒完成;改了辨識設定
  (引擎/模型/詞庫)會自動偵測並重新辨識,不會拿舊結果充數。
- **隨時可反悔**:「重算剪輯」永遠產生新序列,舊序列不動;降噪掛在
  Premiere 裡當效果,不烘死在音檔裡。
- **審閱優先**:程式不直接輸出成品,而是產出剪好的專案讓你把關,誤判可直接改。
- **可插拔、可自訂**:辨識引擎可換,參數集中管理,人人能調成自己的習慣。

## 兩種交付方式(DELIVERY_MODE)

- **baked(預設,直接剪好)**:匯入就是剪完的成品。想調靈敏度不用重跑全部:
  面板改設定 → 按「重算剪輯」→ 幾秒出一個新序列,舊的還在。
- **live(活專案)**:所有片段都保留、只切開並上顏色標籤
  (粉紅=靜音、青綠=音樂、紫=冗詞),進 Premiere 用
  「標籤 > 選取標籤群組」自己批次刪除或改速度。
  注意:片段數量多,長片在 Premiere 裡可能變卡,所以是備選。

## 高度自訂

所有參數都能在 **Premiere 面板的設定表單**裡調(白話說明、分組、滑條),
或直接改 `config/settings.py`。常用的幾個:

- `VOCAB_CATEGORIES` / `CUSTOM_VOCAB` —— 教學類型詞庫 + 你自己的術語,改善辨識最有效的一招
- `SILENCE_THRESHOLD_SEC` / `SILENCE_ACTION` / `SILENCE_SPEED_FACTOR` —— 停頓多久算靜音;要看畫面決定(`auto`,建議)、一律快轉還是一律剪掉;快轉幾倍
- `MUSIC_DETECT` / `MUSIC_DB_ABOVE_FLOOR` —— 音樂保護開關與靈敏度
- `ASR_ENGINE` —— 辨識引擎:`faster-whisper`(預設,中英夾雜較好)或 `funasr`(純中文備選)
- `PREMIERE_VOICE_FX` —— 進 Premiere 後幫人聲掛哪些效果(預設是內建的降噪 → EQ → 壓縮器,通用且不吃顯卡記憶體)
- `AUDIO_MODE` / `VST_CHAIN` / `VST_BAKE` —— 改走 VST 外掛離線烘進音檔的舊做法時才要管(預設不烘)
- `DELIVERY_MODE` —— baked / live(見上)

> **個人設定不會被更新覆蓋**:面板存的設定寫在 `config/settings_local.json`,
> 手動改的可放 `config/settings_local.py`。兩者都會蓋過預設值、不進版控。

## 專案結構

```
pr-autoedit/
├─ pipeline.py          主程式(執行入口)
├─ ui_settings.py       面板設定表單的欄位定義(加欄位面板自動長出)
├─ vst_tool.py          VST 外掛小工具(開介面調參數 / 查能力)
├─ config/
│  └─ settings.py       ★ 所有可調參數(門檻、詞表、引擎、VST 設定)
│     settings_local.json / .py   你的個人覆寫(不進版控)
├─ core/                核心邏輯(已測試,通常不用動)
│  ├─ models.py         資料結構
│  ├─ remap.py          時間戳重映射引擎(系統地基)
│  └─ decision.py       決策引擎(冗詞/靜音判定 + 音樂保護)
├─ modules/             各功能模組
│  ├─ audio_clean.py    音訊處理(VST / 開源 / 不處理;混回影片)
│  ├─ audio_probe.py    音樂/音效偵測(能量分析)
│  ├─ transcribe.py     語音轉錄(faster-whisper / FunASR,可切換)
│  ├─ premiere_xml.py   Premiere XML(baked 剪好 / live 活專案)+ marker
│  ├─ subtitles.py      SRT 字幕 + 簡轉繁
│  ├─ live_subs.py      依 Premiere 剪完的時間軸重新對位字幕
│  └─ report.py         HTML 審閱報告
├─ premiere-panel/      ★ Premiere 面板(一鍵剪輯,見其中的 README)
├─ tests/               測試(先跑這些確認環境沒問題)
├─ output/              產物(每支影片一個資料夾)
├─ requirements.txt     依賴清單
├─ SETUP.md             ★ Windows 安裝與使用說明(先看這個)
└─ README.md            本檔
```

## 文件導覽

| 文件 | 給誰看 |
|------|--------|
| `新手指南.md` | **完全沒碰過程式的人:從安裝到每天怎麼用(先看這個)** |
| `SETUP.md` | 想手動安裝、或會用命令列的人 |
| `PUBLISH.md` | 維護者:如何推上 GitHub |
| `CONTRIBUTING.md` | 貢獻者:改動原則 |
| `AGENTS.md` | AI 助手:日後找 AI 改專案時,請對方先讀這份 |
