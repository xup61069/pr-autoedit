"""
決策引擎 —— 系統的大腦。
輸入:Whisper 的詞級轉錄結果。
輸出:剪輯段落清單(哪些保留、哪些刪除、哪些快轉)。

這是唯一需要針對你個人說話習慣調校的模組,
所有門檻和詞表都在 config/settings.py,這裡只放邏輯。
"""

from __future__ import annotations
from core.models import Word, Segment
import config.settings as cfg


def _is_isolated_or_repeated(words: list[Word], i: int) -> bool:
    """判斷 words[i] 這個有條件冗詞是否該刪:
    句首孤立出現,或與前後詞重複。"""
    w = words[i].text
    # 連續重複(對對對、然後然後)
    if i > 0 and words[i - 1].text == w:
        return True
    if i + 1 < len(words) and words[i + 1].text == w:
        return True
    # 句首孤立:前一個詞距離較遠(像是換句),且本身是連接詞
    if i == 0:
        return True
    gap = words[i].start - words[i - 1].end
    if gap > getattr(cfg, "FILLER_ISOLATED_GAP_SEC", 0.25):
        return True                  # 前面有停頓,像是新句子的開頭語助
    return False


def _standalone_utterance(words: list[Word], i: int,
                        gap: float | None = None) -> bool:
    """words[i] 是不是「獨立發出」的(前後都有停頓)。

    為什麼需要:FunASR 這類逐字引擎會把「好啊」拆成「好」「啊」兩個字;
    黏在句尾的「啊」(前面沒停頓)是句子的一部分,照樣無條件刪除
    會把正常的話剪壞、把字幕剁碎。

    門檻由 cfg.FILLER_PAUSE_SEC 控制,設 0 代表不要求(Whisper 建議值:
    它會把詞排得很密,要求停頓幾乎等於永遠不刪語氣詞)。"""
    if gap is None:
        gap = getattr(cfg, "FILLER_PAUSE_SEC", 0.0)
    if gap <= 0:
        return True                  # 不要求停頓:看到語氣詞就刪
    before = words[i].start - words[i - 1].end if i > 0 else 99.0
    after = (words[i + 1].start - words[i].end
             if i + 1 < len(words) else 99.0)
    return before >= gap and after >= gap


