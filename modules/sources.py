"""輸入來源 —— 一支影片,或「好幾支要接成一支」的影片。

為什麼需要這一層:錄影軟體(bandicam 這類)錄長片會自動切檔,一堂課常常是
`教學_0718.mp4`、`教學_0718_0001.mp4`、`教學_0718_0002.mp4` 三四個檔。
以前只能一支一支跑,拿到三條各自獨立的序列,還要自己在 Premiere 裡接起來
——而字幕、marker、報告也都是各算各的,接完全部要重對。

現在可以一次選起來,整批當成「一支影片」處理:一條序列、一份字幕、
一份報告,時間軸從第一個檔的開頭連到最後一個檔的結尾。

## 怎麼接的

**不產生合併後的大檔**。用 ffmpeg 的 concat demuxer 當「輸入」,
抽音軌、掃畫面、混音三個步驟都直接吃那份清單。
理由很實際:4K 長片一份就十幾 GB,先合併成一個實體檔等於要多一份
——而且那份合併檔用完就沒用了,純粹是浪費硬碟跟時間。
最後寫出來的 `01_clean_av.mp4` 本來就是完整的一支,序列引用它就好。

## 為什麼要擋住「規格不一樣」的檔

concat demuxer 是「串流層級」的接合,不重新編碼(所以快)。代價是它要求
每個檔的編碼參數一致——解析度、幀率、編碼器都要一樣。不一樣的話它不會
報錯,而是產生一個「前半段正常、後半段畫面錯亂或整個解不出來」的檔,
而且要等到很後面才發現。所以寧可在最前面就擋下來,把哪裡不一樣講清楚。
"""

from __future__ import annotations
import os
import re
import subprocess


def natural_key(path: str):
    """檔名的「自然排序」鍵:讓 _2 排在 _10 前面。

    錄影軟體切出來的檔是 _0001、_0002 這種固定位數,純字串排序本來就對;
    但使用者自己命名的 part2 / part10 用字串排會變成 10 在 2 前面。
    數字的部分照數值比,其餘照字串比。"""
    name = os.path.basename(path).lower()
    return [int(t) if t.isdigit() else t
            for t in re.split(r"(\d+)", name)]


def _probe(path: str) -> dict:
    """讀出一個檔的關鍵規格(判斷能不能接在一起)"""
    out = subprocess.run([
        "ffprobe", "-v", "0", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-of", "default=noprint_wrappers=1:nokey=0", path,
    ], capture_output=True, text=True, check=True).stdout
    info = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    dur = subprocess.run([
        "ffprobe", "-v", "0", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ], capture_output=True, text=True, check=True).stdout.strip()
    try:
        info["duration"] = float(dur)
    except ValueError:
        info["duration"] = 0.0
    return info


