#!/usr/bin/env python3
"""
ad_remover.py - Commercial Radio 2 (叱咤903) 自動廣告、整點新聞、交通與天氣切除模組

技術特點：
1. 使用 whisper-cli + Apple Silicon Metal GPU 硬體加速進行快速粵語轉錄。
2. 結合整點新聞狀態機、半點交通消息與商業廣告詞庫進行語意標記。
3. 支援「保留整點語音報時與嗶一聲（Time Pip）」，緊接切除後續新聞與廣告破口。
4. 支援節目官方開場 Jingle 精準保護（保留前奏、搞笑短劇與主題曲）。
5. 雙重安全防護（Fail-safe）：去廣告異常或時長比例不合常理時，自動回退保留原音檔。
"""

import os
import sys
import json
import re
import subprocess
import shutil
import time
from datetime import timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
FFPROBE_BIN = "/opt/homebrew/bin/ffprobe" if os.path.exists("/opt/homebrew/bin/ffprobe") else "ffprobe"

WHISPER_BIN = "/opt/homebrew/bin/whisper-cli"
if not os.path.exists(WHISPER_BIN):
    WHISPER_BIN = "/opt/homebrew/bin/whisper-cpp" if os.path.exists("/opt/homebrew/bin/whisper-cpp") else "whisper-cli"

DEFAULT_MODEL = os.path.join(SCRIPT_DIR, "models", "ggml-base.bin")

# 1. 新聞狀態機關鍵字
NEWS_START_KEYWORDS = [
    "新聞報導", "新聞報道", "商業電台新聞", "即是七九零三", "即測903 新聞", "叱咤903 新聞"
]
NEWS_END_KEYWORDS = [
    "這節新聞播出完", "新聞報道完", "新聞報導完", "這節新聞播出", "報道完畢", "報導完畢", "這節新聞播完"
]

# 2. 交通消息關鍵字
TRAFFIC_KEYWORDS = [
    "最新交通情況", "交通消息", "交通意外", "行車緩慢", "多層擠塞", "龍尾", "紅磡海底隧道",
    "西區海底隧道", "東區海底隧道", "獅子山隧道", "大老山隧道", "吐露港公路", "屯門公路", "東區走廊"
]

# 3. 天氣預報關鍵字
WEATHER_KEYWORDS = [
    "天氣預測", "香港天文台", "天文台", "天文台錄得", "本港地區天氣", "本港地區今天", "氣溫是", "相對濕度",
    "錄得氣溫", "局部地區有", "一兩陣驟雨", "紫外線指數", "黃色暴雨", "紅色暴雨", "黑色暴雨", "雷暴警告",
    "天氣雨叉", "天氣溫度", "空氣質素健康指數", "吹和緩", "氣溫", "今天天氣", "天氣如茶", "空氣質素", "監測站"
]

# 4. 公益宣傳與政府廣告
PSA_KEYWORDS = [
    "香港紅十字會", "輸血服務中心", "捐血", "血庫存量", "衛生署", "防騙視伏器", "反詐騙協調中心",
    "防騙熱線", "18222", "簡約公屋", "房屋局", "香港房屋委員會", "搬遷津貼", "勞工處", "強制性公積金",
    "積金局", "民政事務總署", "民政事務處", "廉政公署", "香港國際鐘表展", "貿發局", "展覽會", "免費入場",
    "食安中心", "河土", "河套", "創科", "創新合作區", "禁煙", "控煙", "吸煙", "定額罰款",
    "選舉管理委員會", "選管會", "區議會", "委員會", "諮詢", "2891", "291-1001", "EAC.HK", "抽獎", "遊戲規則", "得獎"
]

# 5. 商台台呼、節目宣傳與熱線
STATION_PROMOS = [
    "1872903", "叱咤903", "商業二台", "雷霆881", "即上各大音樂平台收聽", "戀愛信號",
    "即拆903", "拆903", "即拆 903", "音樂平台", "平台收聽", "即相隔", "即上各大",
    "有誰共鳴", "有稅共鳴", "引發你的共鳴", "50881903", "中銀香港理財", "一切從音樂開始",
    "LIFE 音樂會", "LIFE音樂會", "woodie", "woodby", "即刻學堂", "即參學彈"
]

