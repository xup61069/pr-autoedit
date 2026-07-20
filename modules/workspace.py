"""輸出資料夾的檔案要放哪 —— 集中在這裡決定。

output/影片名/ 底下原本平鋪十幾個檔案,但其中只有三、四個是「你會打開的」,
其餘全是程式自己用的中繼檔(抽出來的音軌、轉錄快取、決策結果…)。
擺在一起的結果是你根本分不出哪個能點、哪個是雜訊。

所以分兩層:
  最外層     你會用到的:審閱報告、要匯入的專案、字幕、混好聲音的影片
  _work/     程式自用的中繼檔,不用理它(想省硬碟時整個刪掉也沒關係,
             下次跑會重新產生 —— 只是要重跑辨識,比較久)

資料夾名稱刻意用純英文:這個路徑會被 Premiere 的 ExtendScript 讀寫,
中文路徑在那個環境有踩過編碼的雷,不值得冒險。
"""

from __future__ import annotations
import os
import shutil

# 程式自用的中繼檔資料夾名稱
INTERNAL_DIR = "_work"

# 哪些是「程式自用」的。沒列到的一律留在最外層。
_INTERNAL_FILES = {
    "01_raw.wav",           # 從影片抽出來的原始音軌
    "01_clean.wav",         # 降噪後、還沒調響度的中繼音檔
    "01_clean_norm.wav",    # 調完響度的音檔(拿去混回影片的那份)
    "01_clean_gated.wav",   # 把快轉段抹靜音後的音檔
    "01_mux_info.json",     # 上次混音的指紋(判斷能不能沿用)
    "01_audio_info.json",   # 上次聲音處理用的設定指紋
    "02_transcript.json",   # 語音辨識快取(重跑最花時間的就是它)
    "03_timeline.json",     # 決策引擎的段落清單
    "03_timeline.v1.json",  # 給剪輯引擎吃的中繼格式
    "04_project_raw.xml",   # 還沒加審閱標記的專案
    "05_layout.json",       # 從 Premiere 讀回來的序列版面
}

# 早期版本留下、現在已經沒有任何程式在讀的檔案
_OBSOLETE_FILES = {
    "01_mux_info.txt",      # 被 01_mux_info.json 取代
}


def wpath(work_dir: str, filename: str) -> str:
    """這個檔案該放哪。程式自用的收進 _work/,其餘留在最外層。"""
    if filename in _INTERNAL_FILES:
        return os.path.join(work_dir, INTERNAL_DIR, filename)
    return os.path.join(work_dir, filename)


def prepare(work_dir: str) -> None:
    """建好 _work/,把舊版平鋪在外層的中繼檔搬進去,並清掉已淘汰的檔案。

    搬移而不是重新產生,是為了保住轉錄快取 —— 那是最花時間的一步,
    弄丟了就要重跑好幾分鐘的語音辨識。"""
    inner = os.path.join(work_dir, INTERNAL_DIR)
    os.makedirs(inner, exist_ok=True)

    moved = 0
    for name in _INTERNAL_FILES:
        old = os.path.join(work_dir, name)
        if not os.path.exists(old):
            continue
        try:
            shutil.move(old, os.path.join(inner, name))
            moved += 1
        except OSError:
            pass        # 搬不動(檔案被鎖住)就算了,不值得為此中斷整條管線

    removed = 0
    for name in _OBSOLETE_FILES:
        p = os.path.join(work_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass

    if moved or removed:
        print(f"  整理輸出資料夾:{moved} 個中繼檔收進 {INTERNAL_DIR}/"
              + (f"、清掉 {removed} 個已淘汰的舊檔" if removed else ""))


def tidy(work_dir: str) -> None:
    """跑完之後清掉純中繼的音檔,省硬碟。

    只刪「重跑時本來就會重新產生、而且不會拖慢速度」的:
    01_clean.wav 是降噪完還沒調響度的半成品,調完就沒用了。
    刻意不刪 01_raw.wav 與 02_transcript.json —— 前者是音量分析的依據、
    後者是辨識快取,刪了會害你下次重算變成重跑好幾分鐘。"""
    for name in ("01_clean.wav",):
        p = wpath(work_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
