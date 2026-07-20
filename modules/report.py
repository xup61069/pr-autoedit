"""
HTML 審閱報告 —— 打開 PR 之前先花一分鐘掃這個。
每個切點顯示前後文、刪除長度、信心值,發現大面積誤判就直接調參數重跑,
不用浪費一次 PR 審閱。
"""

from __future__ import annotations
from core.models import Word, Timeline
from core.remap import RemapTable
import config.settings as cfg
import html


def _fmt_value(v) -> str:
    """把設定值印成人看得懂的樣子(勾選項目印開/關、清單用頓號串起來)"""
    if isinstance(v, bool):
        return "開" if v else "關"
    if isinstance(v, (list, tuple)):
        if not v:
            return "(空)"
        # VST 路徑很長,只留檔名就夠認人了
        parts = [str(x).replace("\\", "/").split("/")[-1] if "\\" in str(x)
                 or "/" in str(x) else str(x) for x in v]
        s = "、".join(parts)
        return s if len(s) <= 60 else s[:60] + "…"
    return str(v)


def _settings_summary() -> str:
    """本次設定摘要:只列「跟內建預設不一樣」的項目。

    為什麼要有:你調了幾個旋鈕、產出一條序列,過兩天覺得不對想調回去,
    以前沒有任何地方查得到「那條序列當時用的是什麼數字」。
    現在每份報告都自己帶著,對照兩份報告就知道差在哪。"""
    changed = cfg.changed_settings()
    if not changed:
        return ('<div class="summary">本次全部使用內建預設值,沒有任何自訂設定。</div>')

    try:
        from ui_settings import FIELDS
        labels = {f["key"]: f["label"] for f in FIELDS}
    except Exception:
        labels = {}

    rows = "".join(
        f"<tr><td>{html.escape(labels.get(k, k))}</td>"
        f"<td><b>{html.escape(_fmt_value(getattr(cfg, k, None)))}</b></td>"
        f"<td>{html.escape(_fmt_value(cfg.DEFAULTS.get(k)))}</td></tr>"
        for k in changed)
    return f"""
<h2 style="font-size:1.1rem;font-weight:500">本次設定摘要</h2>
<div class="summary">
  下面 {len(changed)} 項跟預設值不同,其餘都是預設。
  這份清單就是這條序列的「配方」——之後覺得剪得不對想調回來,
  拿兩份報告對照就知道差在哪。面板的數字欄位<b>點兩下可恢復預設</b>。
</div>
<table>
  <tr><th>設定項目</th><th>這次用的值</th><th>預設值</th></tr>
  {rows}
</table>"""


def generate(timeline: Timeline, words: list[Word],
            table: RemapTable, out_html: str, live: bool = False) -> str:
    """live=True(活專案模式):時間軸=原始影片,所有時間碼直接用原始位置,
    切點是「建議」而非已執行;文案跟著調整。"""
    cuts = table.cuts_for_markers()      # 全部切點,報告裡都列出來
    fps = timeline.fps

    # 建一個「原始幀 -> 詞索引」的查找,用來抓前後文
    word_starts = [w.start_frame(fps) for w in words]

    def context_around(orig_frame: int, span: int = 3) -> str:
        # 找最接近的詞索引
        idx = min(range(len(words)),
                key=lambda i: abs(word_starts[i] - orig_frame)) if words else 0
        lo = max(0, idx - span)
        hi = min(len(words), idx + span + 1)
        parts = []
        for i in range(lo, hi):
            t = html.escape(words[i].text)
            if i == idx:
                parts.append(f'<mark>{t}</mark>')
            else:
                parts.append(t)
        return "".join(parts)

    def tc(frame: int) -> str:
        """幀 -> 分:秒(給人看的時間碼)"""
        s = max(0, frame) / fps
        return f"{int(s // 60):02d}:{int(s % 60):02d}"

    rows = []
    for c in cuts:
        color = "#c0392b" if c.confidence < cfg.MARKER_MAX_CONFIDENCE else "#7f8c8d"
        badge = "需審閱" if c.confidence < cfg.MARKER_MAX_CONFIDENCE else "自動"
        rows.append(f"""
        <tr>
          <td class="tc">{tc(c.orig_frame if live else c.timeline_frame)}</td>
          <td>{c.reason}</td>
          <td style="color:{color};font-weight:500">{badge}</td>
          <td>{html.escape(c.text)}</td>
          <td>{c.duration_ms} ms</td>
          <td>{c.confidence:.2f}</td>
          <td class="ctx">…{context_around(c.orig_frame)}…</td>
        </tr>""")

    n_review = sum(1 for c in cuts if c.confidence < cfg.MARKER_MAX_CONFIDENCE)

    # 省時摘要:原始長度 vs 剪輯後長度
    orig_frames = max((s.end for s in timeline.segments), default=0)
    edited_frames = table.total_frames
    saved_frames = max(0, orig_frames - edited_frames)
    saved_pct = (saved_frames / orig_frames * 100) if orig_frames else 0
    n_del = sum(1 for s in timeline.segments if s.action == "delete")
    n_spd = sum(1 for s in timeline.segments if s.action == "speed")

    # 音樂/音效段(受保護,不剪不快轉)。時間碼是「原始影片」的位置,
    # 方便對照來源影片確認偵測是否正確。
    music_segs = [s for s in timeline.segments if s.reason == "music"]
    music_frames = sum(s.duration for s in music_segs)
    music_rows = "".join(
        f"<tr><td class='tc'>{tc(s.start)} ~ {tc(s.end)}</td>"
        f"<td>{s.duration / fps:.1f} 秒</td></tr>"
        for s in music_segs)
    music_html = f"""
<h2 style="font-size:1.1rem;font-weight:500">音樂/音效段(已保護,不剪不快轉)</h2>
<div class="summary">
  下面 {len(music_segs)} 段沒有講話、但有聲音(音樂/音效/示範播放),
  已自動保護、原封不動保留。時間碼是<b>原始影片</b>的位置,
  可以對照原片確認有沒有抓錯:漏抓(音樂被快轉)就把設定裡的
  「音樂偵測靈敏度」調小;誤抓(呼吸聲被當音樂)就調大。
</div>
<table>
  <tr><th>原始影片位置</th><th>長度</th></tr>
  {music_rows}
</table>""" if music_segs else ""

    doc = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>剪輯審閱報告</title>
