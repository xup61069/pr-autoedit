# PR 自動剪輯 —— Premiere 面板(CEP)

在 Premiere Pro 裡按一個按鈕,就跑完整條自動剪輯,並把剪好的序列與字幕匯回來。
你完全不用離開 Premiere。

> 這是「薄薄一層」的面板:真正的功能(降噪、轉錄、剪輯)都在上層的 Python
> 程式裡。面板只負責「啟動 Python」和「匯入結果」。

## 運作方式

```
面板按鈕 → 執行 ../pipeline.py 你的影片 → 匯入 output/影片名/04_project.xml + 字幕
```

## 安裝(只需做一次)

1. **確認路徑**:打開 `js/main.js`,檢查最上面兩行是否符合你的電腦:
   ```js
   var PROJECT_DIR = "C:\\pr-autoedit";                         // 專案資料夾
   var PYTHON      = "C:\\Users\\Administrator\\miniconda3\\python.exe"; // Python
   ```

2. **開啟開發者模式**:雙擊執行 `enable-debug-mode.reg`(允許載入自製面板)。

3. **安裝面板**:雙擊執行 `install.bat`(把本資料夾連結到 Premiere 擴充目錄)。

4. **重啟 Premiere Pro**,然後在選單:
   `視窗 (Window) > 擴充功能 (Extensions) > PR 自動剪輯`

## 使用

1. 先在 Premiere 開啟(或新建)一個專案。
2. 面板上按「選擇影片」挑你的錄影檔。
3. 按「一鍵自動剪輯」,等進度跑完。
4. 完成後會自動匯入剪好的序列與字幕,審閱 marker 即可。

## 疑難排解

- **面板選單裡找不到**:確認做了步驟 2(reg)和 3(install.bat),並已重啟 Premiere。
- **按下去說找不到 Python**:檢查 `main.js` 的 `PYTHON` 路徑。
- **想看詳細錯誤**:面板下方的黑框會顯示 Python 的完整輸出訊息。
- **面板改版後沒更新**:因為用 junction 連結,改完存檔重開面板即可;
  改 `manifest.xml` 則要重啟 Premiere。
