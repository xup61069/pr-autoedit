# 安裝與使用說明(Windows + NVIDIA GPU)

這份是給「會跑腳本但不常寫程式」的人。照著做,遇到紅字先看最後的「常見錯誤」。

---

## 第一部分:一次性安裝(大約 30 分鐘)

### 1. 裝 Python 3.11
到 python.org 下載 3.11 版(**不要用 3.12 以上**,有些套件還沒跟上)。
安裝時**務必勾選** "Add Python to PATH"。

裝完打開「命令提示字元」(cmd),輸入確認:
```
python --version
```
應該顯示 `Python 3.11.x`。

### 2. 裝 ffmpeg
1. 到 https://www.gyan.dev/ffmpeg/builds/ 下載 "ffmpeg-release-full.7z"
2. 解壓縮,把裡面 `bin` 資料夾的路徑(例如 `C:\ffmpeg\bin`)加入系統 PATH:
   - 搜尋「編輯系統環境變數」→ 環境變數 → 在 Path 新增那個路徑
3. **重開 cmd**,輸入 `ffmpeg -version` 確認能跑

### 3. 把專案放好
把整個 `pr-autoedit` 資料夾放到你想要的位置,例如 `D:\pr-autoedit`。
在 cmd 進入該資料夾:
```
cd /d D:\pr-autoedit
```

### 4. 建立虛擬環境(隔離套件,避免污染系統)
```
python -m venv venv
venv\Scripts\activate
```
成功的話,命令列前面會出現 `(venv)`。**之後每次使用都要先跑這行 `venv\Scripts\activate`。**

### 5. 裝 PyTorch(CUDA 版)—— 這步最容易錯
**不要**直接 `pip install torch`,那是 CPU 版,GPU 用不到。
到 https://pytorch.org/get-started/locally/ 選 Windows + Pip + CUDA 12.x,
複製它給的指令,大概長這樣:
```
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

裝完驗證 GPU 有被抓到:
```
python -c "import torch; print(torch.cuda.is_available())"
```
要顯示 `True`。若是 `False`,先更新 NVIDIA 驅動再試。

### 6. 裝其餘套件
```
pip install -r requirements.txt
```
這步會花幾分鐘。DeepFilterNet 和 faster-whisper 比較大。

### 7. 驗證安裝
```
python -m tests.test_remap
python -m tests.test_decision
python -m tests.test_e2e_smoke
```
三個都顯示「全部通過」就代表核心沒問題。

---

## 第二部分:每次使用

### 基本用法
```
venv\Scripts\activate
python pipeline.py D:\影片\我的教學_0718.mp4
```

第一次跑會下載 Whisper 模型(約 3GB),之後就快取了。
跑完產物在 `output\我的教學_0718\`:

| 檔案 | 用途 |
|------|------|
| `04_report.html` | **先開這個**,瀏覽器打開,掃一遍切點有沒有大面積誤判 |
| `04_project.xml` | 匯入 Premiere 的專案(檔案 → 匯入) |
| `04_subtitles.srt` | 拖進字幕軌 |

### 在 Premiere 裡的審閱流程
1. 檔案 → 匯入 → 選 `04_project.xml`,會多出一條剪好的序列
2. 用 `Shift+M`(下一個 marker)、`Ctrl+Shift+M`(上一個)逐點跳
3. 每個 marker 聽 1~2 秒,確認接口順不順
4. 誤刪的話:選相鄰兩個 clip 的交界做 rolling edit(按住 N 選滾動編輯工具)拉回來
5. 沒問題就輸出

### 調整判定(讓它更貼合你的說話習慣)
改 `config\settings.py`,常調的幾個:
- `SILENCE_THRESHOLD_SEC`:靜音門檻,講話慢的人調高(1.5),快的人調低(1.0)
- `FILLERS_CONDITIONAL`:加入你的個人口頭禪
- `SILENCE_ACTION`:改 `"delete"` 就是直接剪掉靜音而非快轉

**調完重跑不用重新轉錄** —— 轉錄有快取(02_transcript.json),
改門檻重跑只會重算決策那步,幾秒就好:
```
python pipeline.py D:\影片\我的教學_0718.mp4 --skip-audio
```

### 音訊要走 VST 路線的話
1. 改 `config\settings.py` 的 `AUDIO_MODE = "vst"`
2. 把你的 .vst3 路徑依序填進 `VST_CHAIN`(降噪→EQ→壓縮→limiter 的順序)
3. 重跑(這時不要加 --skip-audio,因為要重做音訊)

---

## 常見錯誤

**`torch.cuda.is_available()` 是 False**
→ NVIDIA 驅動太舊。到 nvidia.com 更新驅動,或裝的 torch CUDA 版本比驅動新。

**`ffmpeg 不是內部或外部命令`**
→ PATH 沒設好,或 cmd 沒重開。重設 PATH 後關掉 cmd 重開。

**Whisper 報 `float16` 相關錯誤**
→ 改 `config\settings.py` 的 `WHISPER_COMPUTE_TYPE = "int8_float16"`。

**GPU 記憶體不足(out of memory)**
→ 把 `WHISPER_MODEL` 改成 `"medium"`,準確度略降但省一半記憶體。

**auto-editor 相關錯誤導致沒產出 XML**
→ 其他產物(字幕、報告)還是會出。先確認 `pip install auto-editor` 有成功。
   XML 是審閱模式必要的,務必把這個裝好。