<style>
  body {{ font-family: system-ui, "Microsoft JhengHei", sans-serif;
        max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #2c2c2a; }}
  h1 {{ font-size: 1.4rem; font-weight: 500; }}
  .summary {{ background: #f1efe8; padding: 1rem; border-radius: 8px;
            margin: 1rem 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #ddd; }}
  th {{ background: #f7f6f2; font-weight: 500; }}
  .ctx {{ color: #555; }}
  .tc {{ font-variant-numeric: tabular-nums; color: #2d6cdf; font-weight: 500; }}
  mark {{ background: #ffe08a; padding: 0 2px; }}
  .stats {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 0.5rem 0 1rem; }}
  .stat {{ background: #f1efe8; padding: 0.7rem 1.1rem; border-radius: 8px; }}
  .stat .num {{ font-size: 1.4rem; font-weight: 600; }}
  .stat .lbl {{ font-size: 12px; color: #666; }}
  .stat.hi .num {{ color: #2e8b57; }}
</style></head><body>
<h1>剪輯審閱報告</h1>
<div class="stats">
  <div class="stat hi"><div class="num">{tc(saved_frames)}</div><div class="lbl">{"套用建議後預計省下" if live else "省下的時間"}({saved_pct:.0f}%)</div></div>
  <div class="stat"><div class="num">{tc(orig_frames)} → {tc(edited_frames)}</div><div class="lbl">{"原長 → 套用建議後" if live else "原長 → 剪後"}</div></div>
  <div class="stat"><div class="num">{n_del} / {n_spd}</div><div class="lbl">刪除段 / 快轉段</div></div>
  <div class="stat"><div class="num">{len(music_segs)}</div><div class="lbl">音樂/音效段(共 {tc(music_frames)},已保護)</div></div>
  <div class="stat"><div class="num">{n_review}</div><div class="lbl">需人工審閱的切點</div></div>
</div>
<div class="summary">
  {"<b>活專案模式</b>:下方切點都只是「建議」,影片一刀未剪,全部片段都在時間軸上(粉紅=靜音、青綠=音樂、紫=冗詞)。<br>「時間」欄就是時間軸位置(=原始影片位置)。在 Premiere 時間軸右鍵「標籤 &gt; 選取標籤群組」可一次選同色片段批次刪除或改速度。<br>" if live else f"下方 {len(cuts)} 個切點中,<b>{n_review}</b> 個標為「需審閱」(低信心,已在專案下 marker)。<br>在 Premiere 用 Shift+M / Ctrl+Shift+M 逐點跳,只需確認「需審閱」的切點;「時間」欄是剪輯後影片的位置。<br>"}
  若這裡看到大量誤判,先調設定的門檻再重跑,不用急著進 Premiere。
</div>
<table>
  <tr><th>時間</th><th>類型</th><th>狀態</th><th>詞</th><th>長度</th><th>信心</th>
      <th>前後文</th></tr>
  {''.join(rows)}
</table>
{music_html}
{_settings_summary()}
</body></html>"""

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"  審閱報告:{len(cuts)} 個切點 -> {out_html}")
    return out_html
