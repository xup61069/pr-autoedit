/*
 * 在 Premiere Pro 內執行的 ExtendScript。
 * 面板跑完 Python 後,呼叫這裡把剪好的專案(FCP7 XML)與字幕匯入目前專案。
 */

function prImportEditedProject(xmlPath, srtPath) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案,請先新建或開啟一個專案";
        }
        var proj = app.project;

        var xmlFile = new File(xmlPath);
        if (!xmlFile.exists) {
            return "ERROR: 找不到剪輯專案檔:" + xmlPath;
        }

        // 匯入 FCP7 XML(會建立一個剪好的序列)
        // importFiles(paths, suppressUI, targetBin, importAsNumberedStills)
        proj.importFiles([xmlPath], true, proj.rootItem, false);

        // 有字幕的話一併匯入(失敗不影響主流程)
        if (srtPath) {
            var srtFile = new File(srtPath);
            if (srtFile.exists) {
                try {
                    proj.importFiles([srtPath], true, proj.rootItem, false);
                } catch (e2) { /* 字幕匯入失敗就略過 */ }
            }
        }

        return "OK";
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
 * (實驗)用 QE 後門把音訊效果掛到目前序列的每個聲音片段上。
 * QE 不是 Adobe 官方支援的 API,所以整段都包 try/catch:
 * 成功回 "OK 掛上數 失敗數",找不到效果回 "NOFX"(面板會改教使用者
 * 用音軌混音器手動掛,一次搞定)。
 * skipJoined:用 | 分隔的片段名稱開頭,例如「音樂」= 音樂段不掛降噪。
 */
function prApplyAudioEffect(effectName, skipJoined) {
    try {
        if (typeof app === "undefined" || !app.project) {
            return "ERROR: 沒有開啟中的 Premiere 專案";
        }
        if (!app.project.activeSequence) {
            return "ERROR: 沒有作用中的序列,請先點開要處理的序列";
        }
        app.enableQE();
        var fx = null;
        try { fx = qe.project.getAudioEffectByName(effectName); } catch (e1) { }
        if (!fx) return "NOFX";

        var qseq = qe.project.getActiveSequence();
        var skips = skipJoined ? String(skipJoined).split("|") : [];
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
                    item.addAudioEffect(fx);
                    applied++;
                } catch (e2) { failed++; }
            }
        }
        return "OK " + applied + " " + failed;
    } catch (e) {
        return "ERROR: " + e.toString();
    }
}