def _fps_of(info: dict) -> float:
    r = info.get("r_frame_rate", "0/1")
    try:
        num, den = r.split("/")
        return round(float(num) / float(den), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


class VideoSource:
    """一個或多個影片檔,對外表現得像「一支影片」。

    用 input_args() 拿到餵給 ffmpeg 的輸入參數 —— 單檔是 ["-i", 路徑],
    多檔是 concat 清單。呼叫端不必分辨是哪一種。"""

    def __init__(self, paths: list[str], list_dir: str | None = None):
        if not paths:
            raise ValueError("沒有給任何影片檔")
        self.paths = list(paths)
        self._list_dir = list_dir
        self._list_file: str | None = None
        self._infos: list[dict] | None = None

    # ---------------- 基本資訊 ----------------
    @property
    def multi(self) -> bool:
        return len(self.paths) > 1

    @property
    def first(self) -> str:
        """拿來探測規格的代表檔(規格已經驗過全部一致)"""
        return self.paths[0]

    @property
    def name(self) -> str:
        """output/ 底下的資料夾名稱。

        多檔時在第一個檔名後面加「_合併N支」:一眼看得出這個資料夾是
        合併來的,也不會跟「單獨跑第一個檔」的結果互相蓋掉。"""
        stem = os.path.splitext(os.path.basename(self.paths[0]))[0]
        return stem if not self.multi else f"{stem}_合併{len(self.paths)}支"

    def infos(self) -> list[dict]:
        if self._infos is None:
            self._infos = [_probe(p) for p in self.paths]
        return self._infos

    def fps(self) -> float:
        return _fps_of(self.infos()[0])

    def dimensions(self) -> tuple[int, int]:
        i = self.infos()[0]
        return int(i["width"]), int(i["height"])

    def duration(self) -> float:
        """接起來之後總共多長(秒)。concat 出來的長度就是各段相加。"""
        return sum(i.get("duration", 0.0) for i in self.infos())

    def total_frames(self, fps: float) -> int:
        return int(self.duration() * fps)

    def fingerprint(self) -> str:
        """「這是不是同一批影片」。給畫面變化量的快取判斷用。

        任何一個檔換了內容、或多選/少選了一個檔,指紋都要跟著變 ——
        否則快取會被誤用,畫面判定是照舊那批算的,而且完全沒有徵兆。"""
        parts = []
        for p in self.paths:
            try:
                st = os.stat(p)
                parts.append(f"{os.path.basename(p)}:{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                parts.append(f"{os.path.basename(p)}:?")
        return "|".join(parts)

    # ---------------- 餵給 ffmpeg ----------------
    def input_args(self) -> list[str]:
        """ffmpeg 的輸入參數。單檔直接 -i;多檔用 concat 清單。

        -safe 0 是因為清單裡放的是絕對路徑,預設的安全檢查會擋掉。"""
        if not self.multi:
            return ["-i", self.paths[0]]
        return ["-f", "concat", "-safe", "0", "-i", self._ensure_list_file()]

    def set_list_dir(self, path: str) -> None:
        """concat 清單要寫到哪。工作區建好之後才知道,所以分開設。"""
        self._list_dir = path
        self._list_file = None

    def _ensure_list_file(self) -> str:
        if self._list_file and os.path.exists(self._list_file):
            return self._list_file
        base = self._list_dir or os.path.dirname(os.path.abspath(self.paths[0]))
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "00_concat.txt")
        with open(path, "w", encoding="utf-8") as f:
            for p in self.paths:
                # concat demuxer 的路徑用單引號包起來;路徑裡真的有單引號時
                # 要寫成 '\'' (關引號、跳脫的引號、再開引號)。
                # 反斜線在這裡是字面值,但統一換成斜線最不會出事。
                safe = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe}'\n")
        self._list_file = path
        return path

    # ---------------- 相容性檢查 ----------------
    def incompatibility(self) -> str | None:
        """這幾個檔能不能直接接在一起?不能的話回傳「哪裡不一樣」的說明。

        concat demuxer 不重新編碼,所以要求編碼參數一致。不一致時它不會
        報錯,而是產生前半段正常、後半段錯亂的檔——要等到剪完進 Premiere
        才發現,那時候已經浪費很多時間了。所以在最前面就擋。"""
        if not self.multi:
            return None
        infos = self.infos()
        ref, ref_path = infos[0], self.paths[0]

        def spec(i):
            return (i.get("width"), i.get("height"),
                    i.get("r_frame_rate"), i.get("codec_name"))

        def human(i):
            return (f"{i.get('width')}x{i.get('height')}、"
                    f"{_fps_of(i):g}fps、{i.get('codec_name')}")

        for path, info in zip(self.paths[1:], infos[1:]):
            if spec(info) != spec(ref):
                return (
                    "這幾個檔的規格不一樣,沒辦法直接接在一起:\n"
                    f"    {os.path.basename(ref_path)}:{human(ref)}\n"
                    f"    {os.path.basename(path)}:{human(info)}\n"
                    "  接合是「不重新編碼」的(所以很快),前提是每個檔的\n"
                    "  解析度、幀率、編碼格式都相同。\n"
                    "  同一次錄影切出來的檔一定相同;不同次錄的就不一定。\n"
                    "  解法:先用同樣的設定重新輸出成一致的規格,或分開處理。")
        return None

    def describe(self) -> str:
        """給人看的一行說明"""
        if not self.multi:
            return os.path.basename(self.paths[0])
        names = "、".join(os.path.basename(p) for p in self.paths)
        mins = self.duration() / 60
        return f"{len(self.paths)} 個檔接成一支({mins:.1f} 分):{names}"


def from_args(paths: list[str], list_dir: str | None = None,
              sort: bool = True) -> VideoSource:
    """從命令列參數建立來源。

    sort=True 會照檔名自然排序 —— 錄影軟體切出來的 _0001/_0002 這樣排是對的。
    面板會先把順序排好再傳過來,而且順序在面板上看得到、可以自己調,
    所以這裡再排一次只是保險(命令列直接跑時萬用字元展開的順序不一定準)。"""
    ordered = sorted(paths, key=natural_key) if sort else list(paths)
    return VideoSource(ordered, list_dir=list_dir)
