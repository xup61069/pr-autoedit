"""進度回報 —— 讓「沒動靜的那幾分鐘」看得出來還活著。

問題:整條管線最花時間的幾步(語音轉錄、掃畫面、混音)在畫面上都是
一行字然後一片安靜。34 分鐘的 4K 片,語音轉錄可能要跑好幾分鐘,
中間完全沒有輸出 —— 使用者分不出「還在跑」跟「當掉了」,
只好一直等、或者按停止重來。

做法:這些步驟固定印出一種「機器看得懂、人也看得懂」的進度行:

    [進度] 語音轉錄 45% 12.3/27.0 分

面板認得 `[進度]` 開頭的行,把它畫成進度條而不是一直往下堆訊息
(見 premiere-panel/js/main.js 的 appendLog)。直接在命令列跑的人
看到的就是一行一行的百分比,一樣讀得懂。

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


class Reporter:
    """一個步驟的進度回報器。

    用法:
        p = Reporter("語音轉錄", total=1620.0, unit="分", scale=1/60)
        p.update(done_seconds)
        p.done()
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
        tail = ""
        if self.unit:
            tail = (f" {done * self.scale:.1f}/"
                    f"{self.total * self.scale:.1f} {self.unit}")
        print(f"  {PREFIX} {self.stage} {pct}%{tail}", flush=True)


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
