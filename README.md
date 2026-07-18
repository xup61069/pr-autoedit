# PR 自動剪輯工具

錄完影片丟進去 → 自動音訊清理、去冗詞、靜音快轉、產字幕 → 輸出一個「已經剪好」的
Premiere 專案,你只要跳著確認每個切點,通過就輸出。目標是把單支教學片的剪輯時間
從 2~3 小時壓到 5~10 分鐘人工。

授權:MIT。歡迎自由使用、修改、散布。

## 文件導覽

| 文件 | 給誰看 |
|------|--------|
| `SETUP.md` | 使用者:安裝與日常操作(先看這個) |
| `PUBLISH.md` | 維護者:如何推上 GitHub |
| `CONTRIBUTING.md` | 貢獻者:改動原則 |
| `AGENTS.md` | AI 助手:日後找 AI 改專案時,請對方先讀這份 |

## 專案結構

```
pr-autoedit/
├─ pipeline.py          主程式(執行入口)
├─ config/
│  └─ settings.py       ★ 所有可調參數(門檻、詞表、Whisper 設定)
├─ core/                核心邏輯(已測試,通常不用動)
│  ├─ models.py         資料結構
│  ├─ remap.py          時間戳重映射引擎(系統地基)
│  └─ decision.py       決策引擎(冗詞/靜音判定)
├─ modules/             各功能模組
│  ├─ audio_clean.py    音訊清理(VST / DeepFilterNet 兩條路)
│  ├─ transcribe.py     語音轉錄(faster-whisper)
│  ├─ premiere_xml.py   Premiere XML + marker
│  ├─ subtitles.py      SRT 字幕 + 簡轉繁
│  └─ report.py         HTML 審閱報告
├─ tests/               測試(先跑這些確認環境沒問題)
├─ output/              產物(每支影片一個資料夾)
├─ requirements.txt     依賴清單
├─ SETUP.md             ★ Windows 安裝與使用說明(先看這個)
└─ README.md            本檔
```

## 快速開始

看 `SETUP.md`。三句話版本:

```
python -m venv venv && venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
python pipeline.py 你的影片.mp4
```

## 設計重點

- **只轉錄一次**:Whisper 詞級時間戳是唯一真相來源,冗詞、靜音、字幕全從它衍生。
- **共用單一映射表**:字幕和 Premiere marker 用同一份重映射,保證永遠對齊。
- **信心分級**:必刪冗詞(嗯、呃)不下 marker;模糊判定(就是、然後)才下,審閱只看這些。
- **轉錄快取**:調門檻重跑不用重新轉錄,幾秒完成。
- **審閱優先**:程式不直接輸出成品,而是產出剪好的 PR 專案讓你把關,誤判可直接在 PR 修。
```
