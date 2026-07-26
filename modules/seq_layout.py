"""Premiere 序列版面(layout JSON)的共用解讀 —— 只有一份實作。

有兩個功能都要讀這份 JSON:
  - `live_subs`:拿當初的轉錄快取,依序列版面重新對位(快,幾秒完成)
  - `seq_asr`  :對目前序列的口白重新辨識(慢,但任何序列都能用)

它們對「哪個片段的來源時間點可不可信」必須做出**完全一樣**的判斷。
各寫一套的下場是:同一條序列,兩個功能會採用不同的片段,
產出兩份對不起來的字幕,而且沒有任何錯誤訊息。
"""

from __future__ import annotations


def speed_is_trustworthy(src_in: float, src_out: float,
                         tl_start: float, tl_end: float,
                         speed: float) -> bool:
    """這個片段回報的「來源入出點」能不能拿來對位?

    ⚠️ Premiere 對「變速片段」回報的來源入出點是不能信的。實測一條
    923 個片段的序列:speed=1 的片段來源範圍完全正確,但 133 個變速片段
    全部回報成錯的 —— 例如某段真正的來源是 10.267~10.767 秒,
    它卻回報 0.850~0.900(跑到影片最前面去了),而且 out-in 給的是
    「時間軸長度」不是來源長度。

    判斷方式不是「看到變速就丟」,而是問資料自己:
    來源長度到底比較像「時間軸長度 × 倍率」(正確),還是比較像
    「時間軸長度」本身(= Premiere 根本沒換算)?哪個比較接近就是哪個。

    這樣寫沒有魔術容差,而且哪天 Premiere 修好了會自動恢復採用。
    (一開始用「差多少算不一致」的容差判斷,結果很短的片段判不出來:
     1 幀的片段容差 0.204 秒比整段預期長度 0.2 秒還大,怎麼調都會漏。)
    """
    if speed == 1.0:
        return True
    got = src_out - src_in
    tl_len = tl_end - tl_start
    err_scaled = abs(got - tl_len * speed)     # 有換算(正確)
    err_unscaled = abs(got - tl_len)           # 沒換算(Premiere 的毛病)
    return not (err_unscaled < err_scaled)


def load_clips(layout_json: str) -> tuple[list[dict], dict]:
    """讀 layout JSON,回傳(依時間軸排序的片段清單, 整份 layout)。

    片段一律補齊 start/end/in/out/speed/path 這幾個鍵,呼叫端不必再防呆。
    """
    import json
    with open(layout_json, "r", encoding="utf-8") as f:
        layout = json.load(f)
    raw = layout.get("clips", [])
    clips = []
    for c in raw:
        start = float(c.get("start", 0.0))
        clips.append({
            "start": start,
            "end": float(c.get("end", start)),
            "in": float(c.get("in", 0.0)),
            "out": float(c.get("out", 0.0)),
            "speed": abs(float(c.get("speed") or 1.0)) or 1.0,
            # 來源檔路徑:舊版的 layout 沒有這個欄位(只有 live_subs 在用,
            # 它不需要檔案)。seq_asr 需要,拿不到就會自己說明。
            "path": c.get("path") or "",
        })
    clips.sort(key=lambda c: c["start"])
    return clips, layout