# 6. 商業特約廣告詞庫
AD_KEYWORDS = [
    "查詢", "詳情", "致電", "熱線", "登記", "立即致電", "優惠", "折扣", "送完即止",
    "條款及細則", "受條款及細則約束", "贊助", "冠名贊助", "特約贊助", "全力贊助", "特約",
    "限時優惠", "現正發售", "門票現正", "公開發售", "快達票", "城網", "撲飛",
    "免費體驗", "立即登記", "電話查詢", "歡迎致電", "借定唔借", "還得到先好借",
    "儲蓄保險", "人壽保險", "定期存款", "年利率", "信用卡", "現金回贈",
    "立即下載", "應用程式", "各大門市", "各大分店", "各大專櫃", "專門店", "門市", "分店",
    "請瀏覽", "詳情請", "歡迎查詢", "網址", "dot com", ".com", ".hk",
    "用心為您", "伴你同行", "專業之選", "為你守護", "帶給您", "生活更精彩", "未來戰士", "未來展示",
    "設計站", "加規站", "車價", "低利率", "BMW", "B&W", "大廣告", "月餅", "美心",
    "衝衝衝", "音樂會", "向前座", "繼續衝"
]

# 7. 節目專屬 Jingle / 主持關鍵字（重點保護，絕不可切！）
PROGRAM_JINGLE_KEYWORDS = [
    "bad girl", "大過佬", "來大笑代替上路", "完美阿正", "笑爆嘴", "elsie", "alsie", "l.c", "小姐, l.c",
    "你公司最討厭", "最討厭的甚麼", "最討厭的是甚麼", "帶過老", "大過老", "代替上路",
    "有人拐住你", "有人gua住你", "有人刮住你", "有人掛住你", "你知道不知道有人",
    "你拍拖嘅時候", "你拍拖的時候", "阿正 你拍拖", "阿鄭,你拍拖", "拍拖的時候最討厭", "拍拖嘅時候最討厭",
    "阿正,你拍拖", "我都冇男朋友", "我也沒有男朋友",
    "聖艾粒", "lalalala", "少爺占", "當奴", "艾粒"
]

# 8. 整點與半點報時關鍵字（需精確定位報時與嗶聲）
TIME_ANNOUNCEMENT_KEYWORDS = [
    "10點", "11點", "12點", "5點", "6點", "7點", "十點", "十一點", "十二點", "五點", "六點", "七點",
    "10點半", "11點半", "十點半", "十一點半", "10時半", "11時半", "5點半", "6點半", "7點半"
]

# 9. 半點時鐘破口關鍵字 (:30)
HALF_HOUR_KEYWORDS = [
    "10點半", "10時半", "十點半", "11點半", "11時半", "十一點半",
    "5點半", "5時半", "五點半", "6點半", "6時半", "六點半",
    "17點半", "18點半"
]


