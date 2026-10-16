#!/usr/bin/env python3
"""
ad_remover.py - Commercial Radio 2 (叱咤903) 自動廣告、整點新聞、交通與天氣切除模組

技術特點：
1. 使用 whisper-cli + Apple Silicon Metal GPU 硬體加速進行超快速粵語全集轉錄。
2. 整合 Google Gemini 3.6 Flash 雲端大模型進行前文後理（上下文）全篇語意深度審查：
   - 區分主持人隨口閒聊口語（如「贊助」、「轉數快」、「車」）與真實特約廣告。
   - 保護節目專屬單元（如「黃埔 AI 豪子」、「街仔好人」）。
   - 保留整點前完整主持人告別語與過場 Jingle。
3. 內建本機啟發式雙引擎（整點新聞狀態機 + 商業廣告詞庫）作為雙重安全回退（Fail-safe）。
4. 支援「保留整點語音報時與嗶一聲（Time Pip）」，消除爆音與跳切。
5. 雙重安全防護機制：若 AI 或規則引擎異常或切除比例不合常理，自動回退保留原音檔。
"""

import os
import sys
import json
import re
import subprocess
import shutil
import time
import urllib.request
import ssl
from datetime import timedelta

try:
    import certifi
except ImportError:
    certifi = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
FFPROBE_BIN = "/opt/homebrew/bin/ffprobe" if os.path.exists("/opt/homebrew/bin/ffprobe") else "ffprobe"

WHISPER_BIN = "/opt/homebrew/bin/whisper-cli"
if not os.path.exists(WHISPER_BIN):
    WHISPER_BIN = "/opt/homebrew/bin/whisper-cpp" if os.path.exists("/opt/homebrew/bin/whisper-cpp") else "whisper-cli"

LARGE_MODEL = os.path.join(SCRIPT_DIR, "models", "ggml-large-v3-turbo.bin")
DEFAULT_MODEL = LARGE_MODEL if os.path.exists(LARGE_MODEL) else os.path.join(SCRIPT_DIR, "models", "ggml-base.bin")

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
    "西區海底隧道", "東區海底隧道", "獅子山隧道", "大老山隧道", "吐露港公路", "屯門公路", "東區走廊",
    "馬路的士交通消息", "馬路迪士交通消息"
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
    "選舉管理委員會", "選管會", "區議會", "委員會", "諮詢", "2891", "291-1001", "EAC.HK", "抽獎", "遊戲規則", "得獎",
    "食安中心提提你", "升級有型食屍", "加入升級更有型", "金管局", "防騙"
]

# 5. 商台台呼、節目宣傳與熱線
STATION_PROMOS = [
    "1872903", "叱咤903", "商業二台", "雷霆881", "即上各大音樂平台收聽", "戀愛信號",
    "即拆903", "拆903", "即拆 903", "音樂平台", "平台收聽", "即相隔", "即上各大",
    "有誰共鳴", "有稅共鳴", "引發你的共鳴", "50881903", "中銀香港理財", "一切從音樂開始",
    "LIFE 音樂會", "LIFE音樂會", "woodie", "woodby", "即刻學堂", "即參學彈",
    "寫一秒", "新一秒", "七九零三", "即七九零三", "色測903", "釋測903", "釋測九零三", "跑高山", "爬下山", "借一秒", "者1秒",
    "王先生想你", "在晴朗的一天出發", "晴朗", "林海峰", "軟紙劫", "阮子健", "道格小島",
    "Pay for the day", "為你親自打點", "同年的自己", "港鐵接軌", "貫通地上地下", "以即時新聞"
]

# 6. 商業特約廣告詞庫
AD_KEYWORDS = [
    "查詢", "詳情", "致電", "熱線", "登記", "立即致電", "優惠", "折扣", "送完即止",
    "條款及細則", "受條款及細則約束", "冠名贊助", "特約贊助", "全力贊助", "贊助播出", "節目贊助", "鳴謝贊助",
    "限時優惠", "現正發售", "門票現正", "公開發售", "快達票", "城網", "撲飛",
    "免費體驗", "立即登記", "電話查詢", "歡迎致電", "借定唔借", "還得到先好借",
    "儲蓄保險", "人壽保險", "定期存款", "年利率", "信用卡", "現金回贈",
    "立即下載", "應用程式", "各大門市", "各大分店", "各大專櫃", "專門店", "門市", "分店",
    "請瀏覽", "詳情請", "歡迎查詢", "網址", "dot com", ".com", ".hk",
    "用心為您", "伴你同行", "專業之選", "為你守護", "帶給您", "生活更精彩", "未來戰士", "未來展示",
    "設計站", "加規站", "車價", "低利率", "BMW", "B&W", "大廣告", "月餅", "美心",
    "要衝衝衝", "衝出身心健康",
    "AIA", "AIA+", "健康生活節", "身心健康", "麥當勞", "Donald M", "麥樂雞", "陌落計", "雞腿包", "Supreme Plus", "每公升", "加碼至",
    "駕駛學校", "駕駛改進課程", "肯倉", "二三四一一三二二", "公共服務車輛", "追返三分", "沒有三分", "扣分", "扣三分", "Shell", "V Power",
    "中銀香港理財", "城市售票網明日公開發售", "Chester 2", "恒基物業",
    "高光時刻", "周殷廷", "演唱會", "售票網", "城市售票網", "公開發售", "私隱專員", "私隱學堂", "個人資料", "香港驗車", "驗車公司",
    "全新單曲", "各大音樂平台", "美斯", "告別信",
    "譚仔", "米線", "譚仔雲南米線", "髮再生", "發在身", "護髮專家", "兒童癌病基金", "捐出5元", "專鎖快", "跨境付款"
]

