"""進度回報 —— 讓「沒動靜的那幾分鐘」看得出來還活著。

問題:整條管線最花時間的幾步(語音轉錄、掃畫面、混音)在畫面上都是
一行字然後一片安靜。34 分鐘的 4K 片,語音轉錄可能要跑好幾分鐘,
中間完全沒有輸出 —— 使用者分不出「還在跑」跟「當掉了」,
只好一直等、或者按停止重來。

做法:這些步驟固定印出一種「機器看得懂、人也看得懂」的進度行:

    [進度] 語音轉錄 45% 剩約 3.2 分

面板認得 `[進度]` 開頭的行,把它畫成進度條而不是一直往下堆訊息
(見 premiere-panel/js/main.js 的 appendLog)。直接在命令列跑的人
看到的就是一行一行的百分比,一樣讀得懂。

這裡的百分比是「整條處理走到哪」,不是「這一小步走到哪」。開跑前 pipeline
會用 begin_run() 登記這次會跑哪些步驟,每步分到整條的一段,進度條就只會
往前、不會每換一步就從 0% 重來(那會看起來像壞掉)。後面那個「剩約 X 分」
是用實際已經花掉的時間,對照整條走了幾成回推的。沒有登記整條執行時
(直接跑命令列、單元測試),退回原本「每步各自 0→100% + 已完成/總長」的樣子:

    [進度] 語音轉錄 45% 12.3/27.0 分

刻意「不」用 \\r 原地覆寫:面板的訊息區是一直往下接的,\\r 在那裡
沒有作用,只會變成一行超長的亂碼。
"""

from __future__ import annotations
import time

# 進度行的開頭。面板靠這個字串認出它,兩邊要一致。
PREFIX = "[進度]"

# 節流:百分比沒變、或距離上次印不到這麼多秒,就不要再印。
# 不節流的話,轉錄一支長片會吐出好幾千行,把真正的訊息淹掉。
_MIN_INTERVAL_SEC = 0.4


# ---------- 整條執行共用的「一路往前」總進度 ----------
#
# 問題:一次處理其實有好幾個小步驟(抽音軌、響度、轉錄、掃畫面、混音),
# 每個都自己從 0% 衝到 100%。它們共用面板上同一條進度條的話,你就會看到
# 條子一次又一次填滿又清空,像壞掉一樣。
#
# 做法:開跑前先登記「這次會依序跑哪些會顯示進度的步驟」,每個步驟分到
# 整條的一段。之後每個步驟回報的不是「自己走到哪」,而是「整條走到哪」——
# 只會往前、不會回頭。附帶算出「剩約 X 分」:用實際已經花掉的時間,
# 對照整條走了幾成,回推還要多久。

# 各步驟佔整條的相對比重(粗估的「大概花多少時間」)。只影響進度條走得
# 順不順(某一步比重大,條子在那一段就爬得慢);「剩約 X 分」是用實際
# 經過時間回推的,估得再糙也會自己修正,不靠這張表準不準。
_STAGE_WEIGHTS = {
    "抽出音軌": 1.0,
    "響度標準化": 1.0,
    "語音轉錄": 8.0,      # 整條裡最久的一步,分到最大一段
    "分析畫面活動": 4.0,
    "混回影片": 2.0,
}
_DEFAULT_WEIGHT = 1.0


class _Run:
    """一次完整處理。把登記的步驟依比重切成一段一段,記住整條走到哪。"""

    def __init__(self, stages: list[str]):
        weights = [_STAGE_WEIGHTS.get(s, _DEFAULT_WEIGHT) for s in stages]
        total = sum(weights) or 1.0
        self.slices: dict[str, tuple[float, float]] = {}   # 步驟 -> (起點, 寬度)
        acc = 0.0
        for stage, w in zip(stages, weights):
            self.slices[stage] = (acc / total, w / total)
            acc += w
        self.start = time.time()
        self.max_pct = 0        # 整條只增不減:擋住任何往回掉的情況


# 目前登記中的執行。None 代表沒登記(直接跑命令列、單元測試)——
# 這時 Reporter 退回原本「每步各自 0→100%」的行為,不影響那些用法。
_active_run: "_Run | None" = None


def begin_run(stages: list[str]) -> None:
    """宣告這次會依序跑哪些會顯示進度的步驟(名稱要跟 Reporter 的 stage 一致)。

    只登記「真的會跑」的步驟 —— 跳過的(例如沒要掃畫面)不要放進來,
    不然它那一段會一直空著,輪到下一步時進度條會突然往前跳一大格。"""
    global _active_run
    _active_run = _Run(list(stages))


def finish_run() -> None:
    """收尾:補一個整條 100%,面板才知道全部做完了。

    有些路徑(例如沿用上次混好的影片)最後一個登記步驟不會真的跑,
    進度條會停在半路;這裡在管線成功結束時補上 100%,把它填滿。"""
    global _active_run
    run = _active_run
    _active_run = None
    if run is not None and run.max_pct < 100:
        print(f"  {PREFIX} 完成 100%", flush=True)


