# 貢獻指引

歡迎貢獻。這個專案刻意保持簡單:純 Python、模組化、每個核心邏輯都有測試。

## 開發環境

照 SETUP.md 裝好環境後,改動前先確認測試都通過:

```
python -m tests.test_remap
python -m tests.test_decision
python -m tests.test_e2e_smoke
node tests/test_panel_voicefx.js    # 面板掛人聲效果的邏輯(不必開 Premiere)
```

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