# 7. 節目專屬 Jingle / 主持關鍵字（重點保護，絕不可切！）
PROGRAM_JINGLE_KEYWORDS = [
    "bad girl", "大過佬", "來大笑代替上路", "完美阿正", "笑爆嘴", "elsie", "alsie", "l.c", "小姐, l.c",
    "你公司最討厭", "最討厭的甚麼", "最討厭的是甚麼", "帶過老", "大過老", "代替上路",
    "大明星殺手", "即日上映", "大哥, 人生當中", "人生當中", "最悲的事", "最悲嘅事", "最悲", "早點睡吧", "戴眼鏡",
    "能力測試", "探職員", "不要叫我大哥", "叫我怕你",
    "cls", "CLS", "水樓cls", "客席嘉賓", "客戚嘉賓",
    "最後一個pose", "最後一個姿勢", "say with me", "最後一條reels", "最後一條", "睇完呢個一定係", "睇完呢個", "reels", "reelser", "最後一個", "出發手勢",
    "街仔好人", "街仔", "好人嘅時間", "好人的時間",
    "有人拐住你", "有人gua住你", "有人刮住你", "有人掛住你", "你知道不知道有人",
    "你拍拖嘅時候", "你拍拖的時候", "阿正 你拍拖", "阿鄭,你拍拖", "拍拖的時候最討厭", "拍拖嘅時候最討厭",
    "阿正,你拍拖", "我都冇男朋友", "我也沒有男朋友",
    "聖艾粒", "lalalala", "少爺占", "當奴", "艾粒",
    "邊個痴線", "邊個正常", "你改變唔到", "唯有自己啦啦啦啦", "啦啦啦啦",
    "Jacky", "jacky", "突擊測驗", "每日一問", "突擊貪業",
    "美術學會", "有冇共鳴", "有冇共鳴?", "你Sing我都Sing", "你sing我sing",
    "明星插班生", "明星導航員", "寶寶很苦",
    "ILUB TV", "晚間劇場", "代課老師", "心水清大師", "心水清",
    "豪子", "阿豪", "占爺", "當奴哥",
    "鼓仔", "聽眾的鼓仔", "分享聽眾", "收到的鼓仔", "你不懂,我懂", "你不懂我懂", "你不懂，我懂",
    "sing a lup", "la la la la", "去蛇王", "辣辣辣", "阿佔", "太陽都落山", "未睡得覺", "四個人甘丁爽", "辣辣青絲",
    "ai 豪子", "ai豪子", "黃埔", "黃埔天地"
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


def run_whisper_transcription(wav_file, model_path=None, json_base_path=None, show_code="bgog"):
    """調用 whisper-cli 搭配 Metal GPU 執行粵語/中文語音辨識 (支援逐字稿快取與節目專屬詞庫提示)"""
    if model_path is None:
        model_path = DEFAULT_MODEL
    if json_base_path is None:
        json_base_path = os.path.splitext(wav_file)[0]

    json_file = f"{json_base_path}.json"
    if os.path.exists(json_file):
        print(f"⚡ 發現現有逐字稿快取: {json_file}，略過 Whisper 轉錄直接載入！")
    else:
        if show_code == "ilub":
            whisper_prompt = (
                "商業電台 叱咤903 聖艾粒LaLaLaLa 少爺占 當奴 Jacky 豪子 "
                "邊個痴線 邊個正常 唯有自己啦啦啦啦 Jacky突擊測驗 美術學會 有冇共鳴 "
                "你Sing我都Sing 明星插班生 明星導航員 寶寶很苦 ILUB TV 晚間劇場 心水清大師 "
                "黃埔天地 AI豪子 聽眾鼓仔 有誰共鳴 新聞報道 馬路的事 交通消息 天氣預測"
            )
        else:
            whisper_prompt = (
                "商業電台 叱咤903 Bad Girl 大過佬 森美 阿正 Elsie 來大笑代替上路 "
                "吹水未夠班 今日手機碌多咗 十句講曬 粒粒皆辛苦 大明星殺手 衰佬CLS 雞仔好人 "
                "Everyday is Sunday Everyday is 新day 你知道唔知道有人掛住你 "
                "有誰共鳴 新聞報道 交通消息 天氣預測"
            )
        cmd = [
            WHISPER_BIN,
            "-m", model_path,
            "-l", "zh",
            "-mc", "0",
            "--prompt", whisper_prompt,
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
        # 嚴格限制：新聞只會出現在整點（50~65 分鐘 / 3000s~3900s，以及節目結尾 110~116 分鐘 / 6600s~6960s）
        is_in_news_window = (3000.0 <= f_sec <= 3900.0) or (f_sec >= 6600.0)
        is_news_start = is_in_news_window and ("商業電台新聞" in text or "報道新聞" in text or "現在由" in text or "報導新聞" in text or ("新聞" in text and not any(k in text for k in NEWS_END_KEYWORDS)) or (f_sec >= 6780.0 and any(k in text for k in ["12點", "19點", "十二點", "十九點", "驗車", "香港驗車"])))
        is_news_end = is_in_news_window and any(k in text for k in NEWS_END_KEYWORDS)

        if (is_news_start or is_news_end) and not in_break:
            in_break = True
            break_type = "整點新聞破口"
            start_cut = f_sec
            if f_sec >= 6700.0:
                # 節目結尾：尋找最後的主持人告別語（如「拜拜」、「下個禮拜再見」、「下星期見」、「聽日見」），切口精準落在告別語之後
                for p in range(idx - 1, max(-1, idx - 80), -1):
                    p_seg = segments[p]
                    p_text = p_seg.get("text", "")
                    p_to = time_offset + (p_seg["offsets"]["to"] / 1000.0 if "offsets" in p_seg else p_seg.get("end", 0.0))
                    if any(w in p_text for w in ["拜拜", "再見", "下個禮拜", "下星期", "聽日見", "明日見", "明天見"]):
                        start_cut = p_to + 0.5
                        break
            else:
                for p in range(idx - 1, max(-1, idx - 80), -1):
                    p_seg = segments[p]
                    p_text = p_seg.get("text", "")
                    p_to = time_offset + (p_seg["offsets"]["to"] / 1000.0 if "offsets" in p_seg else p_seg.get("end", 0.0))
                    p_from = time_offset + (p_seg["offsets"]["from"] / 1000.0 if "offsets" in p_seg else p_seg.get("start", 0.0))
                    prev_to = time_offset + (segments[p-1]["offsets"]["to"] / 1000.0 if "offsets" in segments[p-1] else segments[p-1].get("end", 0.0)) if p > 0 else 0

                    # 1. 若整點前有明顯對話空檔 (>3s) 且落在 2980s~3250s，主持人往往在空檔前已結束對話
                    if (p_from - prev_to > 3.0) and (2980.0 <= prev_to <= 3250.0):
                        start_cut = prev_to + 0.5
                    # 2. 報時字眼
                    elif any(k in p_text for k in TIME_ANNOUNCEMENT_KEYWORDS):
                        start_cut = p_to + 0.2
                    # 3. 廣告或宣傳聲帶
                    elif any(k in p_text for k in STATION_PROMOS + AD_KEYWORDS + PSA_KEYWORDS):
                        start_cut = p_from
            break_start = start_cut
            print(f"  🛑 [引擎1] 整點新聞破口開始於: {timedelta(seconds=int(break_start))} ({break_start:.2f}s) [觸發: {text[:25]}]")
            continue

        # 2. 檢查半點廣告破口 (Half-hour :30 break，需排除逢星期X、由X點至X點等節目宣傳)
        is_schedule = any(w in text for w in ["星期", "逢", "至", "由", "到", "節目", "收聽"])
        is_half_hour = any(k in text for k in HALF_HOUR_KEYWORDS) and not is_schedule
        # 破口 #1 彈性窗口（10:30 / 17:30，通常落在 1300s ~ 1650s）：若即使沒報時，但出現開頭廣告台呼或演唱會/公署廣告，亦自動啟動半點破口
        is_break1_window = (1300.0 <= f_sec <= 1650.0)
        is_break1_promo = is_break1_window and any(k in text for k in ["新1秒", "新一秒", "者1秒", "即是903", "世責903", "色測903", "即是九零三", "Chester", "肯倉", "恒基", "演唱會", "售票網", "公開發售", "公署", "私隱"])
        # 破口 #1B 特約環節結束後中場廣告與宣傳（約落在 2140s ~ 2330s 或 6550s ~ 6660s）
        is_mid_promo = ((2140.0 <= f_sec <= 2330.0) or (6550.0 <= f_sec <= 6660.0)) and any(k in text for k in ["高光時刻", "高官時刻", "中銀香港", "情情踏踏", "小小事", "思浪", "少少的廣告", "一點廣告"])

        if (is_half_hour or is_break1_promo or is_mid_promo) and not in_break:
            in_break = True
            break_type = "半點廣告破口"
            break_start = f_sec if (is_break1_promo or is_mid_promo) else t_sec + 0.2
            print(f"  🛑 [引擎1] 半點廣告破口開始於: {timedelta(seconds=int(break_start))} ({break_start:.2f}s) [報時/廣告: {text[:25]}]")
            continue

        # 3. 檢查破口是否遇到節目專屬 Jingle / Bumper 重開
        if in_break:
            is_jingle = any(kw.lower() in text.lower() for kw in PROGRAM_JINGLE_KEYWORDS)
            # 安全機制：若超過 900 秒仍未遇到 Jingle，強制截斷破口
            if is_jingle or (f_sec - break_start > 900.0):
                in_break = False
                end_cut = max(break_start + 5.0, f_sec - 1.5)
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
            c_s = 0.0 if b["start"] <= 50.0 else max(0.0, b["start"] - 0.5)
            c_e = min(total_duration, b["end"] + 0.5)
            if c_e > c_s + 3.0:
                cuts.append({
                    "start": round(c_s, 2),
                    "end": round(c_e, 2),
                    "duration": round(c_e - c_s, 2),
                    "sample_text": b["segments"][0]["text"][:30]
                })

    # 合併與去重所有 cuts（凡是兩段切除之間的間隔 <= 20.0 秒之非節目碎片，一律合併消除跳切與孤島碎片）
    cuts.sort(key=lambda x: x["start"])
    merged_cuts = []
    for c in cuts:
        if not merged_cuts:
            merged_cuts.append(c)
        else:
            prev = merged_cuts[-1]
            if c["start"] - prev["end"] <= 20.0:
                prev["end"] = max(prev["end"], c["end"])
                prev["duration"] = round(prev["end"] - prev["start"], 2)
            else:
                merged_cuts.append(c)

    return tagged, merged_cuts


def get_gemini_api_key():
    """從環境變數或 .env 檔案中取得 GEMINI_API_KEY"""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            return val
        except Exception:
            pass
    return None


# Whisper 常見音樂/靜音幻覺詞庫（統一轉換為 [音樂 / 流行歌曲播放中]）
HALLUCINATION_PATTERNS = [
    "請不吝點贊", "请不吝点赞", "優優獨播劇場", "优优独播剧场", "Zither Harp",
    "點贊 訂閱", "点赞 订阅", "明鏡與點點", "明镜与点点", "明鏡需要", "明镜需要",
    "歡迎收看訂閱", "欢迎收看订阅", "YoYo Television", "字幕志愿者", "字幕志願者",
    "中文字幕", "感謝觀看", "感谢观看", "謝謝觀看", "谢谢观看", "Amara.org"
]

# 候選 Gemini 旗艦 Flash 模型池（嚴禁任何 Lite 模型，依穩定度排序並支援健康度感知輪替）
DEFAULT_GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.6-flash"
]
GEMINI_STATE_FILE = os.path.join(SCRIPT_DIR, ".gemini_model_state.json")


def get_gemini_model_candidates():
    """取得模型池列表，支援從環境變數 GEMINI_MODELS 覆寫，並強制過濾剔除所有 Lite 模型"""
    env_models = os.environ.get("GEMINI_MODELS")
    if env_models:
        raw_models = [m.strip() for m in env_models.split(",") if m.strip()]
    else:
        raw_models = list(DEFAULT_GEMINI_MODELS)
    # 嚴格禁制：任何包含 'lite' 的模型一律剔除，確保只有全尺寸上下文旗艦模型參與分析
    safe_models = [m for m in raw_models if "lite" not in m.lower()]
    return safe_models if safe_models else list(DEFAULT_GEMINI_MODELS)


def get_next_gemini_models_sequence():
    """
    實施健康度感知輪流輪替（Health-Aware Round-Robin）：
    根據 .gemini_model_state.json 記錄的上次使用模型索引與連續失敗次數，
    健康模型優先進行輪換，近期連續發生 503/Timeout 的模型自動降級排至隊尾，
    兼顧 API Quota 均衡分配與極速成功響應。
    """
    models = get_gemini_model_candidates()
    last_idx = -1
    state = {}
    if os.path.exists(GEMINI_STATE_FILE):
        try:
            with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_idx = state.get("last_index", -1)
        except Exception:
            state = {}

    start_idx = (last_idx + 1) % len(models)
    ordered_sequence = models[start_idx:] + models[:start_idx]
    stats = state.get("stats", {})
    # 將近期連續錯誤 >= 2 次的模型排到健康模型之後
    ordered_sequence.sort(key=lambda m: (
        stats.get(m, {}).get("consecutive_errors", 0) >= 2,
        stats.get(m, {}).get("consecutive_errors", 0)
    ))
    return ordered_sequence, start_idx, state


def record_gemini_model_success(model_name, model_index):
    """記錄本次成功使用的模型至持久化狀態檔（重置連續失敗計數）"""
    try:
        state = {}
        if os.path.exists(GEMINI_STATE_FILE):
            try:
                with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        state["last_used_model"] = model_name
        state["last_index"] = model_index
        state["last_success_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        stats = state.setdefault("stats", {})
        m_stat = stats.setdefault(model_name, {"success": 0, "errors": 0, "consecutive_errors": 0})
        m_stat["success"] += 1
        m_stat["consecutive_errors"] = 0
        with open(GEMINI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 無法寫入 Gemini 狀態檔案: {e}")


def record_gemini_model_error(model_name, err_msg):
    """記錄模型調用錯誤統計與連續錯誤次數"""
    try:
        state = {}
        if os.path.exists(GEMINI_STATE_FILE):
            try:
                with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        stats = state.setdefault("stats", {})
        m_stat = stats.setdefault(model_name, {"success": 0, "errors": 0, "consecutive_errors": 0})
        m_stat["errors"] += 1
        m_stat["consecutive_errors"] = m_stat.get("consecutive_errors", 0) + 1
        m_stat["last_error"] = str(err_msg)[:150]
        with open(GEMINI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def derive_cuts_from_keep_intervals(keep_intervals, total_duration):
    """
    從 keep_intervals 推導需要切除的 cuts 區間
    """
    sorted_keeps = sorted(keep_intervals, key=lambda x: float(x["start"]))
    cuts = []
    cursor = 0.0
    for item in sorted_keeps:
        ks = float(item["start"])
        ke = float(item["end"])
        desc = item.get("description", "")
        if ke <= ks:
            continue
        if ks > cursor + 1.0:
            cuts.append({
                "start": round(cursor, 2),
                "end": round(ks, 2),
                "duration": round(ks - cursor, 2),
                "sample_text": f"AI語意破口 (銜接: {desc[:25]})"
            })
        cursor = max(cursor, ke)

    if cursor < total_duration - 1.0:
        cuts.append({
            "start": round(cursor, 2),
            "end": round(total_duration, 2),
            "duration": round(total_duration - cursor, 2),
            "sample_text": "AI語意結尾新聞破口"
        })

    return cuts


def inspect_and_verify_cut_quality(keep_intervals, cuts, total_duration, transcript_segments, show_code="bgog", engine_used="gemini"):
    """
    【自動品質品檢門禁與自癒系統 (Quality Gatekeeper & Self-Healing Engine)】
    在剪輯產出音檔前，進行嚴格自我檢查與自癒修正（Self-Verification & Self-Healing）：
    0. 邊界精準對齊自癒 (Boundary End-Snapping)：自動修正 AI 誤填行首時間戳 (t0) 導致切掉最末句歌詞/報歌名的問題。
    1. 空白缺口掃描與正片自癒 (Context-Verified Gap Self-Healing)：挽救遺漏的半點/中段正片（如 Everyday is 新day、Part 2a），並嚴格過濾其他節目宣傳片（如晴朗、主子主場館）。
    2. 時鐘結構與歌曲保護門禁 (Clock Structure & Music Anchors)：保護開場前奏、半點流行歌、整點前話題與結尾片尾曲（防誤吞片尾商業廣告）。
    3. 純淨時長門禁 (Clean Duration Gate) 與 引擎門禁 (Engine Gate)。
    
    返回: (passed: bool, healed_keeps: list, final_cuts: list, report: dict)
    """
    healed_keeps = [dict(k) for k in keep_intervals]
    healed_keeps.sort(key=lambda x: float(x["start"]))
    healed_items = []
    checks = {}
    failure_reasons = []

    is_2hr_show = (total_duration >= 6400.0)
    is_ilub = (show_code == "ilub")

    p1_break_keywords = ["五點半", "5點半", "17點半", "17時半", "大作", "突擊", "測驗", "廣告"] if is_ilub else ["Halloween", "萬聖節", "大過佬", "十點半", "10點半", "10時半", "得獎", "WhatsApp", "廣告"]
    p4_news_keywords = ["新聞報道", "新聞報導", "報道新聞", "報導新聞", "商業電台新聞", "七點", "7點", "十九點", "19點", "行政長官", "特區政府"] if is_ilub else ["新聞報道", "新聞報導", "報道新聞", "報導新聞", "商業電台新聞", "十二點", "12點", "行政長官", "特區政府"]
    gap_host_keywords = [
        "少爺占", "當奴", "阿占", "艾粒", "豪子", "AI豪子", "AI 豪子", "ai豪子", "ai 豪子", "我是AI",
        "Jacky", "jacky", "突擊測驗", "突擊貪業", "每日一問", "鼓仔", "黃埔", "sing a lup", "lalala",
        "有冇共鳴", "沒有共鳴", "寶寶很苦", "明星插班生", "明星導航員", "晚間劇場", "心水清", "你sing", "你Sing",
        "邊個痴線", "唯有自己", "啦啦啦啦"
    ] if is_ilub else [
        "Bad Girl", "大過佬", "森美", "阿正", "Elsie", "妹頭", "吹水未夠班", "手機碌多咗", "十句講曬",
        "衰佬", "雞仔好人", "街仔好人", "新day", "Everyday is Sunday", "Everyday is", "Monday Tuesday",
        "今天有什麼新", "掛住你", "來大笑代替上路", "粒粒皆辛苦"
    ]
    gap_news_keywords = ["18點", "18:30", "19點", "六點", "七點", "5時半", "五點半", "六點半"] if is_ilub else ["11點", "11:30", "12點", "10時半", "十點半", "十一點半"]

    promo_block_markers = [
        "在晴朗的一天", "再晴郎的一天", "再晴朗的一天", "林海峰", "主子主場館", "度假小島", "雙人套房",
        "903FORSTAR", "All-Star", "籃球賽", "男童賽", "古天樂", "古天駱", "歌星勇士隊", "世界航空",
        "跳島遊", "跳渡游", "有誰共鳴", "有谁共鸣", "此時此刻", "此时此刻", "50881903", "50-881-903",
        "專業推介", "专业推介", "新上榜", "三甲歌", "三甲位置", "冠軍作品", "冠军作品", "Good morning",
        "晚上九至十", "同你流汗", "體育資訊"
    ]
    outro_ad_markers = [
        "喜療駝", "藥膏", "痛腳", "雙人套房", "小中瓶", "精華水", "綠色殯葬", "綠色笨藏",
        "食環署", "食環柱", "本篇兒童不宜", "本片兒童不宜", "本片青少年", "我爹想旅行", "我爹上旅行"
    ]
    gap_stop_words = set(
        AD_KEYWORDS + TRAFFIC_KEYWORDS + WEATHER_KEYWORDS + NEWS_START_KEYWORDS +
        STATION_PROMOS + list(gap_news_keywords) + promo_block_markers + outro_ad_markers + [
            "天氣預測", "天氣預算", "流感", "皇家加勒比", "十方法師", "萬寧", "護髮", "打針", "防流感",
            "青衣城", "清衣城", "國際歌迷會", "各大音樂平台", "輸血服務中心", "紅十字會", "通訊局", "coms-auth"
        ]
    )

    # -------------------------------------------------------------
    # 檢查項 0：邊界精準對齊自癒 (Boundary End-Snapping)
    # -------------------------------------------------------------
    song_announce_words = ["演唱", "這首歌", "給你這首", "给你手作品", "個人作品", "聽過有", "听过有", "[音樂 / 流行歌曲播放中]"]
    for k in healed_keeps:
        k_end = float(k["end"])
        for seg in transcript_segments:
            s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
            s_to = seg.get("offsets", {}).get("to", 0) / 1000.0 if "offsets" in seg else seg.get("end", 0.0)
            s_txt = seg.get("text", "").strip()
            if abs(s_from - k_end) <= 1.5 and s_to > k_end + 0.5:
                is_music_or_announce = (
                    any(p in s_txt for p in HALLUCINATION_PATTERNS) or
                    any(w in s_txt for w in song_announce_words)
                )
                if is_music_or_announce and not any(w in s_txt for w in gap_stop_words):
                    healed_items.append(f"段落結尾對齊：由 {k_end:.1f}s 延伸至行尾 {s_to:.1f}s (完整保留結尾音樂/報歌名)")
                    k["end"] = round(s_to, 2)
                    break

    # -------------------------------------------------------------
    # 檢查項 0.5：幻覺假破口自動縫合 (False Mid-Part Split Self-Healing)
    # 若 AI 將同一時段拆成 a/b（缺口 <= 280s），但缺口內完全沒有任何商業廣告/交通/天氣/新聞/台呼，
    # 代表 AI 誤將主持問答（如突擊測驗、嘉賓專訪）當成廣告切走，立即自動無縫縫合！
    # -------------------------------------------------------------
    if len(healed_keeps) >= 2:
        healed_keeps.sort(key=lambda x: float(x["start"]))
        merged_keeps = [healed_keeps[0]]
        for nxt in healed_keeps[1:]:
            prev = merged_keeps[-1]
            p_end = float(prev["end"])
            n_start = float(nxt["start"])
            gap = n_start - p_end
            if 0.0 < gap <= 280.0:
                seg_in_gap = [
                    s for s in transcript_segments
                    if p_end <= (s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)) < n_start
                ]
                has_real_break = any(
                    any(w in s.get("text", "") for w in gap_stop_words)
                    for s in seg_in_gap
                )
                if not has_real_break and len(seg_in_gap) >= 3:
                    healed_items.append(f"假破口自動縫合：合併被誤切的正片區間 {p_end:.1f}s~{n_start:.1f}s (挽回 {gap:.1f}s 連續主持對話/問答)")
                    prev["end"] = float(nxt["end"])
                    prev["description"] = f"{prev.get('description', '')} + {nxt.get('description', '')}".strip(" +")
                    continue
            merged_keeps.append(nxt)
        healed_keeps = merged_keeps

    # -------------------------------------------------------------
    # 檢查項 1：空白缺口掃描與正片自癒 (Context-Verified Gap Self-Healing)
    # （優先於連續性檢查執行，確保挽回後的段落納入時鐘評估）
    # -------------------------------------------------------------
    if is_2hr_show and len(healed_keeps) >= 2:
        healed_keeps.sort(key=lambda x: float(x["start"]))
        additional_rescues = []
        for i in range(len(healed_keeps) - 1):
            curr_end = float(healed_keeps[i]["end"])
            next_start = float(healed_keeps[i+1]["start"])
            gap = next_start - curr_end
            # 檢查前半場或後半場內是否有異常巨大空白 (> 240s)
            is_p2a_gap = (1250.0 <= curr_end <= 1550.0 and next_start >= 1850.0 and gap > 300.0)
            is_p2_gap = (1700.0 <= curr_end <= 2950.0 and next_start >= 3400.0 and gap > 240.0)
            is_p3_gap = (3650.0 <= curr_end <= 4880.0 and next_start >= 5150.0 and gap > 240.0)

            if is_p2a_gap or is_p2_gap or is_p3_gap:
                target_part = "Part 2a" if is_p2a_gap else ("Part 2b" if is_p2_gap else "Part 3b")
                seg_in_gap = [
                    s for s in transcript_segments
                    if curr_end <= (s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)) <= next_start
                ]
                found_start = None
                found_end = None
                promo_until = -1.0
                for s in seg_in_gap:
                    s_f = s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)
                    s_t = s.get("offsets", {}).get("to", 0)/1000.0 if "offsets" in s else s.get("end", 0.0)
                    s_txt = s.get("text", "")
                    # 若遇到其他節目宣傳片或榜單/公益聲帶標題，鎖定後續 75 秒禁止觸發正片起點
                    if any(pm in s_txt for pm in promo_block_markers):
                        promo_until = max(promo_until, s_f + 75.0)
                        if found_start is not None:
                            break
                        continue
                    if s_f < promo_until:
                        continue
                    if any(k in s_txt for k in gap_host_keywords) and not any(w in s_txt for w in gap_stop_words):
                        if found_start is None:
                            found_start = s_f
                    if found_start is not None:
                        if any(k in s_txt for k in gap_stop_words):
                            break
                        found_end = s_t

                if found_start and found_end and (found_end - found_start > 50.0):
                    # 二次驗證：確認挽回區間內不包含其他節目宣傳片結尾
                    rescued_segs = [
                        s for s in seg_in_gap
                        if found_start <= (s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)) <= found_end
                    ]
                    has_promo_contamination = any(
                        any(pm in s.get("text", "") for pm in promo_block_markers)
                        for s in rescued_segs
                    )
                    if not has_promo_contamination:
                        healed_items.append(f"空白缺口掃描自癒：在 {curr_end:.1f}s~{next_start:.1f}s 間成功挽救 {target_part} 正片話題 ({found_start:.1f}s~{found_end:.1f}s, 時長: {found_end-found_start:.1f}s)")
                        additional_rescues.append({
                            "start": round(found_start, 2),
                            "end": round(found_end, 2),
                            "description": f"品檢自癒：挽回遺漏 {target_part} 正片話題"
                        })

        if additional_rescues:
            healed_keeps.extend(additional_rescues)
            healed_keeps.sort(key=lambda x: float(x["start"]))

        # -------------------------------------------------------------
        # 檢查項 1.5：破口後正片延遲切入自癒 (Delayed Part Start Self-Healing)
        # 檢查每個大破口結尾（next_start 前 180 秒內），若最後一則廣告結束後已有單元 Jingle 或主持對話提早開始，
        # 自動將 next_start 前移對齊至單元 Jingle 真正起點（如挽回被延遲切入的【明星導航員】開場）！
        # -------------------------------------------------------------
        resume_jingle_cues = gap_host_keywords + ["明星坐在旁邊", "開車有個明星"]
        for i in range(len(healed_keeps) - 1):
            curr_end = float(healed_keeps[i]["end"])
            next_start = float(healed_keeps[i+1]["start"])
            if next_start - curr_end > 180.0:
                scan_from = max(curr_end + 60.0, next_start - 180.0)
                tail_segs = [
                    s for s in transcript_segments
                    if scan_from <= (s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)) < next_start
                ]
                last_ad_end = scan_from
                for s in tail_segs:
                    s_t = s.get("offsets", {}).get("to", 0)/1000.0 if "offsets" in s else s.get("end", 0.0)
                    s_txt = s.get("text", "")
                    if any(w in s_txt for w in gap_stop_words) or any(d in s_txt for d in ["10月", "11月", "12月", "狂歡", "換票"]):
                        last_ad_end = max(last_ad_end, s_t)
                candidate_start = None
                for s in tail_segs:
                    s_f = s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)
                    s_txt = s.get("text", "")
                    if s_f >= last_ad_end - 0.5 and any(k in s_txt for k in resume_jingle_cues):
                        candidate_start = s_f
                        break
                if candidate_start is not None and (next_start - candidate_start) >= 10.0:
                    healed_items.append(f"段落起點對齊：Part 起點由 {next_start:.1f}s 前移至 {candidate_start:.1f}s (挽回破口後提早開始之單元 Jingle 與主持開場)")
                    healed_keeps[i+1]["start"] = round(candidate_start, 2)

    # -------------------------------------------------------------
    # 檢查項 2：時鐘錨點自癒與保護 (Clock Structure & Music Anchors)
    # -------------------------------------------------------------
    if is_2hr_show and healed_keeps:
        # 2.1 Part 1 開頭主題曲前奏防延遲切入
        p1 = healed_keeps[0]
        p1_start = float(p1["start"])
        intro_jingle_cues = [
            "聖艾粒", "星崖粒", "星矮粒", "星藝粒", "啦啦啦啦", "一早起床", "公海二",
            "太陽都落山", "太陽都降山", "長毛Jackie", "长毛Jackie", "開牙齒", "开牙齿"
        ] if is_ilub else [
            "bad girl", "大過佬", "大過老", "帶過老", "來大笑代替上路", "代替上路",
            "完美阿正", "笑爆嘴", "elsie", "alsie"
        ]
        # 尋找 p1_start 之前最後一則新聞/交通/天氣/宣傳片的結束點
        last_pre_show_ad_end = 25.0
        for seg in transcript_segments:
            s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
            s_to = seg.get("offsets", {}).get("to", 0) / 1000.0 if "offsets" in seg else seg.get("end", 0.0)
            s_text = seg.get("text", "")
            if s_from < p1_start:
                if any(w in s_text for w in gap_stop_words) or any(w in s_text for w in ["王牌出場", "深夜一至二", "直播室跟大家再見面"]):
                    last_pre_show_ad_end = max(last_pre_show_ad_end, s_to)

        found_start = None
        for seg in transcript_segments:
            s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
            s_text = seg.get("text", "")
            if max(25.0, last_pre_show_ad_end) <= s_from < p1_start - 3.0:
                if any(k in s_text for k in intro_jingle_cues):
                    found_start = s_from
                    break
        if found_start is not None:
            healed_items.append(f"Part 1 起點由 {p1_start:.1f}s 前移至 {found_start:.1f}s (挽救開場主題曲前奏)")
            p1["start"] = round(found_start, 2)
            p1_start = float(p1["start"])

        if p1_start <= 500.0:
            checks["part1_intro"] = f"PASS (起點: {p1_start:.1f}s)"
        else:
            checks["part1_intro"] = f"WARN (起點: {p1_start:.1f}s > 500s)"

        # 2.2 Part 1 結束時間防提前切歌保護 (10:30 或 17:30 半點前流行歌)
        p1_end = float(p1["end"])
        if p1_end < 1250.0:
            new_p1_end = p1_end
            for seg in transcript_segments:
                s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
                s_to = seg.get("offsets", {}).get("to", 0) / 1000.0 if "offsets" in seg else seg.get("end", 0.0)
                s_text = seg.get("text", "")
                if 1250.0 <= s_from <= 1450.0:
                    if any(k in s_text for k in p1_break_keywords):
                        new_p1_end = round(s_to + 0.5, 2)
                    elif s_from > new_p1_end and not any(k in s_text for k in gap_stop_words):
                        new_p1_end = round(s_to, 2)
            if new_p1_end > p1_end:
                half_label = "17:30" if is_ilub else "10:30"
                healed_items.append(f"Part 1 結束點由 {p1_end:.1f}s 延伸至 {new_p1_end:.1f}s (挽救 {half_label} 半點前流行歌)")
                p1["end"] = new_p1_end
                checks["part1_pop_song"] = f"PASS (自癒延伸至: {p1['end']}s >= 1250s)"
            else:
                checks["part1_pop_song"] = f"WARN (結束點: {p1_end:.1f}s < 1250s)"
        else:
            checks["part1_pop_song"] = f"PASS (結束點: {p1_end:.1f}s >= 1250s)"

        # 2.3 Part 2 結束點檢驗 (11:00 或 18:00 整點新聞前主持對話)
        h1_keeps = [k for k in healed_keeps if float(k["start"]) < 3500.0]
        if h1_keeps:
            last_h1_end = max(float(k["end"]) for k in h1_keeps)
            h1_min_end = 3100.0
            if last_h1_end < h1_min_end:
                checks["part2_continuity"] = f"WARN (第一小時最後結束於: {last_h1_end:.1f}s < {int(h1_min_end)}s)"
            else:
                checks["part2_continuity"] = f"PASS (第一小時最後結束於: {last_h1_end:.1f}s >= {int(h1_min_end)}s)"
        else:
            checks["part2_continuity"] = "FAIL (缺少第一小時下半段)"

        # 2.4 Part 3 結束點檢驗 (11:30 或 18:30 半點前單元遊戲)
        h2_first_keeps = [k for k in healed_keeps if 3400.0 <= float(k["start"]) < 5200.0]
        if h2_first_keeps:
            last_h2_first_end = max(float(k["end"]) for k in h2_first_keeps)
            if last_h2_first_end < 4950.0:
                checks["part3_continuity"] = f"WARN (第二小時上半段結束於: {last_h2_first_end:.1f}s < 4950s)"
            else:
                checks["part3_continuity"] = f"PASS (第二小時上半段結束於: {last_h2_first_end:.1f}s >= 4950s)"
        else:
            checks["part3_continuity"] = "FAIL (缺少第二小時上半段)"

        # 2.5 Part 4 結尾片尾曲防提前切歌保護 (12:00 或 19:00 整點新聞前，含 60s 前瞻防誤吞片尾劇場式廣告/宣傳)
        p_last = healed_keeps[-1]
        last_end = float(p_last["end"])
        new_last_end = last_end
        if last_end < 6680.0:
            for idx_s, seg in enumerate(transcript_segments):
                s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
                s_to = seg.get("offsets", {}).get("to", 0) / 1000.0 if "offsets" in seg else seg.get("end", 0.0)
                s_text = seg.get("text", "").strip()
                if s_from < last_end - 0.5:
                    continue
                if s_from > 6840.0:
                    break
                if any(k in s_text for k in p4_news_keywords) or any(w in s_text for w in gap_stop_words):
                    break
                # 60 秒前瞻檢查：若當前為非音樂之口語對話，且 60 秒內出現節目宣傳或廣告詞，代表已進入片尾劇場廣告，立即停止！
                is_music_seg = any(p in s_text for p in HALLUCINATION_PATTERNS) or any(w in s_text for w in song_announce_words)
                if not is_music_seg:
                    lookahead_ad = False
                    for fut in transcript_segments[idx_s+1:]:
                        f_from = fut.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in fut else fut.get("start", 0.0)
                        if f_from > s_from + 60.0:
                            break
                        f_txt = fut.get("text", "")
                        if any(w in f_txt for w in outro_ad_markers + promo_block_markers + p4_news_keywords):
                            lookahead_ad = True
                            break
                    if lookahead_ad:
                        break
                new_last_end = round(s_to, 2)

        if new_last_end > last_end + 1.0:
            healed_items.append(f"Part 4 結尾由 {last_end:.1f}s 安全延伸至 {new_last_end:.1f}s (挽救節目完結片尾曲且避開後續廣告)")
            p_last["end"] = new_last_end
            last_end = new_last_end

        if last_end >= 6680.0:
            checks["part4_outro_song"] = f"PASS (結束於: {last_end:.1f}s >= 6680s)"
        else:
            checks["part4_outro_song"] = f"WARN (結束於: {last_end:.1f}s < 6680s)"

    # 計算自癒後純淨時長
    clean_duration = sum(float(k["end"]) - float(k["start"]) for k in healed_keeps)
    clean_dur_str = f"{int(clean_duration//3600):02d}:{int((clean_duration%3600)//60):02d}:{int(clean_duration%60):02d}"

    # -------------------------------------------------------------
    # 檢查項 3：時長門禁 (Clean Duration Gate)
    # -------------------------------------------------------------
    if is_2hr_show:
        min_clean_dur = 4260.0 if is_ilub else 4440.0
        max_clean_dur = 5280.0 if is_ilub else 5400.0
        min_str = "01:11:00" if is_ilub else "01:14:00"
        max_str = "01:28:00" if is_ilub else "01:30:00"
        if clean_duration < min_clean_dur:
            checks["duration_gate"] = f"FAIL (純淨時長 {clean_dur_str} 過短 < {min_str}，疑似遺漏大段主持話題)"
            failure_reasons.append(f"純淨時長過短 ({clean_dur_str} < {min_str})")
        elif clean_duration > max_clean_dur:
            checks["duration_gate"] = f"FAIL (純淨時長 {clean_dur_str} 超出上限 > {max_str}，廣告未切除乾淨)"
            failure_reasons.append(f"純淨時長超出上限 ({clean_dur_str} > {max_str})")
        else:
            checks["duration_gate"] = f"PASS (純淨時長: {clean_dur_str}，符合 {min_str}~{max_str} 標準區間)"
    else:
        checks["duration_gate"] = f"PASS (非標準2小時錄音，純淨時長: {clean_dur_str})"

    # -------------------------------------------------------------
    # 檢查項 4：引擎門禁 (Engine Gate)
    # -------------------------------------------------------------
    if "heuristic" in engine_used.lower() and clean_duration > 5280.0:
        checks["engine_gate"] = f"FAIL (啟發式規則引擎產出時長 {clean_dur_str} 超過 88 分鐘，廣告未切淨，嚴禁放行)"
        failure_reasons.append("啟發式引擎切除不完全，時長超標")
    else:
        checks["engine_gate"] = f"PASS (採用引擎: {engine_used})"

    # 推導最終切除區間
    final_cuts = derive_cuts_from_keep_intervals(healed_keeps, total_duration)

    passed = (len(failure_reasons) == 0)

    # 評分機制
    score = 100
    if not passed:
        score -= 50
    for k, v in checks.items():
        if "WARN" in v:
            score -= 10
        elif "FAIL" in v:
            score -= 25
    score = max(0, score)

    report = {
        "passed": passed,
        "score": score,
        "clean_duration_seconds": round(clean_duration, 2),
        "clean_duration_formatted": clean_dur_str,
        "total_intervals": len(healed_keeps),
        "total_cuts": len(final_cuts),
        "engine_used": engine_used,
        "checks": checks,
        "healed_items": healed_items,
        "failure_reasons": failure_reasons
    }

    return passed, healed_keeps, final_cuts, report


