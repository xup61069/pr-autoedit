"""把設定鎖回內建預設 —— 測試不該被「使用者的個人設定」左右。

為什麼需要這個(這是真的發生過的事):

使用者在面板把「冗詞前後要停多久才算冗詞」調成 0.15 秒,存進他自己的
`config/settings_local.json`。而 test_e2e_smoke 的假資料裡,那個「嗯」前後
剛好各停 0.15 秒 —— 浮點數算出來是 0.1499999999999999,`>= 0.15` 是 False,
於是冗詞沒被刪掉,測試就紅了。**程式一行都沒改,是他調了一個面板設定。**

這種紅法最傷:他的工作規則是「測試必須全綠才能推」,而現在測試會因為
他調自己的設定而變紅。試幾次之後,人就會開始不相信測試 —— 那比沒有測試
更危險,因為真的壞掉時他也會以為「又是設定的關係」。

專案裡本來就有這個原則,只是只套用在詞庫上(見 test_e2e_smoke 裡
「不該讓他的個人詞庫決定專案測試過不過」)。這裡把它變成通用的:
測試一開始就把**所有**設定鎖回 `DEFAULTS`,測試要什麼特別值再自己覆寫。

刻意不用「逐條列出要鎖哪些」的寫法 —— 那正是這次出事的原因:
test_e2e_smoke 列了七、八個設定,偏偏漏了 FILLER_PAUSE_SEC。
只要有人新增一個設定,逐條列的清單就又過時了。
"""

from __future__ import annotations
import copy

import config.settings as cfg


def pin_defaults() -> None:
    """把 config.settings 裡的所有設定還原成內建預設值。

    `DEFAULTS` 是在讀取個人覆寫「之前」拍下的快照,所以還原之後,
    使用者的 settings_local.json / vocab_local.json 就完全影響不到測試。
    用 deepcopy 是因為裡面有 list / dict:直接指過去的話,測試改一筆
    就會把快照本身也改掉,後面的測試跟著遭殃。
    """
    for key, value in cfg.DEFAULTS.items():
        setattr(cfg, key, copy.deepcopy(value))