def get_audio_duration(file_path):
    """獲取音訊總時長（秒）"""
    cmd = [
        FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        out = subprocess.check_output(cmd).decode().strip()
        return float(out)
    except Exception as e:
        print(f"⚠️ 無法取得音訊時長: {e}")
        return 0.0


def extract_16k_wav(input_file, output_wav, start_sec=None, duration_sec=None):
    """將輸入音檔轉碼為 Whisper 所需之 16kHz mono 16-bit PCM WAV"""
    cmd = [FFMPEG_BIN, "-y"]
    if start_sec is not None:
        cmd.extend(["-ss", str(start_sec)])
    if duration_sec is not None:
        cmd.extend(["-t", str(duration_sec)])
    cmd.extend([
        "-i", input_file,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_wav
    ])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def run_whisper_transcription(wav_file, model_path=None, json_base_path=None):
    """調用 whisper-cli 搭配 Metal GPU 執行粵語/中文語音辨識 (支援逐字稿快取)"""
    if model_path is None:
        model_path = DEFAULT_MODEL
    if json_base_path is None:
        json_base_path = os.path.splitext(wav_file)[0]

    json_file = f"{json_base_path}.json"
    if os.path.exists(json_file):
        print(f"⚡ 發現現有逐字稿快取: {json_file}，略過 Whisper 轉錄直接載入！")
    else:
        cmd = [
            WHISPER_BIN,
            "-m", model_path,
            "-l", "zh",
            "-mc", "0",
            "--prompt", "商業電台 叱咤903 Bad Girl 大過佬 聖艾粒 阿正 Elsie 新聞報道 交通消息 天氣預測 你拍拖最討厭 你公司最討厭 有人掛住你",
            "-f", wav_file,
            "-oj",
            "-of", json_base_path
        ]
        start_time = time.time()
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        elapsed = time.time() - start_time
        print(f"⚡ Whisper 轉錄完成，耗時: {elapsed:.1f} 秒")

    if not os.path.exists(json_file):
        raise FileNotFoundError(f"Whisper 未輸出 JSON 檔案: {json_file}")

    with open(json_file, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    return data.get("transcription", [])


def detect_ad_intervals(segments, total_duration, time_offset=0.0):
    """
    雙引擎廣告、新聞與交通破口偵測器：
    引擎 1：整點新聞與半點廣告破口狀態機（精確保留整點與半點報時與嗶聲，整段跨越新聞、天氣與特約廣告，直到節目 Jingle 起拍點）
    引擎 2：交通消息、公益宣傳與商業特約廣告詞庫聚合器
    """
    cuts = []

    # -------------------------------------------------------------
    # 引擎 1：整點新聞與半點廣告破口狀態機 (Clock Break State Machine)
    # -------------------------------------------------------------
    in_break = False
    break_start = None
    break_type = None
    last_ad_end = None

    for idx, seg in enumerate(segments):
        text = seg.get("text", "").strip()
        if not text:
            continue

        if "offsets" in seg:
            f_sec = time_offset + seg["offsets"].get("from", 0) / 1000.0
            t_sec = time_offset + seg["offsets"].get("to", 0) / 1000.0
        else:
            f_sec = time_offset + seg.get("start", 0.0)
            t_sec = time_offset + seg.get("end", 0.0)

        is_ad_item = any(k in text for k in TRAFFIC_KEYWORDS + WEATHER_KEYWORDS + PSA_KEYWORDS + STATION_PROMOS + AD_KEYWORDS)
        if is_ad_item:
            last_ad_end = t_sec

        # 1. 檢查整點新聞破口 (Top-of-the-hour break)
        is_news_start = ("新聞" in text or "商業電台新聞" in text or "報道新聞" in text)
        if is_news_start and not in_break:
            in_break = True
            break_type = "整點新聞破口"
            start_cut = f_sec
            for p in range(max(0, idx - 6), idx):
                p_text = segments[p].get("text", "")
                if any(k in p_text for k in TIME_ANNOUNCEMENT_KEYWORDS):
                    p_to = time_offset + (segments[p]["offsets"]["to"] / 1000.0 if "offsets" in segments[p] else segments[p].get("end", 0.0))
                    start_cut = p_to + 0.2
                    break
            break_start = start_cut
            print(f"  🛑 [引擎1] 整點新聞破口開始於: {timedelta(seconds=int(break_start))} ({break_start:.2f}s) [觸發: {text[:25]}]")
            continue

        # 2. 檢查半點廣告破口 (Half-hour :30 break，需排除逢星期X、由X點至X點等節目宣傳)
        is_schedule = any(w in text for w in ["星期", "逢", "至", "由", "到", "節目", "收聽"])
        is_half_hour = any(k in text for k in HALF_HOUR_KEYWORDS) and not is_schedule
        if is_half_hour and not in_break:
            in_break = True
            break_type = "半點廣告破口"
            break_start = t_sec + 0.2
            print(f"  🛑 [引擎1] 半點廣告破口開始於: {timedelta(seconds=int(break_start))} ({break_start:.2f}s) [報時: {text[:25]}]")
            continue

        # 3. 檢查破口是否遇到節目專屬 Jingle / Bumper 重開
        if in_break:
            is_jingle = any(kw.lower() in text.lower() for kw in PROGRAM_JINGLE_KEYWORDS)
            # 安全機制：若超過 750 秒仍未遇到 Jingle，強制截斷破口
            if is_jingle or (f_sec - break_start > 750.0):
                in_break = False
                # 若為半點廣告破口，且最後廣告與 Jingle 之間有搞笑短劇空隙 (> 10 秒)
                if break_type == "半點廣告破口" and last_ad_end and break_start < last_ad_end and (f_sec - last_ad_end) > 10.0:
                    end_cut = last_ad_end + 0.6
                else:
                    end_cut = max(break_start + 5.0, f_sec - 1.8)

                cuts.append({
                    "start": round(break_start, 2),
                    "end": round(end_cut, 2),
                    "duration": round(end_cut - break_start, 2),
                    "sample_text": f"{break_type} (已保留報時/接回Jingle)"
                })
                print(f"  🎙️ [引擎1] {break_type}結束於 Jingle 前: {timedelta(seconds=int(end_cut))} ({end_cut:.2f}s) (時長: {end_cut - break_start:.1f}s)")

    # 若錄音在破口結束前就結束（例如節目尾聲新聞緩衝）
    if in_break and break_start is not None:
        cuts.append({
            "start": round(break_start, 2),
            "end": round(total_duration, 2),
            "duration": round(total_duration - break_start, 2),
            "sample_text": "錄音結尾新聞緩衝破口"
        })
        print(f"  🛑 [引擎1] 錄音結尾新聞破口切除至結尾: {timedelta(seconds=int(break_start))} ({break_start:.2f}s) -> {timedelta(seconds=int(total_duration))} ({total_duration:.2f}s)")

    # -------------------------------------------------------------
    # 引擎 2：半點交通與商業廣告詞庫聚合器
    # -------------------------------------------------------------
    tagged = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        if "offsets" in seg:
            f_sec = time_offset + seg["offsets"].get("from", 0) / 1000.0
            t_sec = time_offset + seg["offsets"].get("to", 0) / 1000.0
        else:
            f_sec = time_offset + seg.get("start", 0.0)
            t_sec = time_offset + seg.get("end", 0.0)

        # 若該片段已被引擎 1 覆蓋，則略過
        already_covered = any(c["start"] - 2.0 <= f_sec and t_sec <= c["end"] + 2.0 for c in cuts)
        if already_covered:
            continue

        # 檢查是否為保護內容（節目 Jingle、整點報時）
        is_program_jingle = any(kw.lower() in text.lower() for kw in PROGRAM_JINGLE_KEYWORDS)
        is_time_announcement = any(kw in text for kw in TIME_ANNOUNCEMENT_KEYWORDS)

        reasons = []
        if any(kw in text for kw in TRAFFIC_KEYWORDS):
            reasons.append("交通消息")
        if any(kw in text for kw in WEATHER_KEYWORDS):
            reasons.append("天氣預測")
        if any(kw in text for kw in PSA_KEYWORDS):
            reasons.append("公益/政府宣傳")
        if any(kw in text for kw in STATION_PROMOS):
            reasons.append("電台宣傳")
        if any(kw in text for kw in AD_KEYWORDS):
            reasons.append("商業廣告")

        is_ad = len(reasons) > 0 and not is_program_jingle and not is_time_announcement

        tagged.append({
            "start": f_sec,
            "end": t_sec,
            "text": text,
            "is_ad": is_ad,
            "reasons": reasons
        })

    # 廣告聚合（間隔 <= 100 秒聚合，以完整涵蓋特約贊助短片）
    ad_blocks = []
    current_block = None

    for seg in tagged:
        if seg["is_ad"]:
            if current_block is None:
                current_block = {
                    "start": seg["start"],
                    "end": seg["end"],
                    "segments": [seg]
                }
            else:
                gap = seg["start"] - current_block["end"]
                if gap <= 100.0:
                    current_block["end"] = seg["end"]
                    current_block["segments"].append(seg)
                else:
                    ad_blocks.append(current_block)
                    current_block = {
                        "start": seg["start"],
                        "end": seg["end"],
                        "segments": [seg]
                    }

    if current_block:
        ad_blocks.append(current_block)

    for b in ad_blocks:
        dur = b["end"] - b["start"]
        if dur >= 8.0 or len(b["segments"]) >= 2:
            c_s = 0.0 if b["start"] <= 15.0 else max(0.0, b["start"] - 0.5)
            c_e = min(total_duration, b["end"] + 0.5)
            if c_e > c_s + 3.0:
                cuts.append({
                    "start": round(c_s, 2),
                    "end": round(c_e, 2),
                    "duration": round(c_e - c_s, 2),
                    "sample_text": b["segments"][0]["text"][:30]
                })

    # 合併與去重所有 cuts
    cuts.sort(key=lambda x: x["start"])
    merged_cuts = []
    for c in cuts:
        if not merged_cuts:
            merged_cuts.append(c)
        else:
            prev = merged_cuts[-1]
            if c["start"] <= prev["end"] + 5.0:
                prev["end"] = max(prev["end"], c["end"])
                prev["duration"] = round(prev["end"] - prev["start"], 2)
            else:
                merged_cuts.append(c)

    return tagged, merged_cuts


def assemble_cleaned_audio(input_file, cuts, output_file, total_duration):
    """根據切除區間，調用 FFmpeg 進行精準無損無縫拼接"""
    if not cuts:
        print("未偵測到需要切除的廣告區間，直接複製音訊。")
        shutil.copy2(input_file, output_file)
        return

    # 計算保留段落 (Keep Intervals)
    keep_intervals = []
    cursor = 0.0
    for cut in cuts:
        if cut["start"] > cursor + 0.5:
            keep_intervals.append((cursor, cut["start"]))
        cursor = max(cursor, cut["end"])
    if cursor < total_duration - 0.5:
        keep_intervals.append((cursor, total_duration))

    print(f"\n✂️ 將保留以下 {len(keep_intervals)} 個精華內容段落:")
    for idx, (ks, ke) in enumerate(keep_intervals):
        print(f"  段落 #{idx+1}: {timedelta(seconds=int(ks))} ({ks:.1f}s) -> {timedelta(seconds=int(ke))} ({ke:.1f}s) [時長: {timedelta(seconds=int(ke-ks))}]")

    # 使用 FFmpeg concat filter 進行精確合成，消除爆音
    filter_parts = []
    concat_inputs = []
    for idx, (ks, ke) in enumerate(keep_intervals):
        filter_parts.append(f"[0:a]atrim=start={ks}:end={ke},asetpts=PTS-STARTPTS[a{idx}];")
        concat_inputs.append(f"[a{idx}]")

    filter_complex = "".join(filter_parts) + "".join(concat_inputs) + f"concat=n={len(keep_intervals)}:v=0:a=1[outa]"

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", input_file,
        "-filter_complex", filter_complex,
        "-map", "[outa]",
        "-c:a", "aac",
        "-b:a", "64k",
        "-movflags", "+faststart",
        output_file
    ]

    start_time = time.time()
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print(f"✅ 剪輯與合成完成，耗時: {time.time()-start_time:.1f} 秒 -> {output_file}")


def process_audio_ad_removal(input_file, output_file=None, model_path=None):
    """
    全自動去廣告主入口（含雙重安全防護機制）
    :param input_file: 原始廣播音檔 (如 recordings/bgog-20260904.aac)
    :param output_file: 輸出純淨音檔 (若為 None 則預設為 原檔名_cleaned.m4a)
    :param model_path: Whisper 模型路徑 (預設: models/ggml-base.bin)
    :return: (final_output_path, is_cleaned, stats_dict)
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"找不到輸入音檔: {input_file}")

    if output_file is None:
        base, _ = os.path.splitext(input_file)
        output_file = f"{base}_cleaned.m4a"

    total_duration = get_audio_duration(input_file)
    print(f"\n{'='*70}")
    print(f"🎙️ 開始商台廣播自動去廣告處理: {os.path.basename(input_file)}")
    print(f"檔案總時長: {timedelta(seconds=int(total_duration))} ({total_duration:.1f} 秒)")
    print(f"{'='*70}")

    temp_wav = os.path.join(SCRIPT_DIR, "temp_full_16k.wav")
    transcript_base = os.path.splitext(input_file)[0] + "_transcript"

    try:
        # 1. 轉碼為 16kHz mono WAV (若逐字稿已存在則略過)
        if not os.path.exists(f"{transcript_base}.json"):
            print("\n[步驟 1/4] 正在將錄音轉碼為 Whisper 專用 16kHz WAV...")
            extract_16k_wav(input_file, temp_wav)
        else:
            print("\n[步驟 1/4] 發現現有逐字稿，略過轉碼 WAV。")

        # 2. Whisper 粵語全集轉錄
        print("\n[步驟 2/4] 正在執行 Whisper Metal 硬體加速語音辨識...")
        segments = run_whisper_transcription(temp_wav, model_path=model_path, json_base_path=transcript_base)

        # 3. 廣告/新聞區間語意偵測
        print("\n[步驟 3/4] 正在分析逐字稿並識別廣告、新聞與交通破口...")
        tagged, cuts = detect_ad_intervals(segments, total_duration)

        total_cut_seconds = sum(c["duration"] for c in cuts)
        print(f"\n📊 偵測結果分析:")
        print(f"  總共發現 {len(cuts)} 個非節目廣告/新聞破口")
        print(f"  預計切除總時長: {timedelta(seconds=int(total_cut_seconds))} ({total_cut_seconds:.1f} 秒)")

        for idx, cut in enumerate(cuts):
            print(f"    破口 #{idx+1}: {timedelta(seconds=int(cut['start']))} -> {timedelta(seconds=int(cut['end']))} (時長: {timedelta(seconds=int(cut['duration']))}) [{cut['sample_text']}]")

        # 4. 安全合理性檢查 (Fail-Safe Verification)
        # 2小時節目廣告通常在 10 ~ 35 分鐘之間 (佔比 10% ~ 40%)
        # 若切除時長 < 2 分鐘（幾乎沒切）或 > 50%（切除過度），觸發安全回退
        cut_ratio = total_cut_seconds / max(1.0, total_duration)
        if total_cut_seconds < 120.0:
            print("\n⚠️ 警告: 偵測到的切除時長過短 (< 2 分鐘)，為保證節目完整性，安全保留原始音檔。")
            shutil.copy2(input_file, output_file)
            return output_file, False, {"total_cuts": len(cuts), "cut_seconds": 0.0}

        if cut_ratio > 0.45:
            print(f"\n⚠️ 警告: 切除比例異常過高 ({cut_ratio*100:.1f}%)，為防誤切節目，安全保留原始音檔。")
            shutil.copy2(input_file, output_file)
            return output_file, False, {"total_cuts": len(cuts), "cut_seconds": 0.0}

        # 5. 執行音訊切除與無縫合成
        print(f"\n[步驟 4/4] 正在執行音訊精準剪切與無縫拼接...")
        assemble_cleaned_audio(input_file, cuts, output_file, total_duration)

        cleaned_duration = get_audio_duration(output_file)
        cleaned_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n{'='*70}")
        print("🎉 純淨版 Podcast 音檔生成成功！")
        print(f"  輸出路徑: {output_file}")
        print(f"  原始時長: {timedelta(seconds=int(total_duration))} ({total_duration:.1f} 秒)")
        print(f"  純淨時長: {timedelta(seconds=int(cleaned_duration))} ({cleaned_duration:.1f} 秒)")
        print(f"  共節省時間: {timedelta(seconds=int(total_cut_seconds))} ({total_cut_seconds:.1f} 秒)")
        print(f"  檔案大小: {cleaned_size_mb:.2f} MB")
        print(f"{'='*70}\n")

        return output_file, True, {
            "total_cuts": len(cuts),
            "cut_seconds": total_cut_seconds,
            "original_duration": total_duration,
            "cleaned_duration": cleaned_duration
        }

    except Exception as e:
        print(f"\n❌ 去廣告過程發生異常: {e}")
        print("🛡️ 啟動安全防護機制: 自動回退使用原始音檔，確保 Podcast 發布不受影響。")
        shutil.copy2(input_file, output_file)
        return output_file, False, {"error": str(e)}

    finally:
        # 清理暫存音檔 WAV
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        in_file = sys.argv[1]
        out_file = sys.argv[2] if len(sys.argv) > 2 else None
        process_audio_ad_removal(in_file, out_file)
    else:
        test_file = os.path.join(SCRIPT_DIR, "recordings", "bgog-20260904.aac")
        if os.path.exists(test_file):
            process_audio_ad_removal(test_file)
        else:
            print("請指定輸入音檔路徑。")