def _split_gap(start_f: int, end_f: int,
               audible: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """把一段沒有詞的空隙,依「有聲區間」切成小段。
    回傳 [(起, 迄, 種類), ...],種類是 "silence" 或 "music",完整覆蓋整個空隙。
    audible 需已排序、彼此不重疊(audio_probe 的輸出天然如此)。"""
    pieces: list[tuple[int, int, str]] = []
    cursor = start_f
    for a, b in audible:
        a, b = max(a, start_f), min(b, end_f)
        if b <= a or b <= cursor:
            continue
        if a > cursor:
            pieces.append((cursor, a, "silence"))
        pieces.append((max(a, cursor), b, "music"))
        cursor = b
    if cursor < end_f:
        pieces.append((cursor, end_f, "silence"))
    return pieces


def build_segments(words: list[Word], fps: float, total_frames: int,
                audible: list[tuple[int, int]] | None = None) -> list[Segment]:
    """
    主流程:掃過所有詞,產生連續的 Segment 清單。
    保證輸出的段落首尾相連、覆蓋整支影片(0 到 total_frames)。

    audible:音訊能量偵測出的「有聲區間」(幀),見 modules/audio_probe。
    詞與詞之間的空隙若跟有聲區間重疊,重疊部分視為音樂/音效段,
    標成 keep + reason="music" 保護起來(不刪、不快轉)。
    """
    segments: list[Segment] = []
    cursor = 0                       # 目前處理到的原始幀位置

    def emit_keep(start_f: int, end_f: int):
        if end_f > start_f:
            segments.append(Segment(start_f, end_f, "keep"))

    def emit_silence_piece(start_f: int, end_f: int):
        if cfg.SILENCE_ACTION == "delete":
            segments.append(Segment(start_f, end_f, "delete",
                                    reason="silence", confidence=0.95))
        else:
            segments.append(Segment(start_f, end_f, "speed",
                                    factor=cfg.SILENCE_SPEED_FACTOR,
                                    reason="silence", confidence=0.95))

    def emit_silence(start_f: int, end_f: int):
        if end_f <= start_f:
            return
        pieces = _split_gap(start_f, end_f, audible or [])
        if len(pieces) == 1 and pieces[0][2] == "silence":
            emit_silence_piece(start_f, end_f)   # 沒有音樂,維持原本行為
            return
        min_silence = round(cfg.SILENCE_THRESHOLD_SEC * fps)
        for a, b, kind in pieces:
            if kind == "music":
                # 音樂/音效段:保護起來。信心 0.8 = 報告會提醒使用者確認
                segments.append(Segment(a, b, "keep", reason="music",
                                        confidence=0.8))
            elif b - a >= min_silence:
                emit_silence_piece(a, b)
            else:
                emit_keep(a, b)      # 音樂前後的短空隙,不值得剪,保留

    pad = round(cfg.SILENCE_PADDING_SEC * fps)
    silence_gap = cfg.SILENCE_THRESHOLD_SEC

    for i, w in enumerate(words):
        ws = w.start_frame(fps)
        we = w.end_frame(fps)

        # --- 1. 處理這個詞之前的空隙(可能是靜音)---
        if ws > cursor:
            gap_sec = (ws - cursor) / fps
            if gap_sec >= silence_gap:
                # 空隙夠長 -> 靜音處理,但前後留 padding
                emit_keep(cursor, min(cursor + pad, ws))
                emit_silence(min(cursor + pad, ws), max(ws - pad, cursor + pad))
                emit_keep(max(ws - pad, cursor + pad), ws)
            else:
                emit_keep(cursor, ws)      # 短空隙,正常保留
        cursor = max(cursor, ws)

        # --- 2. 判斷這個詞本身是不是冗詞 ---
        text = w.text.strip()
        if text in cfg.FILLERS_ALWAYS and _standalone_utterance(words, i):
            segments.append(Segment(ws, we, "delete", reason="filler",
                                    text=text, confidence=1.0))
        elif text in cfg.FILLERS_CONDITIONAL and _is_isolated_or_repeated(words, i):
            segments.append(Segment(ws, we, "delete", reason="filler",
                                    text=text,
                                    confidence=cfg.CONDITIONAL_CONFIDENCE))
        else:
            emit_keep(ws, we)              # 正常的詞,保留
        cursor = max(cursor, we)

    # --- 3. 收尾:最後一個詞到影片結尾 ---
    if cursor < total_frames:
        gap_sec = (total_frames - cursor) / fps
        if gap_sec >= silence_gap:
            emit_silence(cursor, total_frames)
        else:
            emit_keep(cursor, total_frames)

    return _merge_adjacent(segments)


def _cut_spans(segments: list[Segment],
            spans: list[tuple[int, int, str]],
            reason: str, confidence: float) -> list[Segment]:
    """把一批「原始幀區間」從保留段裡挖掉,改成刪除段。

    共用給能量微剪(挖安靜)與重講偵測(挖說錯的那次)使用。
    只動 action="keep" 的段;音樂/音效段(reason="music")絕不碰。
    spans 需已排序、彼此不重疊。輸出仍然首尾相連、覆蓋範圍不變。"""
    if not spans:
        return segments

    out: list[Segment] = []
    for s in segments:
        if s.action != "keep" or s.reason == "music":
            out.append(s)
            continue
        cursor = s.start
        for a, b, text in spans:
            if b <= cursor:
                continue
            if a >= s.end:
                break
            a, b = max(a, cursor), min(b, s.end)
            if b <= a:
                continue
            if a > cursor:
                out.append(Segment(cursor, a, "keep", reason=s.reason,
                                text=s.text, confidence=s.confidence))
            out.append(Segment(a, b, "delete", reason=reason,
                            text=text, confidence=confidence))
            cursor = b
        if cursor < s.end:
            out.append(Segment(cursor, s.end, "keep", reason=s.reason,
                            text=s.text, confidence=s.confidence))

    return _merge_adjacent(out)


def protect_words(quiet: list[tuple[int, int]], words: list[Word],
                fps: float) -> list[tuple[int, int]]:
    """丟掉那些「會把一整個詞吃掉」的安靜區。

    為什麼:輕聲的短字(你、它、的…)音量本來就低,可能整個掉在門檻以下。
    連整個詞一起剪掉的話,聲音會少一個字、字幕也跟著缺字,聽起來像跳針。
    辨識引擎既然在那裡認出一個詞,就當作那裡有話,寧可少剪一點。"""
    if not quiet or not words:
        return quiet
    spans = [(w.start_frame(fps), w.end_frame(fps)) for w in words]
    out = []
    for a, b in quiet:
        if any(a <= ws and we <= b for ws, we in spans if ws < b and we > a):
            continue                     # 這塊安靜區整個蓋住某個詞 -> 不剪
        out.append((a, b))
    return out


def trim_quiet_inside(segments: list[Segment],
                    quiet: list[tuple[int, int]],
                    fps: float) -> list[Segment]:
    """能量微剪:把「保留段」裡面真正沒聲音的地方挖掉。

    為什麼要有這步:辨識引擎給的詞會把時間撐滿,停頓被包在詞的範圍裡面,
    只看詞間隔的 build_segments 完全看不到。這裡拿實際音量掃出來的安靜區
    (見 modules/audio_probe.quiet_regions_from_array)再切一次。

    規則:
      - 只動 action="keep" 的段落。
      - 音樂/音效段(reason="music")絕不動 —— 那是刻意保護的。
      - 挖出來的安靜區一律「刪除」,不做快轉:這些停頓通常只有零點幾秒,
        快轉沒有意義,而且會在 Premiere 產生大量細碎的變速片段(效能地雷)。
      - quiet 需已排序、彼此不重疊(audio_probe 的輸出天然如此)。

    不改變總覆蓋範圍:切出來的段落仍然首尾相連、覆蓋原本那一段。"""
    # 信心 0.95 = 跟一般靜音同級,高於 marker 門檻,不會洗版 marker
    return _cut_spans(segments, [(a, b, "") for a, b in quiet],
                    reason="silence", confidence=0.95)


def apply_motion(segments: list[Segment],
                motion: list[tuple[int, int]],
                fps: float) -> list[Segment]:
    """依「畫面有沒有在動」決定沒講話的段落要加速還是剪掉。

    決策引擎只聽聲音,所以你默默示範操作的那幾秒(拉推桿、開選單、比對前後)
    會被當成停頓剪掉 —— 內容真的消失,而且不容易發現。加進畫面資訊之後:
        畫面在動   -> 加速帶過(看得到,但不佔時間)
        畫面靜止   -> 照舊剪掉

    只動 reason="silence" 的段落:
      - 音樂/音效段是刻意保護的 keep,不碰。
      - 微剪挖出來的小停頓也是 reason="silence",但它們夾在句子中間、
        通常不到一秒,轉成變速只會產生大量細碎的變速片段(效能地雷),
        所以用 MOTION_MIN_SEC 擋掉:短於這個長度的段落一律維持原判。

    判定用「重疊時間」而不是「重疊比例」:只要段落裡有夠久的畫面活動就算,
    寧可誤判成加速也不要誤刪。誤判成加速的代價很小(20 倍速下一分半只佔
    時間軸五秒),誤刪掉的示範內容卻是救不回來的。"""
    if not motion:
        return segments

    min_sec = float(getattr(cfg, "MOTION_MIN_SEC", 0.5))
    min_frames = min_sec * fps
    factor = cfg.SILENCE_SPEED_FACTOR
    out: list[Segment] = []
    for s in segments:
        if s.reason != "silence" or s.duration < min_frames:
            out.append(s)
            continue
        overlap = sum(max(0, min(s.end, b) - max(s.start, a))
                      for a, b in motion)
        moving = overlap >= min_frames
        if moving:
            out.append(Segment(s.start, s.end, "speed", factor=factor,
                               reason="silence_motion", confidence=s.confidence))
        else:
            out.append(Segment(s.start, s.end, "delete",
                               reason="silence", confidence=s.confidence))
    return out


# ---------------------------------------------------------------------------
# 重講偵測(說錯重來 -> 砍掉前一次)
# ---------------------------------------------------------------------------
# 錄教學片最常見的浪費:一句話講到一半發現講錯,停一下,重講一次。
# 前面那次是廢的,但它有完整的語音、也有詞,前面所有機制都刪不掉它。
#
# 判斷方式:把口白依「停頓」切成一句一句,拿相鄰的兩句比對文字相似度。
# 兩種情況算重講:
#   1. 整句幾乎一樣    ——「我們按這個鈕」/「我們按這個鈕」
#   2. 前一句是後一句的開頭 ——「我們按這」/「我們按這個鈕開始執行」(講一半重來)
# 命中就把「前面那次」標成刪除。留後面那次,因為重講通常才是講對的版本。
#
# 這是唯一會刪掉「真正說話內容」的機制,誤判成本比刪靜音高很多,
# 所以信心值刻意壓低 -> 一定會下 marker,請在報告/Premiere 裡逐一確認。

_PUNCT = "。,,、;;::!!??…-—~~「」『』()()\"' 　"


def _norm_text(s: str) -> str:
    """比對用的正規化:去標點空白、英文轉小寫"""
    return "".join(c for c in s.lower() if c not in _PUNCT)


def find_retakes(words: list[Word], fps: float) -> list[tuple[int, int, str]]:
    """找出「說錯重講」裡該砍掉的前一次嘗試。

    做法:不依賴斷句(辨識引擎會把詞排得很密,靠停頓根本切不出句子)。
    改成沿著逐字稿滑動,在每個「詞的交界」把前面 N 個字跟後面 N 個字對比,
    夠像就代表這裡發生了「講一次 -> 重講一次」,砍掉前面那次。
    等長比對同時涵蓋兩種情況:
      整句重講 —— 前「我們按這個鈕」/ 後「我們按這個鈕」
      講一半重來 —— 前「我們按這」  / 後「我們按這個鈕開始」(前 4 字一樣)

    回傳 [(起始幀, 結束幀, 被砍掉的那段話), ...],已排序、不重疊。"""
    if not words or not getattr(cfg, "RETAKE_DETECT", False):
        return []

    from difflib import SequenceMatcher

    sim_need = float(getattr(cfg, "RETAKE_SIMILARITY", 0.8))
    min_chars = int(getattr(cfg, "RETAKE_MIN_CHARS", 4))
    max_chars = int(getattr(cfg, "RETAKE_MAX_CHARS", 24))
    need_gap = float(getattr(cfg, "RETAKE_BOUNDARY_GAP_SEC", 0.15))

    # 攤平成一串字,並記住每個字屬於哪個詞(標點不參與比對)
    chars: list[str] = []
    owner: list[int] = []
    for wi, w in enumerate(words):
        for c in _norm_text(w.text):
            chars.append(c)
            owner.append(wi)
    text = "".join(chars)
    if len(text) < min_chars * 2:
        return []

    # 每個詞的第一個字在這串字裡的位置 = 可以當「重講交界」的候選點
    first_char_of: dict[int, int] = {}
    for pos, wi in enumerate(owner):
        first_char_of.setdefault(wi, pos)

    out: list[tuple[int, int, str]] = []
    cut_until = 0            # 已經被砍掉的字位置,避免重疊
    for wi in range(1, len(words)):
        p = first_char_of.get(wi)
        if p is None or p <= cut_until:
            continue
        # 真正的重講,講者多半會停頓一下再重來。要求交界處有個小停頓,
        # 可以擋掉大量「正常重複用字」的誤判。設 0 就是不要求。
        if need_gap > 0 and words[wi].start - words[wi - 1].end < need_gap:
            continue
        # 由長到短試:優先砍掉最長的那段重複
        best = None
        for w_len in range(min(max_chars, p - cut_until, len(text) - p),
                        min_chars - 1, -1):
            a = text[p - w_len:p]
            b = text[p:p + w_len]
            if SequenceMatcher(None, a, b).ratio() >= sim_need:
                best = w_len
                break
        if not best:
            continue
        start_char = p - best
        s = words[owner[start_char]].start_frame(fps)
        e = words[owner[p - 1]].end_frame(fps)
        if e > s:
            out.append((s, e, text[start_char:p]))
            cut_until = p
    return out


def drop_retakes(segments: list[Segment],
                retakes: list[tuple[int, int, str]],
                fps: float) -> list[Segment]:
    """把偵測到的「說錯的那一次」從保留段裡挖掉。

    信心用 cfg.RETAKE_CONFIDENCE(預設 0.5,低於 marker 門檻)
    -> 每一刀都會下 marker,方便你在 Premiere 逐一確認有沒有砍錯。"""
    return _cut_spans(segments, retakes, reason="retake",
                    confidence=float(getattr(cfg, "RETAKE_CONFIDENCE", 0.5)))


def _merge_adjacent(segments: list[Segment]) -> list[Segment]:
    """合併相鄰的同類段落,讓時間軸更乾淨(減少 PR 裡的碎片 clip)"""
    if not segments:
        return []
    merged = [segments[0]]
    for s in segments[1:]:
        last = merged[-1]
        same = (last.action == s.action and last.end == s.start
                and last.factor == s.factor and last.reason == s.reason
                and s.action != "delete")   # 刪除段各自獨立,保留 marker 資訊
        if same:
            last.end = s.end
        else:
            merged.append(s)
    return merged
