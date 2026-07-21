/*
 * 在 Premiere Pro 內執行的 ExtendScript。
 * 面板跑完 Python 後,呼叫這裡把剪好的專案(FCP7 XML)與字幕匯入目前專案。
 */

/*
 * 刪掉一條序列。Premiere 各版本的 API 不太一致,兩種做法都試:
 * 官方的 deleteSequence,不行就用序列的 projectItem 刪。刪不掉也不算致命,
 * 頂多是舊序列還留著,所以全部包 try/catch、回傳成功與否。
 */
function prDeleteSequence(seq) {
    try {
        if (app.project.deleteSequence) {
            app.project.deleteSequence(seq);
            return true;
        }
    } catch (e1) { }
    try {
        if (seq.projectItem && seq.projectItem.deleteBin) {
            seq.projectItem.deleteBin();
            return true;
        }
    } catch (e2) { }
    return false;
}

/*
 * 匯入字幕,並盡量直接掛到序列上。
 *
 * 為什麼要先複製成新檔名:Premiere 對「同一個檔案路徑」會沿用專案裡已經
 * 存在的那個項目,不會重讀檔案內容。所以重跑後 04_subtitles.srt 明明已經
 * 更新了,匯入看到的還是上一次的舊字幕。複製成帶時間戳的檔名 = 全新路徑,
 * Premiere 一定會讀到新內容。
 *
 * 回傳給面板顯示的短字串:SUBS_OK(有掛上序列)/ SUBS_IMPORTED(只匯入到
 * 專案,要自己拖到時間軸)/ SUBS_FAIL。
 */
function prImportCaptions(proj, srtPath, seq) {
    var f = new File(srtPath);
    if (!f.exists) return "SUBS_NONE";

    var target = srtPath;
    try {
        var d = new Date();
        function p2(n) { return (n < 10 ? "0" : "") + n; }
        var stamp = p2(d.getHours()) + p2(d.getMinutes()) + p2(d.getSeconds());
        var uniq = srtPath.replace(/\.srt$/i, "_" + stamp + ".srt");
        if (f.copy(uniq)) target = uniq;
    } catch (eCopy) { /* 複製失敗就用原檔名,至少還會嘗試匯入 */ }

    try {
        proj.importFiles([target], true, proj.rootItem, false);
    } catch (eImp) {
        return "SUBS_FAIL";
    }

    // 找出剛匯入的那個字幕項目(用檔名比對)
    var wanted = target.replace(/\\/g, "/").split("/").pop();
    var item = null;
    try {
        for (var i = 0; i < proj.rootItem.children.numItems; i++) {
            var ch = proj.rootItem.children[i];
            var nm = String(ch.name || "");
            if (nm === wanted || nm === wanted.replace(/\.srt$/i, "")) item = ch;
        }
    } catch (eFind) { }

    // 試著直接建立字幕軌掛上去,省得使用者每次都要自己拖。
    // createCaptionTrack 不是每個 Premiere 版本都有,失敗就退回「請自己拖」。
    if (item && seq) {
        try {
            seq.createCaptionTrack(item, 0);
            return "SUBS_OK";
        } catch (eCap) { }
    }
    return "SUBS_IMPORTED";
}

/*
 * 匯入剪好的專案(FCP7 XML)與字幕。
 *
 * replace="1":覆蓋模式。匯入後,把「同名的舊序列」刪掉,只留最新這條
 * ——重跑同一支影片不會愈堆愈多。序列名稱由 pipeline 產生、帶影片名
 * (例:「我的教學 自動剪輯」),所以只會刪到這支片自己的舊序列,
 * 別支影片的序列絕對不會被動到。
 * replace 不是 "1" 時維持舊行為:新序列照加、舊的留著(可以互相比較、反悔)。
 */
function prImportEditedProject(xmlPath, srtPath, replace) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案,請先新建或開啟一個專案";
        }
        var proj = app.project;

        var xmlFile = new File(xmlPath);
        if (!xmlFile.exists) {
            return "ERROR: 找不到剪輯專案檔:" + xmlPath;
        }

        // 匯入前先記下現有序列,才分得出哪條是這次新產生的
        var before = {};
        var i;
        for (i = 0; i < proj.sequences.numSequences; i++) {
            before[proj.sequences[i].sequenceID] = true;
        }

        // 匯入 FCP7 XML(會建立一個剪好的序列)
        // importFiles(paths, suppressUI, targetBin, importAsNumberedStills)
        proj.importFiles([xmlPath], true, proj.rootItem, false);

        // 找出新序列,並收集「同名的舊序列」當作待刪清單
        var fresh = null, stale = [];
        for (i = 0; i < proj.sequences.numSequences; i++) {
            if (!before[proj.sequences[i].sequenceID]) fresh = proj.sequences[i];
        }
        if (fresh) {
            for (i = 0; i < proj.sequences.numSequences; i++) {
                var s = proj.sequences[i];
                if (s.sequenceID !== fresh.sequenceID && s.name === fresh.name) {
                    stale.push(s);
                }
            }
            // 先把新序列打開,再刪舊的:免得刪掉的正好是時間軸上開著那條
            try { proj.openSequence(fresh.sequenceID); } catch (eOpen) { }
        }

        var removed = 0;
        if (replace === "1" || replace === true) {
            for (i = 0; i < stale.length; i++) {
                if (prDeleteSequence(stale[i])) removed++;
            }
        }

        // 有字幕的話一併匯入(失敗不影響主流程)
        var subs = "";
        if (srtPath) subs = prImportCaptions(proj, srtPath, fresh);

        return "OK " + removed + " " + subs;
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/* 把字幕匯入並掛到「目前作用中的序列」(剪輯後工具的產字幕鈕用) */
function prImportCaptionsToActive(srtPath) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        return prImportCaptions(app.project, srtPath,
                                app.project.activeSequence);
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/* 一個素材項目對應到硬碟上的哪個檔案 */
function prMediaPathOf(item) {
    try {
        if (item && item.getMediaPath) {
            var p = item.getMediaPath();
            if (p && String(p).length) return String(p);
        }
    } catch (e) { }
    return null;
}