def detect_ad_intervals_with_gemini(transcript_segments, total_duration, show_code="bgog", api_key=None, model=None, date_str=None):
    """
    調用 Google Gemini Flash 雲端大模型進行前文後理（上下文）全篇語意理解與去廣告區間劃定。
    支援健康度感知輪流使用（Health-Aware Round-Robin）與自動容錯轉移（Multi-Model Sequential Failover）。
    """
    if not api_key:
        api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("未提供有效的 GEMINI_API_KEY")

    lines = []
    for seg in transcript_segments:
        if "offsets" in seg:
            t0 = seg["offsets"].get("from", 0) / 1000.0
            t1 = seg["offsets"].get("to", 0) / 1000.0
        else:
            t0 = seg.get("start", 0.0)
            t1 = seg.get("end", t0)
        text = seg.get("text", "").strip()
        if text:
            for pat in HALLUCINATION_PATTERNS:
                if pat in text:
                    text = "[音樂 / 流行歌曲播放中]"
                    break
            m, s = int(t0 // 60), int(t0 % 60)
            lines.append(f"[{m:02d}:{s:02d} ({t0:.1f}s -> {t1:.1f}s)] {text}")

    transcript_text = "\n".join(lines)

    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_hint = ""
    if date_str:
        try:
            clean_d = re.sub(r'[^0-9]', '', str(date_str))
            if len(clean_d) >= 8:
                dt = datetime.strptime(clean_d[:8], "%Y%m%d")
                weekday_name = weekdays_cn[dt.weekday()]
                weekday_hint = f"【本集錄音播出日期】：{dt.strftime('%Y-%m-%d')}（{weekday_name}）\n請特別比對今日（{weekday_name}）對應的節目特色與單元環節！\n"
        except Exception:
            pass

    if show_code == "ilub":
        show_info = f"""{weekday_hint}目標節目：《聖艾粒LaLaLaLa》（叱咤903，主持：少爺占、當奴、Jacky、豪子）
【播出時間】：星期一至五 下午 17:00 - 19:00（黃昏 17:00 開播，18:00 六點整點新聞，19:00 七點節目結束）

【節目每週固定環節日程表（17:00 - 19:00）】：
◆ 第一小時（17:00 - 18:00）環節：
  - 【開場清談（17:00 - 17:30）】：少爺占與當奴開場閒聊、生活瑣事互寸、近期旅遊或日常爆笑經歷，中途會播放 1~2 首流行歌。
  - 【17:30 半點單元】：
    * 【Jacky 突擊測驗 / 每日一問】：監製 Jacky 或豪子每天準備一道趣味問答題考少爺占與當奴（開場聲帶常有「Jacky 突擊測驗」）。
    * 【搞邊科】：介紹潮流時尚事物、流行文化。
    * 【五點半 Jingle】：「邊個痴線 邊個正常 你改變唔到佢哋 唯有自己啦啦啦啦...」
    * 主持人接續話題閒聊與流行歌曲播放，連貫主持直到 18:00 六點鐘新聞前！

◆ 第二小時（18:00 - 19:00）環節：
  - 【18:00 - 18:30 每日主題單元】（六點新聞與交通消息結束後）：
    * 星期一【美術學會 / 拍片】：主持人出題並進行創意繪畫或短片拍攝比賽。
    * 星期二【有冇共鳴】：主持人拋出生活怪癖、尷尬經驗或觀察，互相爭辯「有冇共鳴？」。
    * 星期三【你Sing我都Sing】：少爺占與當奴唱歌詞、猜歌名遊戲，與 Facebook 聽眾直播互動。
    * 星期四【明星插班生 / 明星導航員】：邀請歌手、藝人或運動員嘉賓專訪。
    * 星期五【寶寶很苦】：聽眾電話與心聲分享，主持人爆笑開解安慰。
  - 【18:30 - 19:00 下半場單元】（18:30 交通消息與廣告結束後）：
    * 【ILUB TV 晚間劇場 / 廣播劇】：占奴經典廣播劇與角色扮演惡搞。
    * 【心水清大師】/【代課老師】。
    * 【黃埔天地 AI 豪子】及【聽眾鼓仔】：占奴與 AI 豪子的爆笑互動、聽眾投稿分享，【100% 屬於節目正片，絕對嚴禁誤殺】！
    * 主持人道別（如「聽日見、拜拜、聽日五點再見」）及之後播放的【片尾流行歌曲】，必須完整保留直到 19:00 整點新聞報時前！

【節目專屬 Jingle 聲帶】：
- 「聖艾粒」、「lalalala」、「sing a lup」、「你不懂我懂」、「四個人甘丁爽」
- 「邊個痴線 邊個正常 你改變唔到佢哋 唯有自己啦啦啦啦」（五點半 Jingle）
- 「Jacky 突擊測驗」（或突擊貪業）
- 「ILUB TV 晚間劇場」、「心水清大師」

【極度重要防誤殺原則】：
1. 尖峰時段交通消息處理：傍晚 17:00~19:00 是香港下班尖峰時間，商台會頻繁播放「馬路的事 交通消息」。交通消息聲帶本身可切除，但切口必須精確！【絕對不可將前後主持人的話題對話、開場閒聊、或拿交通消息開玩笑的言論當成廣告切除】！
2. 「黃埔 AI 豪子」與「聽眾鼓仔」：這是占奴正片固定環節，絕不是商場廣告，必須 100% 完整保留！
3. 主持人隨口笑談的商業品牌、贊助或電台活動屬於節目對話，切勿截斷！
4. 節目結尾道別後的【片尾歌曲】必須完整保留直至 19:00 整點新聞前！"""

        part_rules = """2. 【四個段落（Parts 1~4）的起止點判定規範（《聖艾粒LaLaLaLa》17:00 - 19:00）】：
   - 【Part 1（第一小時上半段，17:00 - 17:30）】：
     * 起點：開播前電台台呼/天氣/商業廣告以及前續節目宣傳結束，少爺占與當奴開場主題曲前奏音樂響起或兩人開場說話之處（約 100s~300s，切勿因前奏為純音樂而延遲切入！）。
     * 終點：17:30（五點半）商業廣告與報時開始前（通常落在 1300s~1500s / 21~25 分鐘左右）。【絕對不可在話題剛說完就提前切斷】！若主持人在話題後播了一首流行歌並繼續說話再入廣告，歌曲與對話必須完整連貫保留！
   - 【Part 2（第一小時下半段，17:30 - 18:00，可拆為 2a/2b）】：
     * 起點：半點廣告/交通消息結束後，主持人說話、「邊個痴線... 唯有自己啦啦啦啦」Jingle 或「Jacky 突擊測驗」接回之處（約 1700s 左右）。
     * 終點：【極度重要】主持人在整點前結束話題的時間各集皆不同。若中途有 1 分鐘短廣告，拆分為 2a 與 2b，後半段話題必須聊到 18:00（六點鐘）整點新聞報時前（約 3200s 左右）！只有當主持人明確結束話題進入 18:00 新聞報時，才是 Part 2 終點！
   - 【Part 3（第二小時上半段，18:00 - 18:30，可拆為 3a/3b）】：
     * 起點：【極度重要】18:00 六點新聞報道與交通消息結束後，單元專屬 Jingle（如週一【美術學會】、週二【有冇共鳴】、週三【你Sing我都Sing】、週四【明星插班生】、週五【寶寶很苦】）響起之處！
     * 終點：【極度重要】若中途有 1 分鐘短廣告，拆分為 3a 與 3b，後半段話題必須聊到 18:30 半點報時「六點半」廣告前（約 5000s 左右）！絕不可提早將整個 Part 3 截斷！
   - 【Part 4（第二小時下半段，18:30 - 19:00）】：
     * 起點：【極度重要】18:30 交通消息與廣告結束後，單元專屬 Jingle（如 ILUB TV 晚間劇場、心水清大師、黃埔 AI 豪子、聽眾鼓仔）起拍響起的瞬間！
     * 終點：【極度重要】節目最後主持人道別後，若有播放片尾曲，【片尾曲必須完整保留】直到 19:00 七點鐘整點新聞報道開始前（通常在 6800s 左右）！後續播出的商業廣告及整點新聞，【切除在 19:00 新聞報時前一瞬間】！"""

        json_format_example = """{{
  "keep_intervals": [
    {{"start": 0.0, "end": 0.0, "description": "Part 1: 開場主題曲起拍至 17:30 五點半報時與商業廣告交通前"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 2 (或 2a/2b): 17:30 半點破口後接回至 18:00 六點新聞前主持對話（若中途有短廣告請拆分切除，切勿丟棄後半段主持話題！）"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 3 (或 3a/3b): 18:00 六點新聞與交通後單元 Jingle 接回至 18:30 廣告前（若中途有短廣告請拆分切除，切勿丟棄後半段主持話題！）"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 4: 18:30 半點單元 Jingle 起拍至節目完結道別與片尾曲結束（切除 19:00 新聞）"}}
  ]
}}"""

    else:
        show_info = f"""{weekday_hint}目標節目：《Bad Girl 大過佬》（叱咤903，主持：森美、阿正、Elsie，常有代班主持如妹頭 Ransi 等人互動）
【節目每週固定環節日程表（10:00 - 12:00）】：
◆ 第一小時（10:00 - 11:00）環節：
  - 星期一【吹水未夠班】：每次有一個主題。主持會分到要分享該件事的程度（由小一至小六其一）。主持分享後大家互猜每位主持的程度。
  - 星期三【今日手機碌多咗】：每位主持會分享他們在手機找到的「廢post」（Reels、IG post、Threads等），然後由每位主持評價「廢」與否。
  - 星期四【十句講曬】：每次會由 AI 生成一個十個句子的故事，主持輪流猜是哪十句。

◆ 第二小時（11:00 - 12:00）環節：
  - 星期一【大明星殺手】：每期邀請一位明星嘉賓參與訪談與互動遊戲「射爆九宮格」——收集嘉賓近期於社交媒體發布的九張相片，由三位主持通過觀察與分析，猜嘉賓本人最偏愛其中的哪一張。
  - 星期二【衰佬CLS】：
    * 第一部分：主持人 Elsie 提供一個兩性關係矛盾案例作為討論。
    * 第二部分：接聽由聽眾所提供的親身經歷電話，廣大聽眾可以在節目社交媒體上進行投票投訴是否成立。主持人將探討兩性關係應如何採取適合措施予以回應或彌補。
  - 星期三【雞仔好人】：邀請聽眾來電介紹自己做過的好事，可以是生活中的微小好事。
  - 星期四上半場【十句講曬 / 粒粒皆辛苦】：主持玩字數接龍或情境故事遊戲（開場 Jingle:「三個農夫聊天 我們絕對不講多 不講少 粒粒皆辛苦」）。
  - 星期四下半場【Everyday is 新day】：每期主持介紹新事物，及邀請聽眾來電介紹新事物（開場 Jingle:「EVERYDAY IS SUNDAY / EVERYDAY IS 新DAY」）。
  - 星期五【你知道唔知道有人掛住你】：接聽聽眾來電，向伴侶或心儀對象傳遞平日難以言表的情感。

【節目專屬 Jingle 聲帶】：
- 「大大大大大大過佬」、「來大笑代替上路」
- 「最後一個 pose... 最後一條 reels」（下半場開場 Jingle）
- 「衰佬CLS」、「雞仔好人」（Whisper 有時可能辨識為「街仔好人」）
- 「三個農夫聊天 我們絕對不講多 不講少 粒粒皆辛苦」、「粒粒皆辛苦」（週四十句講曬 Jingle）
- 「EVERYDAY IS SUNDAY / EVERYDAY IS 新DAY」（週四新事物 Jingle）

【極度重要防誤殺原則】：
1. 嘉賓訪談與聽眾電話：上述所有環節（如星期二衰佬投訴、星期三雞仔好人、星期四新事物與十句講曬、星期五電話告白，以及嘉賓訪談）包含大量聽眾電話連線與嘉賓對話，【100% 屬於節目正片】，絕對不可誤判為廣告！
2. 兩性話題與個案投票：【衰佬CLS】提及的案例、奇怪投訴等皆為節目內容，絕非商業廣告！
3. 主持人口頭語：主持人日常隨口開玩笑或話題中提及的「買車」、「保險」、「贊助」、「轉數快」、「IG」完全屬於節目口語對話，切勿截斷！
4. 節目結尾主持人的最後道別（如「下個禮拜再見，拜拜！」）及之前的聽眾互動（如「雞仔好人」好事）屬於節目正片，不可提前切斷！"""

        part_rules = """2. 【四個段落（Parts 1~4）的起止點判定規範（《Bad Girl 大過佬》10:00 - 12:00）】：
   - 【Part 1（第一小時上半段，10:00 - 10:30）】：
     * 起點：開播前電台台呼/天氣/商業廣告以及前續節目宣傳結束，節目專屬開場主題曲前奏音樂響起之處（約 30s~240s，切勿因前奏為純音樂而延遲切入歌詞！）。
     * 終點：10:30 商業廣告與報時開始前（通常落在 1300s~1500s / 21~25 分鐘左右）。【絕對不可在 18 分鐘或話題剛說完就提前切斷】！若主持人在話題結束後播了一首流行歌（如 1080s~1280s）並繼續說話/宣布活動再入廣告，歌曲與後續對話必須完整連貫保留！
   - 【Part 2（第一小時下半段，10:30 - 11:00，可拆為 2a/2b）】：
     * 起點：半點廣告破口結束後，主持人說話或節目 Jingle 接回之處（約 1700s 左右）。
     * 終點：【極度重要】必須依據逐字稿實際語意！主持人們在整點前結束話題的時間各集皆不同。若中途有 1 分鐘廣告，拆分為 2a 與 2b，後半段話題必須聊到 11:00 整點新聞報時前（約 3200s 左右）！只有當主持人明確結束話題，隨後緊接著商業廣告或整點新聞報時之處，才是 Part 2 終點！
   - 【Part 3（第二小時上半段，11:00 - 11:30，可拆為 3a/3b）】：
     * 起點：【極度重要】整點新聞後，若主持人僅隨口說幾句隨即進入商業廣告破口，或中間有商業贊助遊戲（如換票證、電影戲票問答）、商業特約廣告及其他節目宣傳聲帶，【這些全部屬於商業破口，必須完全切除】！Part 3 起點必須精準落在第二小時正式單元專屬 Jingle（如週二【衰佬CLS】主題 Jingle「森仔 咩啊... 來大笑代替上路...」、週四【粒粒皆辛苦/十句講曬】或嘉賓專訪 Jingle）起拍響起的瞬間！
     * 終點：【極度重要】若中途有 1 分鐘短廣告，拆分為 3a 與 3b，後半段話題必須聊到 11:30 半點報時與商業廣告開始前（約 5000s 左右）！絕不可在 11:15 說完一個話題就提早將整個 Part 3 截斷！
   - 【Part 4（第二小時下半段，11:30 - 12:00）】：
     * 起點：【極度重要】半點廣告破口通常包含《此時此刻 有誰共鳴》（公益團體呼籲如捐款支持某協會）等商業/公益宣傳，【全部屬於廣告破口，必須完全切除】！Part 4 起點必須精確落在單元專屬 Jingle（如 Everyday is 新day、雞仔好人、衰佬CLS 等）起拍響起的瞬間，【絕不可延遲切入，也絕不可將前面的有誰共鳴公益聲帶保留進來】！
     * 終點：【極度重要】節目最後主持人道別後，若有播放片尾曲，【片尾曲必須完整保留】直到 12:00 整點新聞報道開始前（通常在 6800s~6840s）！後續播出的商業廣告及整點新聞，【全部屬於破口，切除在新聞報時前一瞬間】！"""

        json_format_example = """{{
  "keep_intervals": [
    {{"start": 0.0, "end": 0.0, "description": "Part 1: 開場 Jingle 起拍至 10:30 歌曲/話題結束、商業破口開始前"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 2 (或 2a/2b): 10:30 半點破口後接回至 11:00 整點新聞前主持對話（若中途有 1 分鐘廣告請拆分切除，切勿丟棄後半段主持話題！）"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 3 (或 3a/3b): 11:00 整點新聞後單元 Jingle 接回至 11:30 廣告前（若中途有 1 分鐘廣告請拆分切除，切勿丟棄後半段主持話題！）"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 4: 11:30 半點單元 Jingle 起拍至節目完結道別與片尾曲結束（切除有誰共鳴與 12:00 新聞）"}}
  ]
}}"""

    prompt = f"""你是一位專業的香港商台叱咤903廣播節目剪輯專家。你的任務是閱讀以下帶有時間戳的廣播節目完整逐字稿（廣東話），根據【前文後理】識別出哪些是【真正的電台節目正片內容】，哪些是【非節目內容：新聞報道、天氣預測、交通消息、電台台呼/宣傳、商業特約廣告】。

{show_info}

剪輯核心原則：
1. 【必須保留的正片 (keep_intervals)】：
   - 主持人之間的所有交談、話題閒聊、聽眾電話互動、金句、嘉賓訪談、單元遊戲。
   - 節目專屬 Jingle、開場主題曲、過場 Bumper（包含每半點過後的單元 Jingle）。
   - 【流行歌曲與純音樂連貫性（極度重要防誤殺，絕對嚴禁切歌！）】：
     1. 主持人在時段中播放的流行歌曲（包含片頭主題曲前奏音樂、話題之間的插曲、半點前/整點前播放的流行歌、以及節目最後道別後的片尾曲直到整點新聞前），【100% 屬於電台節目正片內容，絕對不准切除】！歌曲必須與前後對話完整連貫保留！
     2. 逐字稿中標記為「[音樂 / 流行歌曲播放中]」或歌詞片段的內容，屬於背景音樂或電台流行歌，【絕對不是廣告】，必須完整連貫保留在該段正片中！
     3. 【段落劃分與中途短廣告（極度重要，絕不可丟棄後半段主持話題！）】：
        - 節目按時鐘劃分為四個大時段（Part 1 ~ Part 4）。
        - 若某個時段中途出現 1~2 分鐘的極短商業廣告（例如 10:42 或 17:42 播了 1 分鐘汽車廣告、或活動宣傳，隨後主持人接回繼續主持話題），【請務必切除該 1 分鐘廣告並拆分為 a/b 兩個保留段落（如 Part 2a, Part 2b 或 Part 3a, Part 3b）】！
        - 【絕對不可因為時段中途出現 1 分鐘短廣告，就把該時段後續長達 8~10 分鐘的主持節目全盤拋棄】！
        - keep_intervals 可以輸出 4 到 7 個正片區間，只要是真正的主持話題與歌曲，都必須完整保留！
   - 節目結尾主持人的最後道別（如「聽日見、拜拜！」、「下個禮拜再見！」）及之前的所有話題。

{part_rules}

【嚴格警告】：請務必依據上方逐字稿中的【真實時間戳】輸出精確數值！每個段落的開頭都必須找到該段落 Jingle 或主持對話【實際開始的時間戳】，結尾必須找到主持對話或歌曲【實際結束的時間戳】，嚴禁猜測或抄襲任何數字！

請仔細閱讀前文後理，輸出【保留正片區間 (keep_intervals)】。
請嚴格輸出純 JSON 格式，格式如下（注意：start 與 end 必須填入逐字稿中的實際秒數浮點數，絕不可填 0.0 或猜測數字；若有中途短廣告可輸出 2a/2b、3a/3b）：
{json_format_example}

以下是逐字稿：
{transcript_text}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }

    req_data = json.dumps(payload).encode("utf-8")

    if certifi:
        ctx = ssl.create_default_context(cafile=certifi.where())
    else:
        ctx = ssl.create_default_context()

    if model:
        model_queue = [model]
    else:
        model_queue, _, _ = get_next_gemini_models_sequence()

    all_models_list = get_gemini_model_candidates()
    last_error = None
    successful_model = None
    parsed = None

    for attempt in range(1, 3):
        for idx, cand_model in enumerate(model_queue):
            if "lite" in cand_model.lower():
                print(f"  🛑 略過 Lite 模型 [{cand_model}]（系統嚴格禁用 Lite 模型以確保剪輯精確度）")
                continue
            round_str = f", 輪次 {attempt}/2" if attempt > 1 else ""
            print(f"  🤖 嘗試調用 Gemini 模型 [{cand_model}] (順位 {idx+1}/{len(model_queue)}{round_str})...")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cand_model}:generateContent?key={api_key}"
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, data=req_data, context=ctx, timeout=120) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    result_text = res["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(result_text)

                keep_intervals = parsed.get("keep_intervals", [])
                if not keep_intervals:
                    raise ValueError(f"模型 {cand_model} 未回傳有效的 keep_intervals")

                successful_model = cand_model
                if cand_model in all_models_list:
                    model_idx = all_models_list.index(cand_model)
                    record_gemini_model_success(cand_model, model_idx)

                print(f"  ✨ 模型 [{cand_model}] 分析成功！")
                break

            except Exception as e:
                last_error = e
                record_gemini_model_error(cand_model, e)
                print(f"  ⚠️ 模型 [{cand_model}] 調用失敗: {e}，自動嘗試下一個候選模型...")

        if successful_model and parsed:
            break
        if attempt < 2:
            print("  ⏳ 第一輪所有候選模型調用遇到網絡或服務波動，等待 6 秒後啟動第二輪重試...")
            time.sleep(6)

    if not successful_model or not parsed:
        raise RuntimeError(f"所有候選 Gemini 模型均調用失敗，最後錯誤: {last_error}")

    keep_intervals = parsed.get("keep_intervals", [])

    # 執行品檢門禁與自癒機制 (Quality Gatekeeper & Self-Healing Engine)
    passed, keep_intervals, cuts, report = inspect_and_verify_cut_quality(
        keep_intervals, cuts=[], total_duration=total_duration,
        transcript_segments=transcript_segments, show_code=show_code,
        engine_used=f"gemini ({successful_model})"
    )

    return keep_intervals, cuts, successful_model, report


def assemble_cleaned_audio(input_file, cuts, output_file, total_duration, keep_metadata=None):
    """根據切除區間，調用 FFmpeg 進行精準無損無縫拼接（含 120ms 微淡入淡出防爆音與原生 M4A 章節標記）"""
    if not cuts:
        print("未偵測到需要切除的廣告區間，直接複製音訊。")
        shutil.copy2(input_file, output_file)
        return []

    # 計算保留段落 (Keep Intervals)
    keep_intervals = []
    cursor = 0.0
    for cut in cuts:
        if cut["start"] > cursor + 0.5:
            keep_intervals.append((cursor, cut["start"]))
        cursor = max(cursor, cut["end"])
    if cursor < total_duration - 0.5:
        keep_intervals.append((cursor, total_duration))

    # 對應每個保留段落的描述名稱 (供 M4A 章節與 Podcast Show Notes 使用)
    chapter_entries = []
    out_cursor = 0.0
    print(f"\n✂️ 將保留以下 {len(keep_intervals)} 個精華內容段落:")
    for idx, (ks, ke) in enumerate(keep_intervals):
        seg_dur = max(0.1, ke - ks)
        desc = f"Part {idx+1}"
        if keep_metadata:
            # 尋找最匹配的 keep_metadata 描述
            for km in keep_metadata:
                if abs(float(km.get("start", -999)) - ks) < 5.0 or abs(float(km.get("end", -999)) - ke) < 5.0:
                    raw_desc = str(km.get("description", "")).strip()
                    if raw_desc:
                        desc = raw_desc
                    break
        out_start = out_cursor
        out_end = out_cursor + seg_dur
        out_cursor = out_end
        chapter_entries.append({
            "index": idx + 1,
            "orig_start": ks,
            "orig_end": ke,
            "out_start": out_start,
            "out_end": out_end,
            "duration": seg_dur,
            "title": desc
        })
        print(f"  段落 #{idx+1}: {timedelta(seconds=int(ks))} ({ks:.1f}s) -> {timedelta(seconds=int(ke))} ({ke:.1f}s) [時長: {timedelta(seconds=int(seg_dur))}] | {desc}")

    # 使用 FFmpeg concat filter 進行精確合成，並在每個接縫加入 0.12s 微淡入淡出 (Micro-Fade) 徹底消除切點爆音
    filter_parts = []
    concat_inputs = []
    fade_d = 0.12
    for idx, (ks, ke) in enumerate(keep_intervals):
        seg_dur = max(0.5, ke - ks)
        fade_out_st = max(0.0, seg_dur - fade_d)
        filter_parts.append(
            f"[0:a]atrim=start={ks}:end={ke},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade_d},afade=t=out:st={fade_out_st:.3f}:d={fade_d}[a{idx}];"
        )
        concat_inputs.append(f"[a{idx}]")

    filter_complex = "".join(filter_parts) + "".join(concat_inputs) + f"concat=n={len(keep_intervals)}:v=0:a=1[concata];[concata]volume=2.0dB,alimiter=limit=0.98[outa]"

    # 建立 FFmpeg 原生 M4A 章節 Metadata 檔案 (;FFMETADATA1)
    meta_file = f"{output_file}.ffmeta"
    try:
        with open(meta_file, "w", encoding="utf-8") as mf:
            mf.write(";FFMETADATA1\n")
            for ch in chapter_entries:
                st_ms = int(round(ch["out_start"] * 1000))
                et_ms = int(round(ch["out_end"] * 1000))
                safe_title = ch["title"].replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\\", "\\\\").replace("\n", " ")
                mf.write(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={st_ms}\nEND={et_ms}\ntitle={safe_title}\n")
    except Exception as e:
        print(f"  ⚠️ 寫入章節 Metadata 略過: {e}")
        meta_file = None

    # 選擇最佳 AAC 編碼器與碼率：
    # 優先使用 macOS 原生 Apple AudioToolbox (aac_at)，音質更溫潤自然，消除低碼率人聲乾癟問題；
    # 碼率設定為 96k，使 75~85 分鐘的去廣告精華時長剛好穩定保持在 50~60 MB 之間。
    audio_codec = "aac"
    try:
        test_cmd = [FFMPEG_BIN, "-v", "quiet", "-f", "lavfi", "-i", "anullsrc", "-c:a", "aac_at", "-t", "0.1", "-f", "null", "-"]
        if subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            audio_codec = "aac_at"
    except Exception:
        pass

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", input_file,
    ]
    if meta_file and os.path.exists(meta_file):
        cmd.extend(["-i", meta_file, "-map_metadata", "1", "-map_chapters", "1"])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outa]",
        "-c:a", audio_codec,
        "-b:a", "96k",
        "-movflags", "+faststart",
        output_file
    ])

    start_time = time.time()
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    finally:
        if meta_file and os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except Exception:
                pass
    print(f"✅ 剪輯與合成完成（已嵌入 {len(chapter_entries)} 個原生 M4A 章節與防爆音微淡入淡出），耗時: {time.time()-start_time:.1f} 秒 -> {output_file}")
    return chapter_entries


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
    filename = os.path.basename(input_file).lower()
    show_code = "ilub" if "ilub" in filename else "bgog"
    date_match = re.search(r'(\d{4})[-_]?(\d{2})[-_]?(\d{2})', filename)
    date_str = f"{date_match.group(1)}{date_match.group(2)}{date_match.group(3)}" if date_match else None

    print(f"\n{'='*70}")
    print(f"🎙️ 開始商台廣播自動去廣告處理: {os.path.basename(input_file)} (節目代碼: {show_code})")
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

        # 2. Whisper 粵語全集轉錄（傳入 show_code 啟用節目專屬詞彙引導 Prompt）
        print("\n[步驟 2/4] 正在執行 Whisper Metal 硬體加速語音辨識...")
        segments = run_whisper_transcription(temp_wav, model_path=model_path, json_base_path=transcript_base, show_code=show_code)

        # 3. 廣告/新聞區間語意偵測
        print("\n[步驟 3/4] 正在分析逐字稿並識別廣告、新聞與交通破口...")
        cuts = []
        final_keeps = []
        used_engine = "heuristic"
        inspection_report = None

        api_key = get_gemini_api_key()

        if api_key:
            try:
                date_label = f", 日期: {date_str}" if date_str else ""
                print(f"🧠 [AI 語意理解] 偵測到 GEMINI_API_KEY，啟用 Gemini 模型池輪流輪替分析 {show_code}{date_label}...")
                keeps, ai_cuts, used_model, report = detect_ad_intervals_with_gemini(segments, total_duration, show_code=show_code, api_key=api_key, date_str=date_str)
                if report["passed"]:
                    cuts = ai_cuts
                    final_keeps = keeps
                    used_engine = f"gemini ({used_model})"
                    inspection_report = report
                    print(f"✅ Gemini [{used_model}] 品質品檢通過 (評分: {report['score']}/100)！純淨時長: {report['clean_duration_formatted']}")
                    if report["healed_items"]:
                        print(f"  🔧 自動自癒修正 {len(report['healed_items'])} 項:")
                        for h_item in report["healed_items"]:
                            print(f"    - {h_item}")
                    for idx, k in enumerate(keeps):
                        print(f"    正片 #{idx+1}: {timedelta(seconds=int(k['start']))} -> {timedelta(seconds=int(k['end']))} ({k.get('description', '')})")
                else:
                    print(f"⚠️ Gemini [{used_model}] 品質品檢未通過 ({report['failure_reasons']})，切換至啟發式規則引擎進行品檢。")
            except Exception as e:
                print(f"⚠️ Gemini 語意分析遇到問題 ({e})，自動安全回退至本機啟發式規則引擎！")

        if not cuts:
            print("⚙️ 啟動本機啟發式雙引擎（整點新聞狀態機 + 交通商業廣告詞庫）...")
            tagged, raw_heuristic_cuts = detect_ad_intervals(segments, total_duration)
            # 將 heuristic cuts 轉為 keep_intervals 進行品檢門禁檢驗
            heuristic_keeps = []
            cursor = 0.0
            for c in raw_heuristic_cuts:
                if c["start"] > cursor + 1.0:
                    heuristic_keeps.append({"start": cursor, "end": c["start"], "description": "啟發式正片"})
                cursor = max(cursor, c["end"])
            if cursor < total_duration - 1.0:
                heuristic_keeps.append({"start": cursor, "end": total_duration, "description": "啟發式正片結尾"})

            h_passed, heuristic_keeps, heuristic_cuts, h_report = inspect_and_verify_cut_quality(
                heuristic_keeps, cuts=raw_heuristic_cuts, total_duration=total_duration,
                transcript_segments=segments, show_code=show_code, engine_used="heuristic"
            )
            inspection_report = h_report
            if h_passed:
                cuts = heuristic_cuts
                final_keeps = heuristic_keeps
                used_engine = "heuristic"
                print(f"✅ 啟發式雙引擎品檢通過！純淨時長: {h_report['clean_duration_formatted']}")
            else:
                print(f"🛑 [門禁阻斷] 啟發式雙引擎產出未通過品檢門禁 ({h_report['failure_reasons']})！")

        total_cut_seconds = sum(c["duration"] for c in cuts) if cuts else 0.0
        print(f"\n📊 偵測結果分析 (採用引擎: {used_engine}):")
        print(f"  總共發現 {len(cuts)} 個非節目廣告/新聞破口")
        print(f"  預計切除總時長: {timedelta(seconds=int(total_cut_seconds))} ({total_cut_seconds:.1f} 秒)")

        for idx, cut in enumerate(cuts):
            print(f"    破口 #{idx+1}: {timedelta(seconds=int(cut['start']))} -> {timedelta(seconds=int(cut['end']))} (時長: {timedelta(seconds=int(cut['duration']))}) [{cut['sample_text']}]")

        # 4. 安全品質門禁檢驗 (Quality Gatekeeper Final Verification)
        if not cuts or not inspection_report or not inspection_report.get("passed", False):
            print("\n" + "="*70)
            print("🚨 [門禁警報] 音檔未通過自動品質檢驗門禁 (Quality Gatekeeper Failed)！")
            reasons = inspection_report.get("failure_reasons", ["未達標"]) if inspection_report else ["無有效切除區間"]
            for r in reasons:
                print(f"  ❌ {r}")
            print("🛡️ 安全機制：嚴格阻止殘缺、多切或廣告未切淨之劣質音檔發布！")
            print("="*70 + "\n")
            return output_file, False, {
                "inspection_passed": False,
                "inspection_report": inspection_report,
                "error": "品質品檢門禁未通過"
            }

        # 5. 執行音訊切除與無縫合成（嵌入 M4A 章節與微淡入淡出）
        print(f"\n[步驟 4/4] 正在執行音訊精準剪切與無縫拼接...")
        chapter_entries = assemble_cleaned_audio(input_file, cuts, output_file, total_duration, keep_metadata=final_keeps)

        # 產生 Podcast Show Notes 章節時間軸摘要
        chapters_lines = []
        for ch in chapter_entries:
            s_sec = int(ch["out_start"])
            m_part, s_part = divmod(s_sec, 60)
            h_part, m_part = divmod(m_part, 60)
            ts_str = f"{h_part:02d}:{m_part:02d}:{s_part:02d}" if h_part > 0 else f"{m_part:02d}:{s_part:02d}"
            chapters_lines.append(f"{ts_str} - {ch['title']}")
        chapters_summary = "\n".join(chapters_lines)

        cleaned_duration = get_audio_duration(output_file)
        cleaned_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n{'='*70}")
        print("🎉 純淨版 Podcast 音檔生成成功（通過品質品檢門禁）！")
        print(f"  輸出路徑: {output_file}")
        print(f"  原始時長: {timedelta(seconds=int(total_duration))} ({total_duration:.1f} 秒)")
        print(f"  純淨時長: {timedelta(seconds=int(cleaned_duration))} ({cleaned_duration:.1f} 秒)")
        print(f"  共節省時間: {timedelta(seconds=int(total_cut_seconds))} ({total_cut_seconds:.1f} 秒)")
        print(f"  檔案大小: {cleaned_size_mb:.2f} MB")
        print(f"  品檢評分: {inspection_report.get('score', 100)}/100")
        print(f"{'='*70}\n")

        return output_file, True, {
            "engine": used_engine,
            "total_cuts": len(cuts),
            "cut_seconds": total_cut_seconds,
            "original_duration": total_duration,
            "cleaned_duration": cleaned_duration,
            "chapters_summary": chapters_summary,
            "chapter_entries": chapter_entries,
            "inspection_passed": True,
            "inspection_report": inspection_report
        }

    except Exception as e:
        print(f"\n❌ 去廣告過程發生異常: {e}")
        return output_file, False, {"error": str(e), "inspection_passed": False}

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
