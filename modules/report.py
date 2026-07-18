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


def generate(timeline: Timeline, words: list[Word],
            table: RemapTable, out_html: str) -> str:
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

    rows = []
    for c in cuts:
        color = "#c0392b" if c.confidence < cfg.MARKER_MAX_CONFIDENCE else "#7f8c8d"
        badge = "需審閱" if c.confidence < cfg.MARKER_MAX_CONFIDENCE else "自動"
        rows.append(f"""
        <tr>
          <td>{c.reason}</td>
          <td style="color:{color};font-weight:500">{badge}</td>
          <td>{html.escape(c.text)}</td>
          <td>{c.duration_ms} ms</td>
          <td>{c.confidence:.2f}</td>
          <td class="ctx">…{context_around(c.orig_frame)}…</td>
          <td>{c.timeline_frame}</td>
        </tr>""")

    total_deleted_ms = sum(c.duration_ms for c in cuts
                        if timeline.segments)  # 概略
    n_review = sum(1 for c in cuts if c.confidence < cfg.MARKER_MAX_CONFIDENCE)

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
  mark {{ background: #ffe08a; padding: 0 2px; }}
</style></head><body>
<h1>剪輯審閱報告</h1>
<div class="summary">
  共 {len(cuts)} 個切點,其中 <b>{n_review}</b> 個標為「需審閱」(低信心,已在 PR 下 marker)。<br>
  在 Premiere 用 Shift+M / Ctrl+Shift+M 逐點跳,只需確認「需審閱」的切點。<br>
  若這裡看到大量誤判,先調 config/settings.py 的門檻再重跑,不要進 PR。
</div>
<table>
  <tr><th>類型</th><th>狀態</th><th>詞</th><th>長度</th><th>信心</th>
      <th>前後文</th><th>時間軸幀</th></tr>
  {''.join(rows)}
</table>
</body></html>"""

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"  審閱報告:{len(cuts)} 個切點 -> {out_html}")
    return out_html