/*
 * 找出「你現在選取的素材」是哪個檔案,讓面板不必再開檔案總管去翻。
 * 先看專案面板裡選取的項目,沒有的話再看時間軸上選取的片段。
 * 回傳 "OK <完整路徑>",沒選東西回 "NONE"。
 */
function prGetSelectedMedia() {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        var i, p;

        // 1. 專案面板裡選取的素材
        try {
            var sel = app.getCurrentProjectViewSelection();
            if (sel && sel.length) {
                for (i = 0; i < sel.length; i++) {
                    p = prMediaPathOf(sel[i]);
                    if (p) return "OK " + p;
                }
            }
        } catch (e1) { }

        // 2. 時間軸上選取的片段
        try {
            var seq = app.project.activeSequence;
            if (seq && seq.getSelection) {
                var clips = seq.getSelection();
                for (i = 0; i < clips.length; i++) {
                    p = clips[i] ? prMediaPathOf(clips[i].projectItem) : null;
                    if (p) return "OK " + p;
                }
            }
        } catch (e2) { }

        return "NONE";
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/* 匯入單一檔案(例如剪完後重新產生的字幕 SRT) */
function prImportFile(p) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        var f = new File(p);
        if (!f.exists) return "ERROR: 找不到檔案:" + p;
        app.project.importFiles([p], true, app.project.rootItem, false);
        return "OK";
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/*
 * 把「目前作用中序列」的版面寫成 JSON(給 modules/live_subs.py 對位字幕用)。
 * 每個片段記:時間軸位置(start/end)、來源入出點(in/out)、速度倍率。
 * 只讀 V1 視訊軌 —— 本工具產生的序列,V1 就是完整的剪輯結構。
 */
function prDumpSequenceLayout(outPath) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        var seq = app.project.activeSequence;
        if (!seq) return "ERROR: 沒有作用中的序列,請先在 Premiere 點開要產字幕的序列";
        if (seq.videoTracks.numTracks < 1) return "ERROR: 這個序列沒有視訊軌";

        var tr = seq.videoTracks[0];
        var parts = [];
        for (var i = 0; i < tr.clips.numItems; i++) {
            var c = tr.clips[i];
            var speed = 1.0;
            try { speed = Math.abs(c.getSpeed()) || 1.0; } catch (eS) { }
            parts.push('{"start":' + c.start.seconds +
                ',"end":' + c.end.seconds +
                ',"in":' + c.inPoint.seconds +
                ',"out":' + c.outPoint.seconds +
                ',"speed":' + speed + '}');
        }
        if (!parts.length) return "ERROR: 時間軸上沒有片段";

        var f = new File(outPath);
        f.encoding = "UTF-8";
        if (!f.open("w")) return "ERROR: 無法寫入暫存檔:" + outPath;
        f.write('{"clips":[' + parts.join(",") + ']}');
        f.close();
        return "OK " + parts.length;
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/*
 * 這台 Premiere 有哪些音訊效果可以掛(回傳用 | 分隔的名稱)。
 *
 * 存在的理由:效果名稱是「跟著介面語言翻譯」的,所以在別人的 Premiere 上
 * 到底叫什麼,寫死猜不到。掛效果失敗時把這份清單印出來,一眼就看得出
 * 是名字對不上還是根本沒這個效果,不必瞎猜。
 */
function prListAudioEffects() {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        app.enableQE();
        // 有些版本是屬性、有些是函式,兩種都接
        var list = qe.project.getAudioEffectList;
        if (typeof list === "function") list = qe.project.getAudioEffectList();
        if (!list || !list.length) return "NONE";
        var names = [];
        for (var i = 0; i < list.length; i++) {
            names.push(String(list[i].name || list[i]));
        }
        return "OK " + names.join("|");
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}

/*
 * 在這台 Premiere 上,把一組「候選名稱」解析成真的存在的那一個。
 * 先精準比對(不分大小寫),再退而求其次用包含比對——中文版有時會在
 * 名稱前後多帶字,例如「動態處理器」。找不到回 null。
 */
function prResolveEffect(candidates) {
    var i, fx;
    for (i = 0; i < candidates.length; i++) {
        var nm = candidates[i];
        if (!nm) continue;
        try {
            fx = qe.project.getAudioEffectByName(nm);
            if (fx) return { fx: fx, name: nm };
        } catch (e1) { }
    }
    // 精準比對全軍覆沒,改掃一遍完整清單做包含比對
    try {
        var list = qe.project.getAudioEffectList;
        if (typeof list === "function") list = qe.project.getAudioEffectList();
        for (var j = 0; list && j < list.length; j++) {
            var have = String(list[j].name || list[j]);
            for (i = 0; i < candidates.length; i++) {
                var want = String(candidates[i] || "").toLowerCase();
                if (want && have.toLowerCase().indexOf(want) >= 0) {
                    try {
                        fx = qe.project.getAudioEffectByName(have);
                        if (fx) return { fx: fx, name: have };
                    } catch (e2) { }
                }
            }
        }
    } catch (e3) { }
    return null;
}

/*
 * (實驗)用 QE 後門把一串人聲效果依序掛到目前序列的每個聲音片段上。
 * QE 不是 Adobe 官方支援的 API,所以整段都包 try/catch。
 *
 * chainJoined:效果鏈,用 || 分隔每一個效果,每個效果內部用 | 分隔候選名稱。
 *              例:"DeNoise|消除雜訊||Parametric Equalizer|參數等化器"
 *              給候選名稱是因為效果名會跟著介面語言翻譯,寫死一個會找不到。
 * skipJoined:用 | 分隔的片段名稱開頭,例如「音樂」= 音樂段不掛
 *              (降噪是衝著人聲設計的,會把音樂當噪音削掉)。
 *
 * 回傳:"OK 掛上數 失敗數 用到的效果名"、"NOFX 沒解析到的候選"、
 *       "TOOMANY 片段數"。面板會照這些狀況給不同的說明。
 */
function prApplyVoiceChain(chainJoined, skipJoined, maxClips) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        if (!app.project.activeSequence) {
            return "ERROR: 沒有作用中的序列,請先點開要處理的序列";
        }
        app.enableQE();

        // 先把每個效果解析成這台機器上真的存在的名稱。
        // 一個都湊不齊就整個不做——只掛到一半的音色比不掛還難判斷。
        var groups = String(chainJoined).split("||");
        var resolved = [], missing = [];
        for (var g = 0; g < groups.length; g++) {
            var cands = groups[g].split("|");
            var hit = prResolveEffect(cands);
            if (hit) resolved.push(hit); else missing.push(cands[0]);
        }
        if (!resolved.length || missing.length) {
            return "NOFX " + missing.join("|");
        }

        var qseq = qe.project.getActiveSequence();
        var skips = skipJoined ? String(skipJoined).split("|") : [];

        // 先數一遍要掛幾個。剪很兇的片動輒上千個片段,再乘上鏈裡的效果數,
        // 時間軸會明顯變頓。數量太多就直接拒絕,讓面板改教「整軌掛一次」。
        var limit = parseInt(maxClips, 10);
        if (!isNaN(limit) && limit > 0) {
            var n = 0, t2, tr2;
            for (t2 = 0; t2 < qseq.numAudioTracks; t2++) {
                tr2 = qseq.getAudioTrackAt(t2);
                for (var k = 0; k < tr2.numItems; k++) {
                    try {
                        var it2 = tr2.getItemAt(k);
                        if (it2 && it2.type !== "Empty") n++;
                    } catch (eC) { }
                }
            }
            if (n > limit) return "TOOMANY " + n;
        }

        var applied = 0, failed = 0;
        for (var t = 0; t < qseq.numAudioTracks; t++) {
            var track = qseq.getAudioTrackAt(t);
            for (var i = 0; i < track.numItems; i++) {
                try {
                    var item = track.getItemAt(i);
                    if (!item || item.type === "Empty") continue;
                    var nm = String(item.name || "");
                    var skip = false;
                    for (var s = 0; s < skips.length; s++) {
                        if (skips[s] && nm.indexOf(skips[s]) === 0) { skip = true; break; }
                    }
                    if (skip) continue;
                    // 依序掛:降噪 -> EQ -> 壓縮。順序有差,先清乾淨再修頻率再壓音量
                    for (var r = 0; r < resolved.length; r++) {
                        item.addAudioEffect(resolved[r].fx);
                    }
                    applied++;
                } catch (e2) { failed++; }
            }
        }
        var used = [];
        for (var u = 0; u < resolved.length; u++) used.push(resolved[u].name);
        return "OK " + applied + " " + failed + " " + used.join("|");
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}
