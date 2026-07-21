# 貢獻指引

歡迎貢獻。這個專案刻意保持簡單:純 Python、模組化、每個核心邏輯都有測試。

## 開發環境

照 SETUP.md 裝好環境後,改動前先確認測試都通過。**十一套都要跑**:

```
python -m tests.test_decision      # 冗詞、停頓、微剪、重講、設定組合的承諾
python -m tests.test_remap         # 時間映射與字幕斷行(系統地基)
python -m tests.test_music         # 音樂保護、雜音剪除、畫面活動判定
python -m tests.test_live_xml      # 活專案 XML:標籤色、marker、片段覆蓋
python -m tests.test_live_subs     # 依 Premiere 序列版面重新對位字幕
python -m tests.test_e2e_smoke     # 主幹跑得通 + 提示詞長度 + 面板設定涵蓋率
node tests/test_panel_voicefx.js   # 面板掛人聲效果的邏輯(不必開 Premiere)
node tests/test_panel_vocab.js     # 教學類型編輯器的額度試算
node tests/test_panel_stop.js      # 停止鈕:收整棵行程樹、停止不等於失敗
node tests/test_panel_merge.js     # 多檔合併:命名與排序要跟 Python 算得一樣
node tests/test_panel_errors.js    # 錯誤翻譯:不准給錯答案
```

> ⚠️ **驗證指令不要接管線。**`python -m tests.xxx | tail` 的離開碼是 `tail` 的,
> 失敗的測試會被靜靜吃掉,看起來一切正常。要嘛直接跑,要嘛單獨驗離開碼。

> 這份清單以前只列三到六套,漏掉的正好是 `test_music` —— 也就是守著畫面
> 判定與雜音剪除的那一套。結果就是有人照文件跑測試、全綠、交付,而
> live 模式的標籤 bug 活了很久。`test_e2e_smoke` 現在會檢查這幾份文件
> 有沒有列滿九套,漏了會紅。

## 介面文案在哪裡改

想改面板上的字,照這張表找:

| 想改什麼 | 檔案 |
|---|---|
| 設定項的標題、說明、分組名稱 | `ui_settings.py` 的 `FIELDS`(每筆的 `label` / `hint` / `group`) |
| 按鈕文字、固定的段落說明 | `premiere-panel/index.html` |
| 跑的時候跳出來的訊息、錯誤解釋 | `premiere-panel/js/main.js`(常見錯誤的白話翻譯在 `ERROR_TABLE`) |
| 審閱報告裡的文字 | `modules/report.py` |
| 活專案裡片段的名字與標籤色 | `modules/premiere_xml.py` 的 `_CLIP_NAMES` / `_LABELS` |
| 設定檔本身的註解(不是 UI,但常一起改) | `config/settings.py` |

改完 `ui_settings.py` 的文案,面板重開就會生效(表單是照 `FIELDS` 自動長出來的,
不必動 HTML)。改 `premiere-panel/index.html`、`premiere-panel/js/main.js`、
`premiere-panel/css/style.css` 也是重開面板即可(CSS 沒更新的話把 Premiere
完全關掉再開)。

## 改動原則

1. **時間一律用幀,不用秒。** 原因見 AGENTS.md,這是硬性規定。
2. **動 core/ 就要重跑對應測試。** 這個專案的 bug 幾乎都是時間映射錯位。
3. **可調參數放 config/settings.py,不要寫死。** 使用者不該為了調門檻去改邏輯。
4. **牽涉時間計算的新功能,先加測試再寫實作。**

## 送 PR 前

- 跑過**十一套**測試,全部通過(離開碼要單獨驗,不要接管線)
- 若新增了可調參數,把它加進 `ui_settings.py` 的 `FIELDS`(面板會自動長出
  控制項);真的不該做成控制項就加進 `PANEL_OMITTED_KEYS` 並寫明理由。
  兩個都沒做的話 `test_e2e_smoke` 會紅——這是防止「程式在用、面板卻找不到」
- 若新增了段落種類(`Segment.reason`),要在 `modules/premiere_xml.py` 補
  對應的標籤色與中文名,並把它加進 `test_live_xml` 的假資料
- 若改了架構或設計決定,更新 AGENTS.md

## 回報問題

開 issue 時附上:作業系統、Python 版本、完整錯誤訊息,以及(若是判定問題)
`output/你的影片/04_report.html` 的內容。
