# PR 自動剪輯工具

錄完影片丟進去 → 自動音訊清理(降噪、響度標準化)、去冗詞、靜音快轉、產繁中字幕
→ 輸出一個「已經剪好」的 Premiere 專案。你只要跳著確認每個切點,通過就輸出。
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
| 🎙️ 音訊清理 | 用你自己的 VST 外掛(或開源方案)降噪,並標準化到 YouTube 響度 |
| 📝 語音轉文字 | 詞級時間戳,是整個系統的唯一真相來源;引擎可切換 |
| ✂️ 去冗詞 | 「嗯、呃」必刪;「就是、然後」這類看語境判斷,低信心的留給你確認 |
| ⏩ 停頓處理 | 靜音自動快轉或刪除;快轉段可自動靜音,避免加速尖聲 |
| 💬 繁中字幕 | 標點感知斷行、簡轉繁(OpenCC),英文術語不被切斷 |
| 🎬 交回 Premiere | 產出帶審閱 marker 的 Premiere 專案 + SRT 字幕 + HTML 審閱報告 |

## 快速開始

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

## 設計重點

- **只轉錄一次**:詞級時間戳是唯一真相來源,冗詞、靜音、字幕全從它衍生。
- **共用單一映射表**:字幕和 Premiere marker 用同一份重映射,保證永遠對齊。
- **信心分級**:必刪冗詞不下 marker;模糊判定才下,審閱只看這些。
- **轉錄快取**:調門檻重跑不用重新轉錄,幾秒完成。
- **審閱優先**:程式不直接輸出成品,而是產出剪好的專案讓你把關,誤判可直接改。
- **可插拔、可自訂**:辨識引擎可換,參數集中管理,人人能調成自己的習慣。

## 高度自訂

所有可調參數集中在 `config/settings.py`,想調什麼都在那裡,例如:

- `CUSTOM_VOCAB` —— 放你常講的術語、軟體名、頻道名,提高辨識準確度
- `SILENCE_SPEED_FACTOR` / `SILENCE_ACTION` —— 快轉倍數、靜音要快轉還是刪除
- `MUTE_SPEED_AUDIO` —— 快轉段是否靜音(避免加速尖聲)
- `ASR_ENGINE` —— 辨識引擎(目前 `faster-whisper`,預留 `funasr`)
- `AUDIO_MODE` / `VST_CHAIN` —— 走 VST、開源降噪、或不處理;VST 外掛路徑

> **不想動到共用設定?** 在 `config/` 底下建一個 `settings_local.py`,
> 裡面的設定會蓋過預設值,而且不進版控、更新專案也不會被覆蓋。

## 專案結構

```
pr-autoedit/
├─ pipeline.py          主程式(執行入口)
├─ config/
│  └─ settings.py       ★ 所有可調參數(門檻、詞表、引擎、VST 設定)
├─ core/                核心邏輯(已測試,通常不用動)
│  ├─ models.py         資料結構
│  ├─ remap.py          時間戳重映射引擎(系統地基)
│  └─ decision.py       決策引擎(冗詞/靜音判定)
├─ modules/             各功能模組
│  ├─ audio_clean.py    音訊清理(VST / 開源 / 不處理三模式)
│  ├─ transcribe.py     語音轉錄(引擎可切換:faster-whisper …)
│  ├─ premiere_xml.py   Premiere XML + marker
│  ├─ subtitles.py      SRT 字幕 + 簡轉繁
│  └─ report.py         HTML 審閱報告
├─ tests/               測試(先跑這些確認環境沒問題)
├─ output/              產物(每支影片一個資料夾)
├─ requirements.txt     依賴清單
├─ SETUP.md             ★ Windows 安裝與使用說明(先看這個)
└─ README.md            本檔
```

## 文件導覽

| 文件 | 給誰看 |
|------|--------|
| `SETUP.md` | 使用者:安裝與日常操作(先看這個) |
| `PUBLISH.md` | 維護者:如何推上 GitHub |
| `CONTRIBUTING.md` | 貢獻者:改動原則 |
| `AGENTS.md` | AI 助手:日後找 AI 改專案時,請對方先讀這份 |
