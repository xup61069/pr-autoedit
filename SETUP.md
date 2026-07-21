# 安裝與使用說明(Windows + NVIDIA GPU)

這份是給「會跑腳本但不常寫程式」的人。照著做,遇到紅字先看最後的「常見錯誤」。

---

## 第一部分:一次性安裝(大約 30 分鐘)

### 1. 裝 Python(3.11 ~ 3.13 都可以)
到 python.org 下載 3.11、3.12 或 3.13 版(都實測可用)。
安裝時**務必勾選** "Add Python to PATH"。

> 小提醒:只有「免費降噪 DeepFilterNet」那條選用路線在 3.13 上要另外裝 Rust
> 才裝得起來(見第 6 步);主流程與 VST 降噪在 3.13 完全正常。

裝完打開「命令提示字元」(cmd),輸入確認:
```
python --version
```
應該顯示 `Python 3.11 / 3.12 / 3.13` 其中之一。

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
到 https://pytorch.org/get-started/locally/ 選 Windows + Pip,**CUDA 版本要對得上你的顯卡**,複製它給的指令來裝。

> ⚠️ **新顯卡(RTX 50 系列,如 5080/5090)要特別注意**
> 這些卡是新架構(Blackwell),**必須用 cu128 或更新**,舊的 `cu121` 會裝到但跑不動。
> 較新的卡:
> ```
> pip install torch --index-url https://download.pytorch.org/whl/cu128
> ```
> 較舊的卡(RTX 30/20 系列)才用:
> ```
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ```
> 不確定的話,以 pytorch.org 官網當下建議的最新 CUDA 版本為準。

裝完驗證 GPU 有被抓到:
```
python -c "import torch; print(torch.cuda.is_available())"
```
要顯示 `True`。若是 `False`,先更新 NVIDIA 驅動再試。

### 6. 裝其餘套件
```
pip install -r requirements.txt
```
這步會花幾分鐘,faster-whisper 相關套件比較大。

> 免費降噪(DeepFilterNet)預設不裝——它需要先裝 Rust,且在 Python 3.13 上較麻煩。
> 若你用自己的 VST 外掛降噪(pedalboard,已包含在上面的安裝裡)就不需要它。
> 想用免費降噪再看 `requirements.txt` 裡的說明。

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
| `01_clean_av.mp4` | 混好聲音的影片,是專案引用的素材。**別搬走或改名**,搬了序列會離線 |
| `_work/` | 程式自己用的中繼檔(音軌、辨識快取…),不用理它。整個刪掉也沒關係,只是下次要重跑辨識,比較久 |

### 在 Premiere 裡的審閱流程
1. 檔案 → 匯入 → 選 `04_project.xml`,會多出一條剪好的序列
2. 用 `Shift+M`(下一個 marker)、`Ctrl+Shift+M`(上一個)逐點跳
3. 每個 marker 聽 1~2 秒,確認接口順不順
4. 誤刪的話:選相鄰兩個 clip 的交界做 rolling edit(按住 N 選滾動編輯工具)拉回來
5. 沒問題就輸出

### 調整判定(讓它更貼合你的說話習慣)
用 Premiere 面板的話,這些全部在面板的「⚙ 設定」表單裡調(有白話說明);
手動改的話在 `config\settings.py`。常調的幾個:
- `VOCAB_CATEGORIES` + `CUSTOM_VOCAB`:**改善辨識最有效的一招**。
  選你影片的教學類型(剪輯/編曲/特效/3D/動畫/遊戲/程式/攝影,
  會自動載入該領域術語),再把你常講的頻道名、人名、慣用詞列進 CUSTOM_VOCAB
  (例如 MIDI 才不會被聽成「謎底」)。
  內建的詞不合用?面板的教學類型旁邊有「**✎ 編輯類型**」:可以改內建那幾類的詞,
  也可以自己開新類型(木工、烘焙、直播…)。改動存在 `config\vocab_local.json`,
  不進版控、更新專案不會被蓋掉;按「還原成內建」隨時回得去。
  編輯器會一邊打一邊告訴你用掉多少額度 —— 提示詞長度有硬上限,
  超過的詞模型直接看不到而且不會報錯,所以別把不會被聽錯的詞也收進去。
- `SILENCE_THRESHOLD_SEC`:靜音門檻,講話慢的人調高(1.5),快的人調低(1.0)
- `SILENCE_ACTION`:停頓怎麼處理,三選一 ——
  `"auto"`=看畫面決定(建議:畫面在動就快轉帶過、靜止才剪掉,
  你默默示範操作的那幾秒不會消失)、`"speed"`=一律快轉什麼都不刪、
  `"delete"`=一律剪掉
- `MUTE_SPEED_AUDIO`:快轉段是否靜音(True 可避免加速產生的尖聲)
- `SILENCE_SPEED_FACTOR`:快轉倍率(預設 6.0)
- `FILLERS_CONDITIONAL`:加入你的個人口頭禪
- `MUSIC_DETECT`:音樂/音效保護(預設開)。沒講話但有聲音的段落
  (預覽音樂、示範音效)會自動保留,不被當靜音剪掉;
  報告最下面會列出抓到的音樂段,漏抓/誤抓就調「音樂偵測靈敏度」。

