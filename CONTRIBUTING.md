# 貢獻指引

歡迎貢獻。這個專案刻意保持簡單:純 Python、模組化、每個核心邏輯都有測試。

## 開發環境

照 SETUP.md 裝好環境後,改動前先確認測試都通過:

```
python -m tests.test_remap
python -m tests.test_decision
python -m tests.test_e2e_smoke
node tests/test_panel_voicefx.js    # 面板掛人聲效果的邏輯(不必開 Premiere)
node tests/test_panel_vocab.js      # 教學類型編輯器的額度試算
node tests/test_panel_stop.js       # 停止鈕:收整棵行程樹、停止不等於失敗
```

## 介面文案在哪裡改

想改面板上的字,照這張表找:

| 想改什麼 | 檔案 |
|---|---|
| 設定項的標題、說明、分組名稱 | `ui_settings.py` 的 `FIELDS`(每筆的 `label` / `hint` / `group`) |
| 按鈕文字、固定的段落說明 | `premiere-panel/index.html` |
| 跑的時候跳出來的訊息、錯誤解釋 | `premiere-panel/js/main.js` |
| 審閱報告裡的文字 | `modules/report.py` |
| 設定檔本身的註解(不是 UI,但常一起改) | `config/settings.py` |

改完 `ui_settings.py` 的文案,面板重開就會生效(表單是照 `FIELDS` 自動長出來的,
不必動 HTML)。改 `index.html` / `main.js` / `style.css` 也是重開面板即可。

## 改動原則

1. **時間一律用幀,不用秒。** 原因見 AGENTS.md,這是硬性規定。
2. **動 core/ 就要重跑對應測試。** 這個專案的 bug 幾乎都是時間映射錯位。
3. **可調參數放 config/settings.py,不要寫死。** 使用者不該為了調門檻去改邏輯。
4. **牽涉時間計算的新功能,先加測試再寫實作。**

## 送 PR 前

- 跑過三個測試,全部通過
- 若新增了可調參數,更新 SETUP.md 的說明
- 若改了架構或設計決定,更新 AGENTS.md

## 回報問題

開 issue 時附上:作業系統、Python 版本、完整錯誤訊息,以及(若是判定問題)
`output/你的影片/04_report.html` 的內容。