class Reporter:
    """一個步驟的進度回報器。

    用法:
        p = Reporter("語音轉錄", total=1620.0, unit="分", scale=1/60)
        p.update(done_seconds)
        p.done()

    若外面已經用 begin_run() 登記了整條執行、而且這個 stage 在登記清單裡,
    回報的就是「整條走到哪」+「剩約 X 分」;否則退回原本的每步百分比。
    """

    def __init__(self, stage: str, total: float,
                 unit: str = "", scale: float = 1.0):
        self.stage = stage
        self.total = float(total) if total and total > 0 else 0.0
        self.unit = unit
        self.scale = scale
        self._last_pct = -1
        self._last_at = 0.0
        self._finished = False
        # 開跑當下綁定這次執行與自己那一段(begin_run 一定在 Reporter 之前呼叫)
        self._run = _active_run
        self._slice = self._run.slices.get(stage) if self._run else None

    def update(self, done: float) -> None:
        if self._finished or self.total <= 0:
            return
        pct = int(max(0.0, min(1.0, done / self.total)) * 100)
        now = time.time()
        if pct == self._last_pct or (now - self._last_at) < _MIN_INTERVAL_SEC:
            return
        self._last_pct, self._last_at = pct, now
        self._emit(pct, done)

    def done(self) -> None:
        """收尾:補一個 100%,面板才知道這個步驟結束了、可以收掉進度條。"""
        if self._finished:
            return
        self._finished = True
        # 已經印過 100% 就不要再印一次(短檔案很容易一開始就衝到 100)
        if self.total > 0 and self._last_pct != 100:
            self._emit(100, self.total)

    def _emit(self, pct: int, done: float) -> None:
        if self._slice is not None:
            # 有登記整條執行:回報「整條走到哪」+「剩約 X 分」。
            start, width = self._slice
            frac = 0.0 if self.total <= 0 else max(0.0, min(1.0, done / self.total))
            gpct = int(round((start + frac * width) * 100))
            # 單調:整條只增不減。混音「無損複製」失敗改重編碼時,同一段會
            # 從頭再來一次,這裡擋住它把進度條往回拉。
            gpct = max(gpct, self._run.max_pct)
            self._run.max_pct = gpct
            eta = self._eta_text()
            tail = (" " + eta) if eta else ""
            print(f"  {PREFIX} {self.stage} {gpct}%{tail}", flush=True)
            return
        # 沒登記整條執行:維持原本的每步百分比(直接跑命令列、單元測試用)。
        tail = ""
        if self.unit:
            tail = (f" {done * self.scale:.1f}/"
                    f"{self.total * self.scale:.1f} {self.unit}")
        print(f"  {PREFIX} {self.stage} {pct}%{tail}", flush=True)

    def _eta_text(self) -> str:
        """用「已經花掉的時間」對照「整條走了幾成」回推還要多久。

        太早(才剛開始、成數還很低)估出來的數字會亂跳得離譜,乾脆先不報 ——
        寧可晚一點才出現「剩約」,也不要一開始就給一個差很多的數字。"""
        run = self._run
        frac = run.max_pct / 100.0
        elapsed = time.time() - run.start
        if frac < 0.03 or elapsed < 3.0:
            return ""
        remain = elapsed * (1.0 - frac) / frac
        if remain < 1.0:
            return ""
        if remain >= 60.0:
            return f"剩約 {remain / 60.0:.1f} 分"
        return f"剩約 {int(round(remain))} 秒"


def run_ffmpeg(cmd: list[str], stage: str, total_sec: float,
               check: bool = True):
    """跑一個 ffmpeg 指令,順便回報進度。

    做法:加上 `-progress pipe:1 -nostats`,ffmpeg 會把
    `out_time_us=...` 之類的鍵值一行一行吐到 stdout,照著換算成百分比。

    ⚠️ 只能用在「輸出寫到檔案」的 ffmpeg 指令 —— stdout 被進度佔用了。
    需要從 stdout 收資料的(例如抽縮圖那支),請自己數收到多少來回報。

    回傳 subprocess.CompletedProcess 風格的物件(returncode / stderr)。
    """
    import subprocess
    import threading

    full = list(cmd)
    # -progress 要放在輸出檔之前才吃得到;插在 "ffmpeg" 後面最保險
    full[1:1] = ["-progress", "pipe:1", "-nostats"]

    rep = Reporter(stage, total_sec, unit="分", scale=1 / 60)
    proc = subprocess.Popen(full, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")

    # stderr 要「同時」讀走,不能等 stdout 讀完再讀。
    # 管線的緩衝區只有幾十 KB,塞滿之後 ffmpeg 就會卡在寫 stderr 上不動,
    # 而我們還在等 stdout —— 兩邊互等,整條管線就這樣停住,
    # 而且畫面上看起來只是「跑很久」,查起來非常痛苦。
    _err: list[str] = []

    def _drain():
        try:
            _err.append(proc.stderr.read())
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain, daemon=True)
    err_thread.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                raw = line.split("=", 1)[1]
                try:
                    # out_time_us 是微秒;有些版本會吐 N/A
                    val = int(raw)
                except ValueError:
                    continue
                secs = val / 1_000_000 if line.startswith("out_time_us=") \
                    else val / 1000
                rep.update(secs)
    finally:
        proc.stdout.close()
        proc.wait()
        err_thread.join(timeout=5)
        try:
            proc.stderr.close()
        except Exception:
            pass
        stderr = "".join(_err)
    rep.done()

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, full, output=None, stderr=stderr)

    class _Result:
        returncode = proc.returncode
        stderr = None

    r = _Result()
    r.stderr = stderr
    return r