> **不想動到共用設定?** 面板調的設定會存進 `config\settings_local.json`;
> 手動改的可自建 `config\settings_local.py`(例:`CUSTOM_VOCAB = ["我的頻道名"]`)。
> 兩者都蓋過預設值、不進版控、更新專案也不會被覆蓋。

**調完重跑不用重新轉錄** —— 轉錄有快取(`_work/02_transcript.json`),
改剪輯門檻重跑只會重算決策那步,幾秒就好:
```
python pipeline.py D:\影片\我的教學_0718.mp4 --skip-audio
```
(用面板的話,按「重算剪輯」就是在做這件事,還會自動匯入新序列)

改了「辨識」相關設定(引擎、模型、語言、詞庫)想讓辨識重來?
**什麼都不用做**——快取記得當時的辨識設定,發現變了會自動重新辨識。

### 交付方式:直接剪好 vs 活專案(DELIVERY_MODE)
- `"baked"`(預設):匯入 Premiere 就是剪完的成品,搭配「重算剪輯」隨時調。
- `"live"`:所有片段全保留、只切開上顏色標籤(粉紅=靜音、青綠=音樂、紫=冗詞),
  在時間軸右鍵「標籤 > 選取標籤群組」一次選同色片段自己批次處理。
  片段多,長片可能讓 Premiere 變卡,所以是備選。

### 人聲處理怎麼運作
預設 `AUDIO_MODE = "vst"` + `VST_BAKE = False`,意思是:
交出去的聲音**保持原始錄音**(只做響度標準化),人聲加工**在 Premiere 裡掛效果**——
隨時可調、隨時可關,不會烘死在音檔裡。

預設掛的是 **Premiere 內建的三件套**(`PREMIERE_VOICE_FX`):

| 順序 | 效果 | 做什麼 |
|---|---|---|
| 1 | 降噪 DeNoise | 去掉冷氣、電腦風扇那類持續底噪 |
| 2 | 參數等化器 Parametric Equalizer | 修掉悶悶的低頻和刺耳的高頻 |
| 3 | 動態 Dynamics | 把忽大忽小的音量壓穩 |

選內建的原因有三個:**通用**(每套 Premiere 都有,不必另外安裝、不挑顯示卡
廠牌)、**高效能**(純 CPU,不佔顯示卡記憶體)、**適合人聲**(這正是
「基本音效 > 對話」在做的三件事)。掛法二選一:

- **最穩也最快的做法**:視窗 > 音軌混音器,在 A1 軌的效果插槽由上往下
  依序選這三個。插槽有五格,三個放得下;整軌一次搞定,片段再多也一樣快,
  隨時可以調參數或整個關掉比較差異。
- 面板「剪輯後工具」按「幫目前序列掛人聲處理」(實驗性,自動掛到每個
  聲音片段,音樂段除外)。片段太多會拒絕——每片段一組,乘上三個效果
  就是好幾千個實例,時間軸會變得很頓。

> 效果名稱會**跟著 Premiere 的介面語言翻譯**,所以 `PREMIERE_VOICE_FX`
> 每一項給的是一串候選名稱,依序去試、找到哪個用哪個。萬一都對不上,
> 面板會把你這台實際有的效果清單印在訊息區,照著填進設定就會認得。

想用更強的降噪(例如 NVIDIA VoiceFX 這類 AI 外掛),就在音軌混音器裡
把第一格換成它——其餘兩格照舊。代價是每個實例都吃顯示卡記憶體。

想回到「先處理好再交付」的舊做法(**用 VST 外掛離線烘進音檔**):把 `VST_BAKE`
設為 True(面板進階設定「把降噪烘進音檔」打勾),並確認 `VST_CHAIN` 指到你的
降噪外掛(多個外掛就依序排:降噪→EQ→壓縮→limiter),重跑時不要加 --skip-audio。
注意這條路一旦烘進去就改不了,`PREMIERE_VOICE_FX` 那三件套也就用不到了。

> `VST_CHAIN` 預設會自動到標準 VST3 資料夾找 VoiceFX,裝了的人不必設定。
> 想改用別的外掛、或裝在非標準位置,就在面板「進階 > VST 外掛路徑」自己指定。
> 注意 VST3 的外層其實是個資料夾殼,要指到「內層」那顆檔案(例如
> `...\VoiceFX.vst3\Contents\x86_64-win\VoiceFX.vst3`),
> 指到外層資料夾會載入失敗。載入不了就改試內層路徑。
> `"none"` = 完全不處理聲音,第一次測整條管線用這個最快。

### 想全程不離開 Premiere?(推薦)
專案內有一個 `premiere-panel\` 資料夾,是 Premiere 面板:選影片、調設定、
一鍵剪輯、自動匯入,剪完還有「開啟審閱報告 / 重算剪輯 / 掛降噪 / 產字幕」
四顆按鈕。安裝與使用見 `premiere-panel\README.md`。

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

**auto-editor 相關錯誤**
→ 程式會直接停下來並說明,不會假裝成功(以前只印一行警告就結束,
   面板會誤把上一次的舊剪輯匯進 Premiere,看起來像設定沒生效)。
   照訊息執行 `pip install auto-editor` 即可。
