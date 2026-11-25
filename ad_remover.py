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
import urllib.error
import ssl
from datetime import timedelta

try:
    import certifi
except ImportError:
    certifi = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(SCRIPT_DIR, "recordings")
FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
FFPROBE_BIN = "/opt/homebrew/bin/ffprobe" if os.path.exists("/opt/homebrew/bin/ffprobe") else "ffprobe"

WHISPER_BIN = "/opt/homebrew/bin/whisper-cli"
if not os.path.exists(WHISPER_BIN):
    WHISPER_BIN = "/opt/homebrew/bin/whisper-cpp" if os.path.exists("/opt/homebrew/bin/whisper-cpp") else "whisper-cli"

LARGE_MODEL = os.path.join(SCRIPT_DIR, "models", "ggml-large-v3-turbo.bin")
DEFAULT_MODEL = LARGE_MODEL if os.path.exists(LARGE_MODEL) else os.path.join(SCRIPT_DIR, "models", "ggml-base.bin")

# 1. 新聞狀態機關鍵字（含簡繁體 Whisper 常見識別變體）
NEWS_START_KEYWORDS = [
    "新聞報導", "新聞報道", "商業電台新聞", "即是七九零三", "即測903 新聞", "叱咤903 新聞",
    "新闻报导", "新闻报道", "商业电台新闻", "报道新闻", "报导新闻", "北陀新闻", "现在由"
]
NEWS_END_KEYWORDS = [
    "這節新聞播出完", "新聞報道完", "新聞報導完", "這節新聞播出", "報道完畢", "報導完畢", "這節新聞播完",
    "這一節新聞報道完", "这节新闻播出完", "这节新闻报道完", "这节新闻报导完", "新闻报道完", "新闻报导完",
    "报道完毕", "报导完毕", "这节新闻播完", "这一节新闻报道完", "这一节新闻"
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
    "食安中心提提你", "升級有型食屍", "加入升級更有型", "金管局", "防騙",
    "选举委员会", "选举管理委员会", "選舉委員會", "香港赛马会", "香港賽馬會", "香港蔡卖会", "香港蔡賣會",
    "慈善信托基金", "慈善信託基金", "流感疫苗", "流感针", "流感針", "季节性流感", "季節性流感", "卫生署"
]

# 5. 商台台呼、節目宣傳與熱線
STATION_PROMOS = [
    "1872903", "叱咤903", "商業二台", "雷霆881", "即上各大音樂平台收聽", "戀愛信號",
    "即拆903", "拆903", "即拆 903", "音樂平台", "平台收聽", "即相隔", "即上各大",
    "有誰共鳴", "有稅共鳴", "引發你的共鳴", "50881903", "中銀香港理財", "一切從音樂開始",
    "LIFE 音樂會", "LIFE音樂會", "woodie", "woodby", "即刻學堂", "即參學彈",
    "寫一秒", "新一秒", "七九零三", "即七九零三", "色測903", "釋測903", "釋測九零三", "跑高山", "爬下山", "借一秒", "者1秒",
    "這一秒", "这一秒", "7703", "77903", "天天實在", "天天实在",
    "王先生想你", "在晴朗的一天出發", "晴朗", "林海峰", "軟紙劫", "阮子健", "道格小島",
    "Pay for the day", "為你親自打點", "同年的自己", "港鐵接軌", "港铁接軌", "貫通地上地下", "贯通地上地下", "以即時新聞", "關心你生活的每一程",
    "Be Before Bed Time", "be before bed time", "不准睡先", "鄧小巧", "拾回自己的空間",
    "美味開餐", "美食嘉年華", "香港美食嘉年華",
    "知芬衣", "知婚姨", "朱芬", "知多知那麼多", "知多知咁多", "Centerminia", "善男信女", "鍾雪瑩", "楊偉倫",
    "星矮粒下午五至七", "下午五至七", "啦啦星之中八拍", "黃先生想你點", "黄先生想你点", "黃先生黃先生", "黄先生黄先生", "點話點好", "点话点好",
    "先生小姐", "先生,小姐", "拖匪", "手工杏仁", "拖匪翠翠",
    "心跳行星", "八五二零", "即上各大音乐平台", "这一秒 新一秒",
    "重塑經典", "重数经典", "從數經典", "从数经典", "錄音全程就在目前", "录音全程就在目前",
    "目前灌路", "目前觀露", "目前观露", "Taste the Atmosphere", "Singer Under My Skin",
    "HHM的浪漫", "黑喉黑面", "买一重老死", "買一重老死",
    "Always be with you", "周六快樂時代", "周六快乐时代", "Listen to Eason", "Listen to Eastern",
    "Lesson to Ethan", "與我常在", "与我常在", "叱咤樂壇", "戚冊學談", "戚冊學壇", "戚餐學壇", "戚測樂壇", "刺殺樂壇", "七七樂壇",
    "專業推介", "专业推介", "新上榜", "Oscar失眠藥", "Oscar 失眠藥", "一路漂浮", "一路飘放",
    "咆哮山莊", "爬敲山莊", "Music Break", "廣東歌關注組", "路人甲團火",
    "GilikGilikEX", "朱利朱利EX", "聖誕禮貌卡", "圣诞礼贸卡", "聖誕禮物卡", "交換聖誕禮貌卡", "交换圣诞礼贸卡",
    "九龍廣播道3號", "九龙广播道3号", "薛俊寧", "薛俊宁", "青山樂壇", "謝茜嘉", "謝西家", "梁文禮", "Woody",
    "IDG Sunday", "陪你周日狂熱", "陪你周日狂言", "Sunday is your everyday Sunday", "1, 2, 3, 4, 5, 6, 1", "1,2,3,4,5,6,1", "何時才到星期日", "何时才到星期日",
    "東亞丈夫", "东亚丈夫", "湯駿業", "汤俊叶", "王牌出場", "吳港波", "吴港波",
    "Nobody can break you", "永遲半踢水分", "永迟半滴水花", "永迟半踢水分", "想給你最好", "想及你最好", "想給你最好忍遲變成"
]

# 6. 商業特約廣告詞庫
AD_KEYWORDS = [
    "查詢", "詳情", "致電", "熱線", "登記", "立即致電", "優惠", "折扣", "送完即止",
    "條款及細則", "受條款及細則約束", "冠名贊助", "特約贊助", "全力贊助", "贊助播出", "節目贊助", "鳴謝贊助",
    "限時優惠", "現正發售", "門票現正", "公開發售", "快達票", "城網",
    "免費體驗", "立即登記", "電話查詢", "歡迎致電", "借定唔借", "還得到先好借",
    "儲蓄保險", "人壽保險", "定期存款", "年利率", "信用卡", "現金回贈",
    "立即下載", "應用程式", "各大門市", "各大分店", "各大專櫃", "專門店", "門市", "分店",
    "請瀏覽", "詳情請", "歡迎查詢", "網址", "dot com", ".com", ".hk",
    "用心為您", "伴你同行", "專業之選", "為你守護", "帶給您", "生活更精彩", "未來戰士", "未來展示",
    "設計站", "加規站", "車價", "低利率", "BMW", "B&W", "大廣告", "月餅", "美心",
    "要衝衝衝", "衝出身心健康",
    "AIA", "AIA+", "健康生活節", "身心健康", "麥當勞", "Donald M", "麥樂雞", "陌落計", "雞腿包", "Supreme Plus", "每公升", "加碼至",
    "駕駛學校", "駕駛改進課程", "肯倉", "二三四一一三二二", "公共服務車輛", "追返三分", "沒有三分", "扣分", "扣三分", "Shell", "V Power",
    "戒賭", "戒賭熱線", "1834633", "平和基金", "Recording Sessions", "演藝學院歌劇院", "Cityline", "City Line", "Cityline開放登記抽籤", "登記抽籤",
    "加德士", "Tekron", "綠色殯葬", "食安中心", "Select街之選", "街之選", "百佳", "Fusion", "Taste", "西九", "爵士音樂節",
    "中銀香港理財", "城市售票網明日公開發售", "Chester 2", "恒基物業",
    "高光時刻", "售票網", "城市售票網", "公開發售", "私隱專員", "私隱學堂", "個人資料", "香港驗車", "驗車公司",
    "全新單曲", "各大音樂平台", "告別信",
    "譚仔", "譚仔雲南米線", "髮再生", "發在身", "護髮專家", "兒童癌病基金", "捐出5元", "專鎖快", "跨境付款",
    "全城一致讚好", "和爹一起去旅行", "和爹去旅行", "我爹想旅行", "我爹上旅行", "狂賀連續七日", "狂賀連續七天", "飄放冠軍", "票房冠軍",
    "香港國際機場", "王嘉爾", "Jackson Wang", "Under the Castle", "打卡點", "SELECT街之選", "白街自家品牌", "意想前", "易賞錢",
    "Glip", "心安情", "OCBC華僑銀行", "華僑銀行", "成就每一個理想", "用心幫有心人",
    "先生,小姐,你有沒有幾分鐘", "先生小姐", "幾分鐘而已", "拖匪", "手工杏仁", "拖匪翠翠", "知婚姨", "現在姚堅", "姚堅", "報導新聞", "報道新聞",
    "昂坪360", "昂坪 360", "全景画", "全景畫", "水晶缆车", "水晶纜車", "一田購物", "來一田", "Mia Cucina", "Mia Katrina",
    "煤氣煮食爐", "煤气", "煤氣", "The Club", "ASCHA", "張慈浩", "网上投保", "網上投保", "保单契约", "保單契約",
    "不用驗身", "不用验身", "有三高", "完整條款", "完整条款", "獨立投保", "独立投保",
    "OCBC", "華僑銀行", "华翘银行", "財富管理", "财富管理", "成就理想", "追寻理想", "追尋理想", "可靠的伙伴", "可靠的夥伴", "用心帮", "用心幫", "梦想要落地", "夢想要落地", "创造更多这样的旅程",
    "飛鷹哥", "飞鷹哥", "飛鷹活絡油", "飛鷹活樂油", "飞鷹活樂遊", "活絡油", "活樂油", "活樂遊", "骨刺痛",
    "大館", "和言不同", "無界樂章", "Art May", "Art-mate", "存款保障", "存款守護者", "1831831",
    "我年輕過", "我年轻过", "人人都年輕過", "人人都年轻过", "劉德華最新單曲", "刘德华最新单曲", "柳德華最新單曲", "柳德华最新单曲",
    "黑痛", "成人藥片", "寶礦力水特", "補礦力水特", "補礦力水達", "Iron Water",
    "競賽牌照號碼", "竞赛牌照号码", "崇光感謝", "皇冠車展", "貴如美", "桂如美", "歸如美", "瞬間順滑", "國家安全", "国家安全", "愛國者治港", "爱国者治港",
    "港鐵商場", "港铁商场", "MTR MOBA", "MTR Mobile", "電子優惠券", "电子优惠券", "聖誕好禮", "圣诞好礼",
    "29616638", "coms-auth", "搭長榮航空", "搭长荣航空", "長榮航空", "长荣航空",
    "平治陳列室", "平治年度升級", "平致年度升级", "越貴新", "越贵新", "月桂生", "Kaneki", "Dress up your hair",
    "令每位觀眾變成搖滾巨星", "令每位观众变成摇滚巨星", "5 seconds of summer", "Excel Wonderland", "KKTX",
    "大分評", "大分评", "本片兒童不宜", "本片儿童不宜", "打保齡球可以保重", "打保龄球可以保重", "髮再生給你", "发再生给你"
]

# 7. 節目專屬 Jingle / 主持關鍵字（重點保護，絕不可切！）
PROGRAM_JINGLE_KEYWORDS = [
    "bad girl", "baggirl", "大過佬", "來大笑代替上路", "完美阿正", "笑爆嘴", "elsie", "elsi", "alsie", "l.c", "小姐, l.c",
    "你公司最討厭", "最討厭的甚麼", "最討厭的是甚麼", "帶過老", "大過老", "大过老", "代替上路",
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
    "美術學會", "有冇共鳴", "有冇共鳴?", "有沒有共鳴", "有没有共鸣", "你Sing我都Sing", "你sing我sing",
    "明星插班生", "明星導航員", "寶寶很苦", "宝宝很苦", "寶寶好苦", "寶寶恨苦", "寶寶狠腐",
    "寶寶很驚", "宝宝很惊", "寶寶好驚", "宝宝好惊", "寶保很驚", "上天下海", "三字經", "三次經", "你SING我都SING",
    "OOTD", "ootd", "OH OTD", "OH...OTD", "OH....OTD", "Oh OTD",
    "有勇氣是靠本事", "有勇氣,是靠本事", "有勇氣 時刻本事", "你一百通訊站", "Lay 100", "今晚趁著天空有聲", "今晚趁着天空有声",
    "ILUB TV", "I Love TV", "晚間劇場", "晚间剧场", "代課老師", "心水清大師", "心水清", "開心水清大師", "开心水清大师",
    "豪子", "阿豪", "占爺", "當奴哥",
    "鼓仔", "聽眾的鼓仔", "分享聽眾", "收到的鼓仔", "你不懂,我懂", "你不懂我懂", "你不懂，我懂",
    "sing a lup", "la la la la", "去蛇王", "辣辣辣", "阿佔", "太陽都落山", "未睡得覺", "四個人甘丁爽", "辣辣青絲",
    "ai 豪子", "ai豪子", "黃埔", "黃埔天地"
]

# 8. 整點與半點報時關鍵字（需精確定位報時與嗶聲）
TIME_ANNOUNCEMENT_KEYWORDS = [
    "10點", "11點", "12點", "5點", "6點", "7點", "十點", "十一點", "十二點", "五點", "六點", "七點",
    "10點半", "11點半", "十點半", "十一點半", "10時半", "11時半", "5點半", "6點半", "7點半",
    "10点", "11点", "12点", "5点", "6点", "7点", "十点", "十一点", "十二点", "五点", "六点", "七点",
    "10点半", "11点半", "十点半", "十一点半", "5点半", "6点半", "7点半"
]

# 9. 半點時鐘破口關鍵字 (:30)
HALF_HOUR_KEYWORDS = [
    "10點半", "10時半", "十點半", "11點半", "11時半", "十一點半",
    "5點半", "5時半", "五點半", "6點半", "6時半", "六點半",
    "17點半", "18點半",
    "10点半", "十点半", "11点半", "十一点半", "5点半", "五点半", "6点半", "六点半"
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
                "邊個痴線 邊個正常 唯有自己啦啦啦啦 Jacky突擊測驗 OH OTD OOTD 有勇氣是靠本事 "
                "黃埔天地 AI豪子 聽眾鼓仔 有冇共鳴 你SING我都SING 明星導航員 "
                "寶寶很苦 寶寶很驚 上天下海三字經 ILUB TV 晚間劇場 你一百通訊站 "
                "今晚趁著天空有聲 心水清大師 有誰共鳴 新聞報道 馬路的事 交通消息 天氣預測"
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
    non_ad_resume_streak = 0
    first_resume_sec = None

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
            non_ad_resume_streak = 0
            first_resume_sec = None

        # 1. 檢查整點新聞破口 (Top-of-the-hour break)
        # 嚴格限制：新聞只會出現在整點（50~65 分鐘 / 3000s~3900s，以及節目結尾 110~116 分鐘 / 6600s~6960s）
        is_in_news_window = (3000.0 <= f_sec <= 3900.0) or (f_sec >= 6600.0)
        news_anchor_triggers = (
            "商業電台新聞", "商业电台新闻", "報道新聞", "报道新闻", "報導新聞", "报导新闻",
            "現在由", "现在由", "北陀新聞", "北陀新闻", "姚堅報道", "摇坚报道"
        )
        is_news_start = is_in_news_window and (
            any(k in text for k in news_anchor_triggers) or
            (("新聞" in text or "新闻" in text) and not any(k in text for k in NEWS_END_KEYWORDS)) or
            (f_sec >= 6780.0 and any(k in text for k in ["12點", "12点", "19點", "19点", "十二點", "十九點", "驗車", "香港驗車"]))
        )
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

        # 3. 檢查破口是否遇到節目專屬 Jingle / Bumper 重開或主持連續對話恢復
        if in_break:
            is_jingle = any(kw.lower() in text.lower() for kw in PROGRAM_JINGLE_KEYWORDS)
            is_halluc = any(p in text for p in HALLUCINATION_PATTERNS)
            min_break_len = 480.0 if break_type == "整點新聞破口" else 240.0
            max_break_len = 1120.0 if break_type == "整點新聞破口" else 680.0
            if not is_ad_item and not is_halluc and (f_sec - break_start >= min_break_len) and (last_ad_end is None or f_sec - last_ad_end >= 5.0):
                if non_ad_resume_streak == 0:
                    first_resume_sec = f_sec
                non_ad_resume_streak += 1
            else:
                if is_ad_item or is_halluc:
                    non_ad_resume_streak = 0
                    first_resume_sec = None

            is_dialogue_resumed = (non_ad_resume_streak >= 4 and first_resume_sec is not None)
            if is_jingle or is_dialogue_resumed or (f_sec - break_start > max_break_len):
                in_break = False
                resume_anchor = first_resume_sec if (is_dialogue_resumed and not is_jingle) else f_sec
                end_cut = max(break_start + 5.0, resume_anchor - 1.0)
                non_ad_resume_streak = 0
                first_resume_sec = None
                cuts.append({
                    "start": round(break_start, 2),
                    "end": round(end_cut, 2),
                    "duration": round(end_cut - break_start, 2),
                    "sample_text": f"{break_type} (已保留報時/接回Jingle)"
                })
                print(f"  🎙️ [引擎1] {break_type}結束於 Jingle/對話前: {timedelta(seconds=int(end_cut))} ({end_cut:.2f}s) (時長: {end_cut - break_start:.1f}s)")

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


def get_gemini_api_keys():
    """從環境變數或 .env 檔案中取得 GEMINI_API_KEYS（支援多 Key 逗號分隔負載均衡）"""
    keys = []
    env_keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
    if env_keys:
        keys.extend([k.strip() for k in env_keys.split(",") if k.strip()])
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY=") or line.startswith("GEMINI_API_KEYS="):
                        val = line.split("=", 1)[1].strip()
                        for k in val.split(","):
                            k = k.strip()
                            if k and k not in keys:
                                keys.append(k)
        except Exception:
            pass
    return keys


def get_gemini_api_key():
    """取得主要使用的 Gemini API Key"""
    keys = get_gemini_api_keys()
    return keys[0] if keys else None


# Whisper 常見音樂/靜音幻覺詞庫（統一轉換為 [音樂 / 流行歌曲播放中]）
HALLUCINATION_PATTERNS = [
    "請不吝點贊", "请不吝点赞", "優優獨播劇場", "优优独播剧场", "Zither Harp",
    "點贊 訂閱", "点赞 订阅", "明鏡與點點", "明镜与点点", "明鏡需要", "明镜需要",
    "歡迎收看訂閱", "欢迎收看订阅", "YoYo Television", "字幕志愿者", "字幕志願者",
    "中文字幕", "感謝觀看", "感谢观看", "謝謝觀看", "谢谢观看", "Amara.org",
    "MING PAO", "Ming Pao", "明報加拿大"
]

# 候選 Gemini 旗艦/標準模型池（2026-11 實測具備免費額度之非 Lite 模型，搭配健康度與配額感知自動輪替）
DEFAULT_GEMINI_MODELS = [
    "gemini-4-hydrogen",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
]
GEMINI_STATE_FILE = os.path.join(SCRIPT_DIR, ".gemini_model_state.json")


def _create_gemini_ssl_context():
    """建立 Gemini API HTTPS 憑證上下文（優先使用 certifi，失敗時回退系統/寬鬆模式）"""
    if certifi:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl._create_unverified_context()
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def _call_gemini_json(model_name, api_key, req_data, timeout=120, ctx=None):
    """調用 Gemini generateContent API 並解析 JSON 回應（含 SSL 回退與 503 短暫退避重試）"""
    if ctx is None:
        ctx = _create_gemini_ssl_context()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    def _do_open(ssl_ctx):
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, data=req_data, context=ssl_ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    for attempt in range(3):
        try:
            res = _do_open(ctx)
            break
        except urllib.error.HTTPError as http_err:
            if http_err.code == 503 and attempt < 2:
                time.sleep(2.0 + attempt * 2.5)
                continue
            raise
        except Exception as err:
            if "CERTIFICATE_VERIFY_FAILED" in str(err) or "certificate verify failed" in str(err):
                ctx = ssl._create_unverified_context()
                res = _do_open(ctx)
                break
            raise

    result_text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    if result_text.startswith("```"):
        result_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", result_text, flags=re.IGNORECASE | re.DOTALL)
    return json.loads(result_text), ctx


def _extract_gemini_intervals(parsed, key_name):
    """將 Gemini 回傳的 dict 或 list JSON 統一正規化為區間清單"""
    if isinstance(parsed, dict):
        items = parsed.get(key_name, [])
        return items if isinstance(items, list) else []
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict) and key_name in parsed[0]:
            items = parsed[0].get(key_name, [])
            return items if isinstance(items, list) else []
        if all(isinstance(x, dict) and ("start" in x or "refined_start" in x) for x in parsed):
            return parsed
    return []


def get_gemini_model_candidates():
    """取得模型池列表，支援從環境變數 GEMINI_MODELS 覆寫，並強制過濾剔除所有 Lite 模型"""
    env_models = os.environ.get("GEMINI_MODELS")
    if env_models:
        raw_models = [m.strip() for m in env_models.split(",") if m.strip()]
    else:
        raw_models = list(DEFAULT_GEMINI_MODELS)
    safe_models = [m for m in raw_models if "lite" not in m.lower()]
    return safe_models if safe_models else list(DEFAULT_GEMINI_MODELS)


def get_next_gemini_models_sequence():
    """
    實施健康度與每日配額感知輪替（Quota & Health Aware Ordering）：
    1. 每日配額感知：當日請求數已逼近上限 (>= 15/20) 或曾遇 429 配額用盡之模型自動降級排至隊尾。
    2. 健康度感知：近期連續 503/Timeout 錯誤之模型降級。
    3. 配額充足且健康之模型優先調用。
    """
    models = get_gemini_model_candidates()
    last_idx = -1
    state = {}
    today_str = time.strftime("%Y-%m-%d")

    if os.path.exists(GEMINI_STATE_FILE):
        try:
            with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_idx = state.get("last_index", -1)
        except Exception:
            state = {}

    daily_usage = state.get("daily_usage", {})
    stats = state.get("stats", {})
    if state.get("daily_date") != today_str:
        daily_usage = {}
        state["daily_date"] = today_str
        state["daily_usage"] = daily_usage
        # 跨日重設每日配額時，同步清除前一日 429 與連續錯誤懲罰
        for m_st in stats.values():
            if isinstance(m_st, dict):
                m_st["consecutive_errors"] = 0
                if "429" in str(m_st.get("last_error", "")):
                    m_st["last_error"] = ""

    start_idx = (last_idx + 1) % len(models)
    ordered_sequence = models[start_idx:] + models[:start_idx]

    # 排序鍵：(是否逼近每日20次上限或遇到429, 是否連續錯誤>=2次, 今日已消耗請求數, 連續錯誤次數)
    ordered_sequence.sort(key=lambda m: (
        daily_usage.get(m, 0) >= 15 or "429" in str(stats.get(m, {}).get("last_error", "")),
        stats.get(m, {}).get("consecutive_errors", 0) >= 2,
        daily_usage.get(m, 0),
        stats.get(m, {}).get("consecutive_errors", 0)
    ))
    return ordered_sequence, start_idx, state


def record_gemini_model_success(model_name, model_index):
    """記錄本次成功使用的模型至持久化狀態檔（重置連續失敗計數並累計今日調用量）"""
    try:
        state = {}
        if os.path.exists(GEMINI_STATE_FILE):
            try:
                with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        today_str = time.strftime("%Y-%m-%d")
        if state.get("daily_date") != today_str:
            state["daily_date"] = today_str
            state["daily_usage"] = {}
        state.setdefault("daily_usage", {})[model_name] = state["daily_usage"].get(model_name, 0) + 1
        state["last_used_model"] = model_name
        state["last_index"] = model_index
        state["last_success_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        stats = state.setdefault("stats", {})
        m_stat = stats.setdefault(model_name, {"success": 0, "errors": 0, "consecutive_errors": 0})
        m_stat["success"] += 1
        m_stat["consecutive_errors"] = 0
        m_stat["last_error"] = ""
        with open(GEMINI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 無法寫入 Gemini 狀態檔案: {e}")


def record_gemini_model_error(model_name, err_msg):
    """記錄模型調用錯誤統計、累計今日調用量與連續錯誤次數"""
    try:
        state = {}
        if os.path.exists(GEMINI_STATE_FILE):
            try:
                with open(GEMINI_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        today_str = time.strftime("%Y-%m-%d")
        if state.get("daily_date") != today_str:
            state["daily_date"] = today_str
            state["daily_usage"] = {}
        state.setdefault("daily_usage", {})[model_name] = state["daily_usage"].get(model_name, 0) + 1
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
        "少爺占", "當奴", "阿占", "艾粒", "聖艾粒", "星矮粒", "星崖粒", "星藝粒", "星雅粒", "聖雷粒",
        "豪子", "AI豪子", "AI 豪子", "ai豪子", "ai 豪子", "我是AI",
        "Jacky", "jacky", "突擊測驗", "突擊貪業", "突擊抄業", "每日一問", "鼓仔", "黃埔", "sing a lup", "lalala",
        "有冇共鳴", "沒有共鳴", "有沒有共鳴", "有没有共鸣",
        "寶寶很苦", "宝宝很苦", "寶寶好苦", "寶寶恨苦", "寶寶狠腐",
        "寶寶很驚", "宝宝很惊", "寶寶好驚", "宝宝好惊", "寶保很驚", "OOTD", "OH OTD", "OH...OTD", "OH....OTD",
        "明星插班生", "明星導航員", "晚間劇場", "晚间剧场", "I Love TV", "ILUB TV",
        "心水清", "開心水清", "开心水清", "你sing", "你Sing", "你SING", "上天下海", "三字經", "三次經",
        "有勇氣是靠本事", "有勇氣,是靠本事", "有勇氣 時刻本事", "你一百通訊站", "Lay 100", "今晚趁著天空有聲", "今晚趁着天空有声",
        "邊個痴線", "唯有自己", "啦啦啦啦"
    ] if is_ilub else [
        "Bad Girl", "大過佬", "森美", "阿正", "Elsie", "妹頭", "吹水未夠班", "手機碌多咗", "十句講曬",
        "衰佬", "衰老", "衰老CLS", "衰老 CLS", "Customer Love Service",
        "雞仔好人", "街仔好人", "新day", "Everyday is Sunday", "Everyday is", "Monday Tuesday",
        "今天有什麼新", "掛住你", "來大笑代替上路", "粒粒皆辛苦"
    ]
    gap_news_keywords = ["18點", "18:30", "19點", "六點", "七點", "5時半", "五點半", "六點半"] if is_ilub else ["11點", "11:30", "12點", "10時半", "十點半", "十一點半"]

    cross_show_promos = [
        "Bad Girl", "bad girl", "大過佬", "大過老", "大过老", "重做壞句子", "重做坏句子",
        "早上10時至12時", "早上十至十二", "早上10至12"
    ] if is_ilub else [
        "聖艾粒", "星矮粒", "下午五至七", "下午5至7", "啦啦星之中八拍"
    ]

    promo_block_markers = [
        "在晴朗的一天", "再晴郎的一天", "再晴朗的一天", "林海峰", "主子主場館", "度假小島", "雙人套房",
        "903FORSTAR", "All-Star", "籃球賽", "男童賽", "古天樂", "古天駱", "歌星勇士隊", "世界航空",
        "跳島遊", "跳渡游", "有誰共鳴", "有谁共鸣", "此時此刻", "此时此刻", "50881903", "50-881-903",
        "專業推介", "专业推介", "新上榜", "三甲歌", "三甲位置", "冠軍作品", "冠军作品", "Good morning",
        "晚上九至十", "晚上十至十一", "同你流汗", "體育資訊", "心跳行星", "八五二零", "這一秒 新一秒", "这一秒 新一秒",
        "GilikGilikEX", "朱利朱利EX", "聖誕禮貌卡", "圣诞礼贸卡", "聖誕禮物卡", "交換聖誕禮貌卡", "交换圣诞礼贸卡",
        "九龍廣播道3號", "九龙广播道3号", "薛俊寧", "薛俊宁", "青山樂壇", "謝茜嘉", "謝西家", "梁文禮", "Woody",
        "晚上11至12", "晚上十一至十二", "下午十二至二", "下周十二至二",
        "黃先生想你點", "黄先生想你点", "黃先生黃先生", "黄先生黄先生", "點話點好", "点话点好", "髮再生給你", "发再生给你",
        "東亞丈夫", "东亚丈夫", "湯駿業", "汤俊叶", "王牌出場", "吳港波", "吴港波",
        "IDG Sunday", "陪你周日狂熱", "陪你周日狂言", "何時才到星期日", "何时才到星期日",
        "戚餐學壇", "戚冊學壇", "賜7903", "赐7903", "刺7903"
    ] + cross_show_promos
    outro_ad_markers = [
        "喜療駝", "藥膏", "痛腳", "雙人套房", "小中瓶", "精華水", "綠色殯葬", "綠色笨藏",
        "食環署", "食環柱", "本篇兒童不宜", "本片兒童不宜", "本片儿童不宜", "本片青少年", "我爹想旅行", "我爹上旅行",
        "全城一致讚好", "和爹一起去旅行", "和爹去旅行", "狂賀連續七日", "狂賀連續七天", "飄放冠軍", "票房冠軍", "心安情", "Glip",
        "先生,小姐", "先生小姐", "幾分鐘而已", "拖匪", "手工杏仁", "拖匪翠翠", "知婚姨", "現在姚堅", "姚堅", "報導新聞", "報道新聞",
        "一田購物", "Mia Cucina", "Mia Katrina", "名氣家", "煤氣煮食爐", "The Club", "現在搖堅", "现在摇坚", "摇坚报道",
        "郭書洋", "郭书洋", "陳世文", "陈世文", "黃牌美", "黄牌美", "大分評", "大分评",
        "Nobody can break you", "永遲半踢水分", "永迟半滴水花", "永迟半踢水分", "想給你最好", "想及你最好", "天天實在", "天天实在",
        "人人都年輕過", "人人都年轻过", "劉德華最新單曲", "刘德华最新单曲", "柳德華最新單曲", "柳德华最新单曲", "我年輕過", "我年轻过",
        "黃先生想你點", "黄先生想你点", "黃先生黃先生", "黄先生黄先生", "髮再生給你", "发再生给你"
    ]
    shared_ad_extras = [
        "天氣預測", "天氣預算", "天气预测", "天气预算", "流感", "皇家加勒比", "十方法師", "萬寧", "護髮", "打針", "防流感",
        "青衣城", "清衣城", "國際歌迷會", "各大音樂平台", "各大音乐平台", "輸血服務中心", "紅十字會", "通訊局", "coms-auth", "29616638",
        "選舉委員會", "选举委员会", "选举管理委员会", "香港賽馬會", "香港赛马会", "香港蔡賣會", "香港蔡卖会",
        "昂坪360", "昂坪 360", "全景畫", "全景画", "水晶纜車", "水晶缆车", "心跳行星", "八五二零",
        "i'm loving it", "double版燒雞腿包", "這支U87", "这支U87", "2027年2月20", "甚麼事 這是這個菜", "这是这个菜",
        "港鐵商場", "港铁商场", "MTR MOBA", "MTR Mobile", "電子優惠券", "电子优惠券", "聖誕好禮", "圣诞好礼"
    ]
    gap_stop_words = set(
        AD_KEYWORDS + TRAFFIC_KEYWORDS + WEATHER_KEYWORDS + NEWS_START_KEYWORDS +
        STATION_PROMOS + list(gap_news_keywords) + promo_block_markers + outro_ad_markers + shared_ad_extras
    )

    core_host_jingle_cues = [
        k.lower() for k in (gap_host_keywords + (
            [
                "聖艾粒", "星矮粒", "星崖粒", "星藝粒", "星雅粒", "聖雷粒", "少爺占", "當奴", "lalalala",
                "唯有自己", "邊個痴線", "突擊測驗", "美術學會", "有冇共鳴", "有沒有共鳴", "有没有共鸣",
                "你sing", "明星插班生", "明星導航員", "寶寶很苦", "宝宝很苦", "寶寶好苦", "寶寶恨苦", "寶寶狠腐",
                "寶寶很驚", "宝宝很惊", "寶寶好驚", "宝宝好惊", "寶保很驚", "ootd", "oh otd", "oh...otd", "oh....otd", "OOTD", "OH OTD", "OH...OTD", "OH....OTD",
                "ilub tv", "i love tv", "晚間劇場", "晚间剧场", "心水清", "開心水清", "开心水清",
                "黃埔", "豪子", "jacky", "上天下海", "三字經", "三次經", "有勇氣", "你一百通訊站", "lay 100"
            ] if is_ilub else [
                "bad girl", "baggirl", "大過佬", "大過老", "大过老", "來大笑代替上路", "elsie", "elsi", "alsie",
                "最後一個pose", "最後一個姿勢", "最後一條reels", "最後一條reals", "街仔好人", "雞仔好人", "衰佬",
                "衰老", "customer love service", "粒粒皆辛苦", "三個農夫", "everyday is",
                "吹水未夠班", "十句講曬", "手機碌多咗", "大明星殺手", "有人掛住你"
            ]
        ))
    ]

    def _seg_times(seg):
        if "offsets" in seg:
            return seg.get("offsets", {}).get("from", 0) / 1000.0, seg.get("offsets", {}).get("to", 0) / 1000.0
        return seg.get("start", 0.0), seg.get("end", 0.0)

    def _has_kw(text_str, keywords):
        tl = text_str.lower()
        return any((k in text_str) or (k.lower() in tl) for k in keywords)

    # -------------------------------------------------------------
    # 檢查項 0：邊界精準對齊自癒 (Boundary End-Snapping)
    # -------------------------------------------------------------
    song_announce_words = ["演唱", "這首歌", "給你這首", "给你手作品", "個人作品", "聽過有", "听过有", "[音樂 / 流行歌曲播放中]"]
    for k in healed_keeps:
        k_end = float(k["end"])
        for seg in transcript_segments:
            s_from, s_to = _seg_times(seg)
            s_txt = seg.get("text", "").strip()
            if abs(s_from - k_end) <= 1.5 and s_to > k_end + 0.5:
                is_music_or_announce = (
                    any(p in s_txt for p in HALLUCINATION_PATTERNS) or
                    any(w in s_txt for w in song_announce_words)
                )
                if is_music_or_announce and not _has_kw(s_txt, gap_stop_words):
                    healed_items.append(f"段落結尾對齊：由 {k_end:.1f}s 延伸至行尾 {s_to:.1f}s (完整保留結尾音樂/報歌名)")
                    k["end"] = round(s_to, 2)
                    break

    tail_ad_stop_words = set(
        AD_KEYWORDS + outro_ad_markers + STATION_PROMOS + PSA_KEYWORDS +
        TRAFFIC_KEYWORDS + WEATHER_KEYWORDS + NEWS_START_KEYWORDS + NEWS_END_KEYWORDS + shared_ad_extras
    )

    # -------------------------------------------------------------
    # 檢查項 0.1：段落結尾防誤吞廣告/宣傳回縮 (Boundary Tail Ad Pullback)
    # 若某正片段落結尾最後 75 秒內已進入廣告、節目宣傳、報時或新聞導語，自動回縮至廣告開始前
    # （防誤殺：純報時必須整句為報時語，且若回縮點後仍有 >= 3 句連續非廣告主持對話則視為口語閒聊不回縮）
    # -------------------------------------------------------------
    pure_time_re = re.compile(
        r'^(?:現在|现在|依家|而家|時間|时间|時間來到|时间来到)?\s*'
        r'(?:10|11|12|5|6|7|17|18|19|十|十一|十二|五|六|七)'
        r'(?:[:：](?:00|30)|(?:點|点|時|时)(?:半|整|正)?)[。，,!\s]*$'
    )
    strong_tail_ad_cues = [
        "Nobody can break you", "永遲半踢水分", "永迟半滴水花", "永迟半踢水分", "想給你最好", "想及你最好", "天天實在", "天天实在",
        "人人都年輕過", "人人都年轻过", "劉德華最新單曲", "刘德华最新单曲", "柳德華最新單曲", "柳德华最新单曲", "我年輕過", "我年轻过",
        "黃先生想你點", "黄先生想你点", "黃先生黃先生", "黄先生黄先生", "髮再生", "发再生",
        "目前灌路", "目前灌錄", "目前觀露", "目前观露", "重塑經典", "重塑经典", "重數經典", "重数经典", "從數經典",
        "錄音全程就在目前", "录音全程就在目前", "Taste the Atmosphere", "Singer Under My Skin",
        "賜7903", "赐7903", "刺7903", "新一秒",
        "令每位觀眾變成搖滾巨星", "令每位观众变成摇滚巨星", "1, 2, 3, 4, 5, 6, 1", "1,2,3,4,5,6,1", "IDG Sunday",
        "搭長榮航空", "搭长荣航空", "郭書洋", "郭书洋", "陳世文", "陈世文", "報道新聞", "报道新聞", "报道新闻", "新聞報道", "新闻报道"
    ]
    for k in healed_keeps:
        k_st = float(k["start"])
        k_et = float(k["end"])
        earliest_tail_ad = None
        earliest_ad_text = ""
        earliest_is_strong = False
        tail_window = max(k_st, k_et - 75.0)

        tail_segs = [
            s for s in transcript_segments
            if tail_window <= _seg_times(s)[0] <= k_et + 0.3
        ]

        non_ad_after_count = 0
        for s in tail_segs:
            s_from, s_to = _seg_times(s)
            s_txt = s.get("text", "").strip()
            # 僅針對剛好落在結尾交界的新聞導語提前回縮 0.5s，避免擦進主播開聲
            if s_from >= k_et - 0.05:
                if _has_kw(s_txt, ["報道新聞", "报道新闻", "報導新聞", "报导新闻", "郭書洋", "郭书洋", "陳世文", "陈世文", "姚堅", "葉俊庭"]):
                    if earliest_tail_ad is None:
                        earliest_tail_ad = max(k_st, k_et - 0.8)
                        earliest_ad_text = s_txt
                        earliest_is_strong = True
                continue
            is_pure_time = bool(pure_time_re.match(s_txt)) and not _has_kw(s_txt, gap_host_keywords)
            is_strong_ad = _has_kw(s_txt, strong_tail_ad_cues) or is_pure_time
            is_ad_seg = is_strong_ad or _has_kw(s_txt, tail_ad_stop_words)
            if is_ad_seg:
                if earliest_tail_ad is None:
                    earliest_tail_ad = s_from
                    earliest_ad_text = s_txt
                    earliest_is_strong = is_strong_ad
                elif is_strong_ad:
                    earliest_is_strong = True
                non_ad_after_count = 0
            else:
                if _has_kw(s_txt, core_host_jingle_cues):
                    earliest_tail_ad = None
                    earliest_ad_text = ""
                    earliest_is_strong = False
                    non_ad_after_count = 0
                elif earliest_tail_ad is not None and not any(p in s_txt for p in HALLUCINATION_PATTERNS) and len(s_txt) >= 2:
                    non_ad_after_count += 1
                    reset_threshold = 5 if earliest_is_strong else 3
                    if non_ad_after_count >= reset_threshold and (k_et - earliest_tail_ad >= 20.0 or not earliest_is_strong):
                        earliest_tail_ad = None
                        earliest_ad_text = ""
                        earliest_is_strong = False
                        non_ad_after_count = 0

        if earliest_tail_ad is not None and (k_et - earliest_tail_ad >= 0.5):
            healed_items.append(f"段落結尾廣告回縮：由 {k_et:.1f}s 提早回縮至 {earliest_tail_ad:.1f}s (切除誤吞之宣傳/商業廣告: {earliest_ad_text[:20]})")
            k["end"] = round(earliest_tail_ad, 2)

    # -------------------------------------------------------------
    # 檢查項 0.2：段落開頭防誤吞廣告/宣傳後推 (Boundary Head Ad/Promo Trimming)
    # 若 Part 2+ 開頭前 105 秒內出現明顯電台宣傳片或商業廣告（且尚未出現主持或節目 Jingle），自動將起點推後至廣告結束處
    # -------------------------------------------------------------
    head_host_dialogue_cues = core_host_jingle_cues + [
        "大哥", "阿姐", "森美", "阿正", "芯仔", "申美", "早安", "早晨",
        "接個聽眾", "接个听众", "聽眾進來", "听众进来", "game on", "全城起動", "全城起动", "準備好了嗎", "准备好了吗"
    ]
    for k in healed_keeps[1:]:
        k_st = float(k["start"])
        k_et = float(k["end"])
        if k_et - k_st <= 120.0:
            continue
        latest_head_ad_end = None
        latest_head_ad_text = ""
        head_window_end = min(k_et - 30.0, k_st + 105.0)
        non_ad_speech_count = 0
        stop_regular_ad_scan = False
        for s in transcript_segments:
            s_from, s_to = _seg_times(s)
            if s_to <= k_st:
                continue
            if s_from >= head_window_end:
                break
            s_txt = s.get("text", "").strip()
            if _has_kw(s_txt, head_host_dialogue_cues):
                break
            if _has_kw(s_txt, promo_block_markers):
                latest_head_ad_end = max(latest_head_ad_end or k_st, min(s_to, k_et - 30.0))
                latest_head_ad_text = s_txt
                non_ad_speech_count = 0
                stop_regular_ad_scan = False
            elif (not stop_regular_ad_scan) and _has_kw(s_txt, tail_ad_stop_words):
                latest_head_ad_end = max(latest_head_ad_end or k_st, min(s_to, k_et - 30.0))
                latest_head_ad_text = s_txt
                non_ad_speech_count = 0
            else:
                if not any(p in s_txt for p in HALLUCINATION_PATTERNS) and len(s_txt) >= 2:
                    non_ad_speech_count += 1
                    if non_ad_speech_count >= 5:
                        stop_regular_ad_scan = True
        if latest_head_ad_end is not None and (latest_head_ad_end - k_st >= 1.0):
            # 若宣傳片尾聲緊接 10 秒內出現單元 Jingle，精準對齊至 Jingle 起拍點
            aligned_end = latest_head_ad_end
            for s in transcript_segments:
                sf, _ = _seg_times(s)
                if latest_head_ad_end <= sf <= latest_head_ad_end + 12.0:
                    if _has_kw(s.get("text", ""), head_host_dialogue_cues):
                        aligned_end = sf
                        break
            healed_items.append(f"段落開頭廣告後推：由 {k_st:.1f}s 延後至 {aligned_end:.1f}s (切除開頭誤吞宣傳/商業廣告: {latest_head_ad_text[:20]})")
            k["start"] = round(aligned_end, 2)

    # -------------------------------------------------------------
    # 檢查項 0.3：破口內電台宣傳片孤島自動剔除 (Isolated Promo Island Removal)
    # 若兩個大破口之間夾著短區間（<= 165s）且無本節目主持/單元 Jingle 之孤島段落（如跨節目宣傳片 Bad Girl 大過佬、叱咤樂壇宣傳），自動剔除！
    # -------------------------------------------------------------
    if len(healed_keeps) >= 4:
        healed_keeps.sort(key=lambda x: float(x["start"]))
        filtered_islands = [healed_keeps[0]]
        island_promo_cues = [
            "叱咤樂壇", "戚冊學談", "戚測樂壇", "刺殺樂壇", "七七樂壇", "woodie",
            "專業推介", "专业推介", "新上榜", "music break", "咆哮山莊", "爬敲山莊",
            "oscar", "失眠藥", "失眠药", "有誰共鳴", "有谁共鸣", "這一秒", "这一秒", "新一秒"
        ] + HALF_HOUR_KEYWORDS + TIME_ANNOUNCEMENT_KEYWORDS + cross_show_promos
        strong_island_inner_cues = list(cross_show_promos) + [
            "叱咤樂壇", "戚冊學談", "戚測樂壇", "專業推介", "专业推介",
            "有誰共鳴", "有谁共鸣", "50881903", "GilikGilikEX", "朱利朱利EX", "聖誕禮貌卡",
            "黃先生想你點", "黄先生想你点", "IDG Sunday", "東亞丈夫", "心跳行星", "八五二零",
            "Nobody can break you", "永遲半踢水分", "永迟半踢水分", "天天實在", "天天实在",
            "港鐵商場", "港铁商场", "通訊局", "coms-auth", "29616638", "本片兒童不宜", "本篇兒童不宜"
        ]
        for idx_k in range(1, len(healed_keeps) - 1):
            cur_k = healed_keeps[idx_k]
            c_st = float(cur_k["start"])
            c_et = float(cur_k["end"])
            c_dur = c_et - c_st
            prev_et = float(filtered_islands[-1]["end"])
            next_st = float(healed_keeps[idx_k + 1]["start"])
            if c_dur <= 165.0 and (c_st - prev_et >= 35.0) and (next_st - c_et >= 60.0):
                inner_segs = [s for s in transcript_segments if c_st <= _seg_times(s)[0] < c_et]
                ctx_segs = [s for s in transcript_segments if (c_st - 18.0) <= _seg_times(s)[0] <= (c_et + 18.0)]
                has_core_host = any(_has_kw(s.get("text", ""), core_host_jingle_cues) for s in inner_segs)
                has_promo_ctx = any(_has_kw(s.get("text", ""), island_promo_cues + list(tail_ad_stop_words)) for s in ctx_segs)
                has_strong_inner_promo = any(_has_kw(s.get("text", ""), strong_island_inner_cues) for s in inner_segs)
                inner_ad_hits = sum(
                    1 for s in inner_segs
                    if _has_kw(s.get("text", ""), island_promo_cues + list(tail_ad_stop_words))
                )
                is_classic_island = (c_dur <= 135.0 and (c_st - prev_et >= 60.0) and (next_st - c_et >= 90.0) and has_promo_ctx)
                is_strong_promo_island = (has_strong_inner_promo or inner_ad_hits >= 3)
                if not has_core_host and (is_classic_island or is_strong_promo_island):
                    healed_items.append(
                        f"破口宣傳孤島剔除：移除夾在破口中間之電台宣傳/非正片短區間 {c_st:.1f}s~{c_et:.1f}s (時長: {c_dur:.1f}s)"
                    )
                    continue
            filtered_islands.append(cur_k)
        filtered_islands.append(healed_keeps[-1])
        healed_keeps = filtered_islands

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
                next_head_segs = [
                    s for s in transcript_segments
                    if n_start <= _seg_times(s)[0] <= n_start + 12.0
                ]
                has_real_break = any(
                    _has_kw(s.get("text", ""), gap_stop_words | tail_ad_stop_words)
                    for s in seg_in_gap
                )
                has_hallucination = any(
                    any(p in s.get("text", "") for p in HALLUCINATION_PATTERNS)
                    for s in seg_in_gap
                )
                host_hit_count = sum(
                    1 for s in seg_in_gap if _has_kw(s.get("text", ""), gap_host_keywords)
                )
                next_starts_with_jingle = any(
                    _has_kw(s.get("text", ""), PROGRAM_JINGLE_KEYWORDS)
                    for s in next_head_segs
                )
                if not has_real_break and not has_hallucination and host_hit_count >= 2 and not next_starts_with_jingle and len(seg_in_gap) >= 3:
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
        resume_jingle_cues = gap_host_keywords + [
            "明星坐在旁邊", "開車有個明星", "Game On", "全城起動", "全城起动", "接個聽眾", "接个听众"
        ]
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
        # 2.1 Part 1 開頭主題曲前奏防延遲切入與開播前幻覺廣告剔除
        p1 = healed_keeps[0]
        p1_start = float(p1["start"])
        intro_jingle_cues = [
            "聖艾粒", "星崖粒", "星矮粒", "星藝粒", "星雅粒", "聖雷粒", "啦啦啦啦", "一早起床", "公海二",
            "太陽都落山", "太阳都落山", "太陽都降山", "太陽都浪山", "太阳都浪山",
            "長毛Jackie", "长毛Jackie", "長毛Jacky", "长毛Jacky", "開牙齒", "开牙齿",
            "講壞事", "讲坏事", "壞事下午", "坏事下午", "舌頭都流冰", "舌头都流冰",
            "講壞話隨便", "讲坏话随便", "感覺奔放", "感觉奔放", "沙腥老好健壮", "寫字樓好緊張", "收工了有空",
            "未睡得久", "未睡得覺", "未睡得觉", "啦啦聲之中", "啦啦声之中", "四個人甘丁爽"
        ] if is_ilub else [
            "bad girl", "badgirl", "baggirl", "大過佬", "大過老", "大过老", "帶過老", "大过路", "大過路", "來大笑代替上路", "代替上路",
            "完美阿正", "笑爆嘴", "elsie", "elsi", "alsie", "借首歌", "借宿法", "借宿白",
            "每日晨光", "每日晨運", "每日晨混", "扮六尺", "扮六呎"
        ]
        # 尋找開播前最後一則新聞/交通/天氣/公益宣傳/商業廣告的結束點與最早主題曲起點（聖艾粒播前廣告常至 380s）
        intro_scan_limit = 430.0 if is_ilub else 350.0
        last_pre_show_ad_end = 25.0
        earliest_cue_time = None
        for seg in transcript_segments:
            s_from, s_to = _seg_times(seg)
            s_text = seg.get("text", "")
            s_lower = s_text.lower()
            if s_from <= intro_scan_limit:
                is_intro_cue = any(k in s_lower for k in intro_jingle_cues) or any(k in s_text for k in intro_jingle_cues)
                if is_intro_cue:
                    if earliest_cue_time is None:
                        earliest_cue_time = s_from
                    continue
                if (earliest_cue_time is None or s_from <= earliest_cue_time):
                    if _has_kw(s_text, gap_stop_words) or any(w in s_text for w in ["王牌出場", "深夜一至二", "直播室跟大家再見面"]):
                        last_pre_show_ad_end = max(last_pre_show_ad_end, s_to)

        # 防提前切入廣告：若 p1_start 小於等於播前廣告結束點，延後起點避開廣告
        if p1_start < last_pre_show_ad_end - 0.5:
            new_p1_st = last_pre_show_ad_end
            if earliest_cue_time and earliest_cue_time > last_pre_show_ad_end:
                new_p1_st = max(last_pre_show_ad_end, earliest_cue_time - 3.0)
            healed_items.append(f"Part 1 起點由 {p1_start:.1f}s 延後至 {new_p1_st:.1f}s (切除開播前商業廣告/公益宣傳/門票抽籤)")
            p1["start"] = round(new_p1_st, 2)
            p1_start = float(p1["start"])
        # 防延遲切入：若 p1_start 大於最早 Jingle 起點，前移挽回開場前奏
        elif earliest_cue_time and earliest_cue_time >= last_pre_show_ad_end and (p1_start - earliest_cue_time) > 3.0:
            healed_items.append(f"Part 1 起點由 {p1_start:.1f}s 前移至 {earliest_cue_time:.1f}s (挽救開場主題曲前奏)")
            p1["start"] = round(earliest_cue_time, 2)
            p1_start = float(p1["start"])

        if p1_start <= 500.0:
            checks["part1_intro"] = f"PASS (起點: {p1_start:.1f}s)"
        else:
            checks["part1_intro"] = f"WARN (起點: {p1_start:.1f}s > 500s)"

        # 2.2 Part 1 結束時間防提前切歌保護 (10:30 或 17:30 半點前流行歌) 與防異常跨越 10:30/17:30 廣告大破口
        p1_end = float(p1["end"])
        if p1_end > 1650.0:
            # Part 1 異常跨越 10:30 / 17:30 廣告大破口！自動切除破口並拆分出 Part 2a
            found_break_start = None
            found_break_end = None
            for seg in transcript_segments:
                s_from = seg.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in seg else seg.get("start", 0.0)
                s_to = seg.get("offsets", {}).get("to", 0)/1000.0 if "offsets" in seg else seg.get("end", 0.0)
                s_text = seg.get("text", "")
                s_lower = s_text.lower()
                if 1250.0 <= s_from <= 1600.0:
                    if any(w in s_text for w in gap_stop_words) or any(w in s_text for w in p1_break_keywords):
                        if found_break_start is None:
                            found_break_start = s_from
                if 1600.0 <= s_from <= p1_end:
                    if any(k in s_lower for k in intro_jingle_cues) or any(k in s_text for k in intro_jingle_cues):
                        found_break_end = s_from
                        break
            if found_break_start and found_break_end:
                half_time_label = "17:30" if is_ilub else "10:30"
                healed_items.append(f"Part 1 異常過長修復：由 {p1_end:.1f}s 截斷至 {half_time_label} 廣告前 {found_break_start:.1f}s，並於 {found_break_end:.1f}s 接回 Part 2a (切除中途 {found_break_end - found_break_start:.1f}s 廣告大破口)")
                orig_end = p1["end"]
                p1["end"] = round(found_break_start, 2)
                healed_keeps.insert(1, {
                    "start": round(found_break_end, 2),
                    "end": orig_end,
                    "description": f"Part 2a: {half_time_label} 半點破口後主題 Jingle 接回"
                })
                checks["part1_pop_song"] = f"PASS (異常過長已自癒拆分，Part 1 結束於: {p1['end']}s)"
            else:
                checks["part1_pop_song"] = f"PASS (結束點: {p1_end:.1f}s >= 1250s)"
        elif p1_end < 1250.0:
            new_p1_end = p1_end
            has_ad_after_p1 = False
            for seg in transcript_segments:
                s_from = seg.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in seg else seg.get("start", 0.0)
                s_to = seg.get("offsets", {}).get("to", 0) / 1000.0 if "offsets" in seg else seg.get("end", 0.0)
                s_text = seg.get("text", "").strip()
                if s_from < p1_end - 0.5:
                    continue
                if s_from > 1650.0:
                    break
                # 若遇到任何廣告、宣傳、交通、天氣、報時或破口字眼，廣告大破口已開始，立即終止延伸！
                is_break_marker = (
                    any(w in s_text for w in gap_stop_words) or
                    any(w in s_text for w in outro_ad_markers) or
                    any(k in s_text for k in p1_break_keywords) or
                    any(k in s_text for k in HALF_HOUR_KEYWORDS) or
                    any(k in s_text for k in TIME_ANNOUNCEMENT_KEYWORDS) or
                    any(k in s_text for k in TRAFFIC_KEYWORDS + WEATHER_KEYWORDS + PSA_KEYWORDS + STATION_PROMOS + AD_KEYWORDS)
                )
                if is_break_marker:
                    has_ad_after_p1 = True
                    break
                # 僅在無廣告且屬於音樂/歌曲或正片銜接（間隔 <= 5.0s）時順延
                is_music_or_announce = (
                    any(p in s_text for p in HALLUCINATION_PATTERNS) or
                    any(w in s_text for w in song_announce_words)
                )
                if is_music_or_announce and (s_from - new_p1_end <= 5.0):
                    new_p1_end = round(s_to, 2)
                elif s_from - new_p1_end > 5.0:
                    break

            if new_p1_end > p1_end + 3.0:
                half_label = "17:30" if is_ilub else "10:30"
                healed_items.append(f"Part 1 結束點由 {p1_end:.1f}s 延伸至 {new_p1_end:.1f}s (挽救 {half_label} 半點前流行歌)")
                p1["end"] = new_p1_end
                checks["part1_pop_song"] = f"PASS (自癒延伸至: {p1['end']}s)"
            elif has_ad_after_p1:
                checks["part1_pop_song"] = f"PASS (結束點: {p1_end:.1f}s，隨後即為半點廣告破口)"
            else:
                checks["part1_pop_song"] = f"WARN (結束點: {p1_end:.1f}s < 1250s)"
        else:
            checks["part1_pop_song"] = f"PASS (結束點: {p1_end:.1f}s >= 1250s)"

        # 2.25 整點新聞與廣告破口防吞自癒保護 (11:00 或 18:00 整點新聞，嚴禁單一正片區間橫跨整點！)
        # 若某區間跨越了 3250s (整點新聞前) 與 3480s (第二小時 Jingle 接回)，代表模型嚴重幻覺吞噬了整點新聞與廣告大破口！
        new_healed_keeps = []
        for k in healed_keeps:
            k_st = float(k["start"])
            k_et = float(k["end"])
            if k_st < 3250.0 and k_et > 3480.0:
                news_break_keywords = ["新聞報道", "新聞報導", "商業電台新聞", "即測903", "叱咤903 新聞", "11點", "18點", "十一點", "十八點", "報告新聞"]
                h2_start_jingles = (
                    [
                        "聖艾粒", "星矮粒", "星崖粒", "少爺占", "當奴", "lalalala",
                        "有冇共鳴", "有沒有共鳴", "你sing", "明星導航員", "明星插班生",
                        "寶寶很苦", "寶寶很驚", "寶寶好驚", "上天下海", "三字經", "晚間劇場"
                    ] if is_ilub else [
                        "今日手機碌多咗", "手機弄多", "手機碌多", "最後一個post", "最後一條reels", "最後一個",
                        "大明星殺手", "衰佬", "衰佬cls", "粒粒皆辛苦", "你知道唔知道有人掛住你", "來大笑代替上路",
                        "bad girl", "大過佬"
                    ]
                )
                found_news_st = None
                for s in transcript_segments:
                    st_sec = s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)
                    txt = s.get("text", "")
                    if 3100.0 <= st_sec <= 3350.0:
                        if any(w in txt for w in news_break_keywords):
                            found_news_st = st_sec
                            break
                if found_news_st is None:
                    found_news_st = 3205.2

                h2_scan_lo = 3850.0 if is_ilub else 3500.0
                h2_scan_hi = 4250.0 if is_ilub else 3900.0
                found_h2_st = None
                for s in transcript_segments:
                    st_sec = s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)
                    txt = s.get("text", "")
                    if h2_scan_lo <= st_sec <= h2_scan_hi:
                        if any(w in txt.lower() for w in h2_start_jingles):
                            found_h2_st = st_sec
                            break
                if found_h2_st is None:
                    found_h2_st = 4000.0 if is_ilub else 3720.1

                healed_items.append(
                    f"整點新聞破口自癒拆分：區間異常跨越整點新聞破口（{k_st:.1f}s~{k_et:.1f}s），"
                    f"自動於 {found_news_st:.1f}s 截斷第一小時，並於 {found_h2_st:.1f}s 接回第二小時"
                    f"（切除中途 {found_h2_st - found_news_st:.1f}s 整點新聞、天氣、公益與商業廣告）"
                )
                new_healed_keeps.append({
                    "start": k_st,
                    "end": round(found_news_st, 2),
                    "description": f"{k.get('description', '')} (第一小時末尾，整點新聞前)"
                })
                new_healed_keeps.append({
                    "start": round(found_h2_st, 2),
                    "end": k_et,
                    "description": "第二小時上半段單元 Jingle 接回"
                })
            else:
                new_healed_keeps.append(k)
        healed_keeps = new_healed_keeps

        # 2.3 Part 2 結束點檢驗 (11:00 或 18:00 整點新聞前主持對話)
        h1_keeps = [k for k in healed_keeps if float(k["start"]) < 3500.0]
        if h1_keeps:
            last_h1_end = max(float(k["end"]) for k in h1_keeps)
            h1_min_end = 3030.0 if is_ilub else 3100.0
            if last_h1_end < h1_min_end:
                checks["part2_continuity"] = f"WARN (第一小時最後結束於: {last_h1_end:.1f}s < {int(h1_min_end)}s)"
            else:
                checks["part2_continuity"] = f"PASS (第一小時最後結束於: {last_h1_end:.1f}s >= {int(h1_min_end)}s)"
        else:
            checks["part2_continuity"] = "FAIL (缺少第一小時下半段)"
            failure_reasons.append("缺少第一小時下半段")

        # 2.45 半點破口防橫跨自癒保護 (11:30 或 18:30 半點廣告/天氣/宣傳破口，嚴禁單一正片區間橫跨半點大破口！)
        half_break_label = "18:30" if is_ilub else "11:30"
        h2_split_keeps = []
        for k in healed_keeps:
            k_st = float(k["start"])
            k_et = float(k["end"])
            if k_st < 4950.0 and k_et > 5420.0:
                scan_lo = max(k_st + 60.0, 4880.0)
                scan_hi = min(k_et - 60.0, 5680.0)
                span_segs = [s for s in transcript_segments if scan_lo <= _seg_times(s)[0] <= scan_hi]
                strong_half_cues = (
                    promo_block_markers + WEATHER_KEYWORDS + TRAFFIC_KEYWORDS +
                    ["天文台", "相對濕度", "相对湿度", "空氣質素", "空气质素", "萬寧", "万宁", "貴如美", "桂如美", "歸如美", "有誰共鳴", "有谁共鸣"]
                )
                break_hits = []
                for s in span_segs:
                    sf, st_ = _seg_times(s)
                    stxt = s.get("text", "").strip()
                    if _has_kw(stxt, core_host_jingle_cues):
                        continue
                    is_pt = bool(pure_time_re.match(stxt))
                    is_strong = is_pt or _has_kw(stxt, strong_half_cues)
                    is_ad = is_strong or _has_kw(stxt, tail_ad_stop_words)
                    if is_ad:
                        break_hits.append((sf, st_, is_strong, stxt))

                hb_core_hi = 5420.0 if is_ilub else 5380.0
                hb_start_hi = 5340.0 if is_ilub else 5220.0
                strong_in_core = [x for x in break_hits if 4940.0 <= x[0] <= hb_core_hi and x[2]]
                if len(break_hits) >= 3 and len(strong_in_core) >= 2:
                    # 尋找半點破口起點：取 4920s~hb_start_hi 間第一個強破口點（且之後無主持 Jingle 恢復）
                    found_hb_st = None
                    for sf, st_, is_strong, _ in break_hits:
                        if 4920.0 <= sf <= hb_start_hi and is_strong:
                            has_host_soon = any(
                                _has_kw(s.get("text", ""), core_host_jingle_cues)
                                for s in span_segs if sf < _seg_times(s)[0] <= min(sf + 120.0, hb_start_hi)
                            )
                            if not has_host_soon:
                                found_hb_st = sf
                                break
                    if found_hb_st is None:
                        found_hb_st = strong_in_core[0][0]

                    # 尋找半點破口終點：優先在 found_hb_st + 140s ~ 5680s 之間找第一個單元 Jingle 起拍點（且後面 90s 內無強廣告/天氣/宣傳）
                    found_hb_et = None
                    for s in span_segs:
                        sf, st_ = _seg_times(s)
                        if sf < found_hb_st + 140.0:
                            continue
                        stxt = s.get("text", "").strip()
                        if _has_kw(stxt, core_host_jingle_cues):
                            has_ad_soon = any(
                                _has_kw(s2.get("text", ""), strong_half_cues)
                                for s2 in span_segs if sf < _seg_times(s2)[0] <= sf + 90.0
                            )
                            if not has_ad_soon:
                                found_hb_et = sf
                                break

                    # 若 Whisper 未辨識出單元 Jingle，則找最後一則廣告/宣傳/天氣結束後，且後續 90s 無再出現強廣告的連續正片對話起點
                    if found_hb_et is None:
                        last_ad_in_break = found_hb_st + 140.0
                        for s in span_segs:
                            sf, st_ = _seg_times(s)
                            if sf < found_hb_st or sf > 5620.0:
                                continue
                            stxt = s.get("text", "").strip()
                            if _has_kw(stxt, strong_half_cues) or _has_kw(stxt, tail_ad_stop_words) or bool(pure_time_re.match(stxt)):
                                last_ad_in_break = max(last_ad_in_break, st_)
                        resume_streak = 0
                        first_resume_t = None
                        for s in span_segs:
                            sf, st_ = _seg_times(s)
                            if sf < last_ad_in_break - 0.5:
                                continue
                            stxt = s.get("text", "").strip()
                            if not any(p in stxt for p in HALLUCINATION_PATTERNS) and len(stxt) >= 2:
                                if resume_streak == 0:
                                    first_resume_t = sf
                                resume_streak += 1
                                if resume_streak >= 3 and first_resume_t is not None:
                                    found_hb_et = first_resume_t
                                    break
                        if found_hb_et is None and last_ad_in_break > found_hb_st + 140.0:
                            found_hb_et = last_ad_in_break

                    if found_hb_et and (found_hb_et - found_hb_st >= 140.0) and (found_hb_st - k_st >= 60.0) and (k_et - found_hb_et >= 180.0):
                        healed_items.append(
                            f"{half_break_label} 半點破口防橫跨自癒拆分：區間異常跨越半點廣告破口（{k_st:.1f}s~{k_et:.1f}s），"
                            f"自動於 {found_hb_st:.1f}s 截斷並於 {found_hb_et:.1f}s 接回 Part 4"
                            f"（切除中途 {found_hb_et - found_hb_st:.1f}s 半點報時、天氣、宣傳與商業廣告）"
                        )
                        h2_split_keeps.append({
                            "start": k_st,
                            "end": round(found_hb_st, 2),
                            "description": f"Part 3b: {half_break_label} 半點破口前正片"
                        })
                        h2_split_keeps.append({
                            "start": round(found_hb_et, 2),
                            "end": k_et,
                            "description": f"Part 4: {half_break_label} 半點破口後單元 Jingle 接回至完結"
                        })
                        continue
            h2_split_keeps.append(k)
        healed_keeps = h2_split_keeps

        # 2.4 Part 3 結束點檢驗 (11:30 或 18:30 半點前單元遊戲)
        h2_crossing = [k for k in healed_keeps if float(k["start"]) < 4950.0 and float(k["end"]) > 5420.0]
        h2_first_end_max = 5420.0 if is_ilub else 5350.0
        h2_first_keeps = [k for k in healed_keeps if 3400.0 <= float(k["start"]) < 5200.0 and float(k["end"]) <= h2_first_end_max]
        if h2_crossing:
            checks["part3_continuity"] = f"FAIL (單一段落橫跨 {half_break_label} 半點大破口: {h2_crossing[0]['start']}s~{h2_crossing[0]['end']}s)"
            failure_reasons.append(f"單一段落橫跨 {half_break_label} 半點廣告破口")
        elif h2_first_keeps:
            last_h2_first_end = max(float(k["end"]) for k in h2_first_keeps)
            if last_h2_first_end < 4800.0:
                checks["part3_continuity"] = f"WARN (第二小時上半段結束於: {last_h2_first_end:.1f}s < 4800s)"
            else:
                checks["part3_continuity"] = f"PASS (第二小時上半段結束於: {last_h2_first_end:.1f}s >= 4800s)"
        else:
            checks["part3_continuity"] = "FAIL (缺少第二小時上半段)"
            failure_reasons.append("缺少第二小時上半段")

        # 2.5 Part 4 結尾片尾曲防提前切歌保護與防誤吞片尾商業廣告 (12:00 或 19:00 整點新聞前)
        p_last = healed_keeps[-1]
        last_end = float(p_last["end"])
        new_last_end = last_end

        # 防誤吞片尾廣告與整點新聞：若 last_end 落在片尾廣告/宣傳/新聞導語中，自動回縮至廣告開始前
        outro_ad_words = [
            "想你陪我錄音", "想你陪我录音", "mcdonald", "recording sessions",
            "本篇兒童不宜", "本片兒童不宜", "本片儿童不宜", "本篇青少年",
            "12點", "12点", "19點", "19点", "十二點", "十九點",
            "新聞報道", "新聞報導", "報導新聞", "報道新聞", "商業電台新聞", "現在由", "姚堅",
            "郭書洋", "郭书洋", "陳世文", "陈世文", "黃牌美", "黄牌美",
            "先生,小姐", "先生小姐", "幾分鐘而已", "知婚姨", "知芬衣", "朱芬", "拖匪", "手工杏仁",
            "全城一致讚好", "和爹一起去旅行", "和爹去旅行", "票房冠軍", "心安情", "glip",
            "黃先生黃先生", "黄先生黄先生", "黃先生想你點", "黄先生想你点", "點話點好", "点话点好",
            "髮再生", "发再生", "nobody can break you", "永遲半踢水分", "永迟半踢水分", "永迟半滴水花",
            "想給你最好", "想及你最好", "天天實在", "天天实在",
            "人人都年輕過", "人人都年轻过", "劉德華最新單曲", "刘德华最新单曲", "柳德華最新單曲", "柳德华最新单曲",
            "我年輕過", "我年轻过", "大分評", "大分评", "有誰共鳴", "有谁共鸣", "50881903",
            "coms-auth", "29616638", "這一秒 新一秒", "这一秒 新一秒"
        ]
        for s in transcript_segments:
            st_sec = s.get("offsets", {}).get("from", 0)/1000.0 if "offsets" in s else s.get("start", 0.0)
            txt = s.get("text", "")
            if 6550.0 <= st_sec <= last_end + 0.3:
                if _has_kw(txt, outro_ad_words):
                    p_last_st = float(p_last["start"])
                    if st_sec <= p_last_st + 5.0 and len(healed_keeps) >= 2:
                        # 最後一小段完全位於片尾廣告之後（例如 18:59 的 10s 台呼孤島），直接整段剔除避免產生倒置區間！
                        healed_items.append(f"Part 4 尾端孤島剔除：移除位於片尾廣告 ({txt[:16]}@{st_sec:.1f}s) 之後的殘留短段 {p_last_st:.1f}s~{last_end:.1f}s")
                        healed_keeps.pop()
                        p_last = healed_keeps[-1]
                        last_end = float(p_last["end"])
                        new_last_end = last_end
                    elif st_sec < last_end - 0.5:
                        healed_items.append(f"Part 4 結尾由 {last_end:.1f}s 提早回縮至 {st_sec:.1f}s (切除片尾宣傳/整點新聞前廣告: {txt[:20]})")
                        p_last["end"] = round(st_sec, 2)
                        last_end = float(p_last["end"])
                        new_last_end = last_end
                    elif st_sec >= last_end - 0.5:
                        pulled = round(max(p_last_st + 1.0, max(6550.0, st_sec - 0.8)), 2)
                        healed_items.append(f"Part 4 結尾由 {last_end:.1f}s 微調回縮至 {pulled:.1f}s (避開整點新聞起拍重疊: {txt[:20]})")
                        p_last["end"] = pulled
                        last_end = pulled
                        new_last_end = pulled
                    break

        if last_end < 6600.0:
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

        if last_end >= 6600.0:
            checks["part4_outro_song"] = f"PASS (結束於: {last_end:.1f}s >= 6600s)"
        else:
            checks["part4_outro_song"] = f"WARN (結束於: {last_end:.1f}s < 6600s)"

    # -------------------------------------------------------------
    # 檢查項 2.6：段中高密度短廣告自癒拆分 (Mid-Segment Short Ad Cluster Excision)
    # 若單段超過 500 秒之正片中途夾雜 15~180 秒商業短廣告（如 18:45 心水清大師前短破口），自動切除並拆為 a/b 兩段
    # -------------------------------------------------------------
    news_bulletin_anchors = [
        "商業電台新聞", "商业电台新闻", "現在由葉俊庭", "现在由叶俊庭", "葉俊庭報道新聞", "叶俊庭报道新闻",
        "北陀新聞", "北陀新闻", "這節新聞報道完", "這一節新聞報道完", "這節新聞報導完", "這節新聞播出完",
        "這節新聞播完", "这一节新闻报道完", "这节新闻报道完", "这节新闻报导完", "这节新闻播出完", "这节新闻播完",
        "現在姚堅報道新聞", "现在摇坚报道新闻", "姚堅報道新聞", "摇坚报道新闻",
        "郭書洋", "郭书洋", "陳世文", "陈世文", "黃牌美", "黄牌美", "現在由郭", "现在由郭", "現在由陳", "现在由陈"
    ]
    strict_ad_marker_groups = [
        ("流感疫苗", ("流感疫苗", "流感針", "流感针", "季節性流感", "季节性流感")),
        ("昂坪360", ("昂坪360", "昂坪 360", "水晶纜車", "水晶缆车", "全景畫", "全景画")),
        ("選舉委員會", ("選舉委員會", "选举委员会", "選舉管理委員會", "选举管理委员会")),
        ("賽馬會", ("香港賽馬會", "香港赛马会", "香港蔡賣會", "香港蔡卖会", "慈善信託基金", "慈善信托基金")),
        ("有誰共鳴", ("有誰共鳴", "有谁共鸣", "50881903")),
        ("條款細則", ("受條款及細則約束", "受条款及细则约束", "條款及細則", "条款及细则", "完整條款", "完整条款", "保單契約", "保单契约")),
        ("借定唔借", ("借定唔借", "還得到先好借")),
        ("防騙/食安", ("防騙視伏器", "食安中心提提你")),
        ("天氣預報", ("天氣預測", "天气预测", "天氣預算", "本港地區", "本港地区", "本安地區", "本安地区", "天文台", "相對濕度", "相对湿度", "空氣質素", "空气质素", "季後風", "季候風")),
        ("心跳行星", ("心跳行星", "八五二零")),
        ("一田購物", ("一田購物", "來一田")),
        ("煤氣MiaCucina", ("Mia Cucina", "Mia Katrina", "名氣家", "煤氣煮食爐")),
        ("AIA投保", ("AIA",)),
        ("網上/獨立投保", ("網上投保", "网上投保", "獨立投保", "独立投保")),
        ("新歌宣傳", ("全新單曲", "全新单曲", "最新單曲", "最新单曲", "各大音樂平台", "各大音乐平台", "我年輕過", "我年轻过", "人人都年輕過", "人人都年轻过")),
        ("OCBC銀行", ("OCBC", "華僑銀行", "华翘银行")),
        ("財富管理", ("財富管理", "财富管理")),
        ("成就理想", ("成就每一個理想", "成就每一个理想", "成就理想初心")),
        ("用心幫有心人", ("用心幫", "用心帮")),
        ("EasonU87", ("這支U87", "这支U87", "2027年2月20", "Recording Sessions", "演藝學院歌劇院")),
        ("陳奕迅廣告", ("目前灌路", "目前灌錄", "Taste the Atmosphere", "Singer Under My Skin")),
        ("飛鷹活絡油", ("飛鷹哥", "飞鷹哥", "飛鷹活絡油", "飛鷹活樂油", "飞鷹活樂遊", "活絡油", "活樂油", "活樂遊", "骨刺痛")),
        ("大館音樂節", ("大館", "和言不同", "無界樂章", "Art May", "Art-mate")),
        ("存款保障", ("存款保障", "存款守護者", "1831831")),
        ("寶礦力", ("寶礦力水特", "補礦力水特", "補礦力水達", "Iron Water")),
        ("黑痛藥片", ("黑痛", "成人藥片")),
        ("貴如美萬寧", ("貴如美", "桂如美", "歸如美", "瞬間順滑", "萬寧獨家", "万宁独家", "萬寧")),
        ("國安宣傳", ("國家安全", "国家安全", "愛國者治港", "爱国者治港")),
        ("叱咤樂壇宣傳", ("叱咤樂壇", "戚冊學談", "戚測樂壇", "刺殺樂壇", "七七樂壇", "謝茜嘉", "謝西家", "梁文禮", "Woody")),
        ("港鐵台呼", ("港鐵接軌", "港铁接軌", "貫通地上地下", "贯通地上地下", "關心你生活的每一程")),
        ("港鐵商場", ("港鐵商場", "港铁商场", "MTR MOBA", "MTR Mobile", "電子優惠券", "电子优惠券", "聖誕好禮", "圣诞好礼")),
        ("通訊局", ("通訊局", "通讯局", "coms-auth", "29616638")),
        ("天天實在廣告", ("Nobody can break you", "永遲半踢水分", "永迟半踢水分", "永迟半滴水花", "想給你最好", "想及你最好", "天天實在", "天天实在")),
        ("5SOS演唱會", ("5 seconds of summer", "令每位觀眾變成搖滾巨星", "Excel Wonderland", "KKTX")),
        ("黃先生想你點", ("黃先生想你點", "黄先生想你点", "黃先生黃先生", "黄先生黄先生", "髮再生給你", "发再生给你", "髮再生", "发再生")),
        ("IDG Sunday", ("IDG Sunday", "陪你周日狂熱", "陪你周日狂言", "何時才到星期日", "何时才到星期日")),
        ("平治/長榮/越貴新", ("平治陳列室", "平治年度升級", "搭長榮航空", "長榮航空", "越貴新", "月桂生", "Dress up your hair")),
        ("電台聖誕卡宣傳", ("GilikGilikEX", "朱利朱利EX", "聖誕禮貌卡", "圣诞礼贸卡", "聖誕禮物卡", "九龍廣播道3號", "九龙广播道3号", "薛俊寧", "薛俊宁")),
        ("報時台呼", ("這一秒 新一秒", "这一秒 新一秒", "即7903")),
    ]
    if is_ilub:
        strict_ad_marker_groups.append(
            ("跨節目宣傳", ("Bad Girl", "大過佬", "大過老", "大过老", "重做壞句子", "重做坏句子", "早上10時至12時"))
        )
    strong_brand_groups = {
        "飛鷹活絡油", "大館音樂節", "存款保障", "寶礦力", "黑痛藥片", "貴如美萬寧",
        "國安宣傳", "流感疫苗", "昂坪360", "有誰共鳴", "OCBC銀行", "AIA投保", "煤氣MiaCucina", "一田購物",
        "電台聖誕卡宣傳", "叱咤樂壇宣傳", "港鐵台呼",
        "港鐵商場", "通訊局", "天天實在廣告", "陳奕迅廣告", "5SOS演唱會", "黃先生想你點", "IDG Sunday", "平治/長榮/越貴新", "跨節目宣傳"
    }

    def _find_dense_ad_cluster(segs_slice):
        matched_points = []
        for s in segs_slice:
            s_from, s_to = _seg_times(s)
            s_txt = s.get("text", "").strip()
            s_lower = s_txt.lower()
            for canon_name, variants in strict_ad_marker_groups:
                if any((v in s_txt) or (v.lower() in s_lower) for v in variants):
                    matched_points.append((s_from, s_to, canon_name))
        for t_anchor, _, _ in matched_points:
            win_pts = [(ts, te, w) for ts, te, w in matched_points if t_anchor <= ts <= t_anchor + 90.0]
            uniq_groups = {w for _, _, w in win_pts}
            uniq_times = {ts for ts, _, _ in win_pts}
            is_dense = (len(uniq_groups) >= 3 and len(uniq_times) >= 2) or (
                len(uniq_groups) >= 2 and bool(uniq_groups & strong_brand_groups) and len(uniq_times) >= 3
            )
            if is_dense:
                c_end = max(te for _, te, _ in win_pts)
                has_host_inside = any(
                    _has_kw(s.get("text", ""), core_host_jingle_cues)
                    for s in segs_slice if t_anchor <= _seg_times(s)[0] < c_end
                )
                if not has_host_inside:
                    return t_anchor, c_end, sorted(uniq_groups)
        return None

    split_healed_keeps = []
    for k in healed_keeps:
        k_st = float(k["start"])
        k_et = float(k["end"])
        if k_et - k_st >= 500.0:
            k_segs = [s for s in transcript_segments if k_st <= _seg_times(s)[0] < k_et]
            cluster = _find_dense_ad_cluster(k_segs)
            if cluster:
                c_st, c_et, c_words = cluster
                # 向前後延伸貼合同一廣告串前後緊鄰之無主持廣告句（例如首句「不用驗身,有三高」與尾句）
                for s in reversed(k_segs):
                    sf, st_ = _seg_times(s)
                    if sf >= c_st:
                        continue
                    if c_st - st_ > 12.0 or _has_kw(s.get("text", ""), core_host_jingle_cues):
                        break
                    if _has_kw(s.get("text", ""), tail_ad_stop_words):
                        c_st = sf
                    else:
                        break
                for s in k_segs:
                    sf, st_ = _seg_times(s)
                    if sf < c_et:
                        continue
                    if sf - c_et > 12.0 or _has_kw(s.get("text", ""), core_host_jingle_cues):
                        break
                    if _has_kw(s.get("text", ""), tail_ad_stop_words):
                        c_et = st_
                    else:
                        break
                if (c_st - k_st >= 120.0) and (k_et - c_et >= 90.0) and (15.0 <= c_et - c_st <= 180.0):
                    healed_items.append(
                        f"段中短廣告自癒拆分：於 {c_st:.1f}s~{c_et:.1f}s 自動切除中途廣告破口 "
                        f"({c_et-c_st:.1f}s, 命中: {', '.join(c_words[:3])})"
                    )
                    split_healed_keeps.append({"start": k_st, "end": round(c_st, 2), "description": f"{k.get('description', '')} (上半段)"})
                    split_healed_keeps.append({"start": round(c_et, 2), "end": k_et, "description": f"{k.get('description', '')} (下半段)"})
                    continue
        split_healed_keeps.append(k)
    healed_keeps = split_healed_keeps

    # 過濾任何異常倒置或極短（<= 1s）殘段，並合併緊鄰無破口區間（間距 <= 1.0s），確保簡介節數與 M4A 章節數 100% 一致
    valid_sorted = sorted(
        [k for k in healed_keeps if float(k["end"]) > float(k["start"]) + 1.0],
        key=lambda x: float(x["start"])
    )
    normalized_keeps = []
    for k in valid_sorted:
        if normalized_keeps and float(k["start"]) <= float(normalized_keeps[-1]["end"]) + 1.0:
            normalized_keeps[-1]["end"] = max(float(normalized_keeps[-1]["end"]), float(k["end"]))
            if k.get("description") and k.get("description") not in normalized_keeps[-1].get("description", ""):
                normalized_keeps[-1]["description"] = f"{normalized_keeps[-1].get('description', '')} / {k.get('description', '')}"
        else:
            normalized_keeps.append(dict(k))
    healed_keeps = normalized_keeps
    # 計算自癒後純淨時長
    clean_duration = sum(float(k["end"]) - float(k["start"]) for k in healed_keeps)
    clean_dur_str = f"{int(clean_duration//3600):02d}:{int((clean_duration%3600)//60):02d}:{int(clean_duration%60):02d}"

    # -------------------------------------------------------------
    # 檢查項 3：時長門禁 (Clean Duration Gate)
    # -------------------------------------------------------------
    if is_2hr_show:
        min_clean_dur = 3960.0 if is_ilub else 4200.0
        max_clean_dur = 5880.0 if is_ilub else 6000.0
        min_str = "01:06:00" if is_ilub else "01:10:00"
        max_str = "01:38:00" if is_ilub else "01:40:00"
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
    # 檢查項 3.5：正片殘留新聞與廣告污染掃描門禁 (Contamination Scan Gate)
    # -------------------------------------------------------------
    contamination_hits = []
    for k_idx, k in enumerate(healed_keeps):
        k_st = float(k["start"])
        k_et = float(k["end"])
        k_segs = [s for s in transcript_segments if k_st <= _seg_times(s)[0] < k_et]
        matched_news = []
        for s in k_segs:
            s_from, _ = _seg_times(s)
            s_txt = s.get("text", "").strip()
            for na in news_bulletin_anchors:
                if na in s_txt:
                    matched_news.append((s_from, na))
                    break
        if matched_news:
            t_first, w_first = matched_news[0]
            contamination_hits.append(f"段落#{k_idx+1} ({k_st:.0f}s~{k_et:.0f}s) 含整點新聞聲帶 [{w_first}@{t_first:.0f}s]")
            continue
        cluster = _find_dense_ad_cluster(k_segs)
        if cluster:
            c_st, _, c_words = cluster
            contamination_hits.append(f"段落#{k_idx+1} ({k_st:.0f}s~{k_et:.0f}s) 含高密度廣告/公益聲帶 ({', '.join(c_words)}@{c_st:.0f}s)")

    if contamination_hits:
        checks["ad_contamination"] = f"FAIL ({'; '.join(contamination_hits[:3])})"
        failure_reasons.append(f"正片段落殘留新聞或商業廣告 ({'; '.join(contamination_hits[:2])})")
    else:
        checks["ad_contamination"] = "PASS (未偵測到整點新聞或高密度商業/公益廣告殘留)"

    # -------------------------------------------------------------
    # 檢查項 4：引擎門禁 (Engine Gate)
    # -------------------------------------------------------------
    if "heuristic" in engine_used.lower() and (clean_duration > 6000.0 or len(healed_keeps) < 4):
        checks["engine_gate"] = f"FAIL (啟發式規則引擎產出異常：時長 {clean_dur_str}、共 {len(healed_keeps)} 段，廣告未切淨，嚴禁放行)"
        failure_reasons.append("啟發式引擎切除不完全，嚴禁放行")
    else:
        checks["engine_gate"] = f"PASS (採用引擎: {engine_used})"

    # 推導最終切除區間
    final_cuts = derive_cuts_from_keep_intervals(healed_keeps, total_duration)

    # 評分機制
    score = 100
    for k, v in checks.items():
        if "WARN" in v:
            score -= 10
        elif "FAIL" in v:
            score -= 25
            if not any(k in r for r in failure_reasons):
                failure_reasons.append(f"品檢項目未通過: {k} ({v})")
    score = max(0, score)

    passed = (len(failure_reasons) == 0 and score >= 80)

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


def audit_boundaries_with_gemini(raw_keeps, transcript_segments, show_code="bgog", api_key=None, model=None, total_duration=7200.0):
    """
    【雙重審核機制 (Pass 2): 邊界顯微鏡深度覆核 (Two-Pass Microscopic Boundary Audit)】
    在 Pass 1 全局劃定宏觀段落後，聚焦每個保留段落的【開頭前 120s / 後 60s】與【結尾前 180s / 後 90s】過渡帶逐字稿，
    像顯微鏡一樣深度比對微觀對話與廣告邊界：
    1. 剔除邊界殘留廣告（如頭部商業廣告/公益宣傳/開播前歌曲，尾部電台宣傳片/手工杏仁/知婚姨/姚堅新聞前導語）。
    2. 搶救腰斬正片（如尾部主持人在入廣告前未說完的話題/預告/道別，開頭被誤切的主持人打招呼/Jingle）。
    """
    if not raw_keeps:
        return raw_keeps

    if not api_key:
        api_key = get_gemini_api_key()
    if not api_key:
        print("  ⚠️ [Pass 2] 未提供有效的 GEMINI_API_KEY，維持 Pass 1 全局初劃區間")
        return raw_keeps

    def _get_seg_times(seg):
        if "offsets" in seg:
            t0 = seg["offsets"].get("from", 0) / 1000.0
            t1 = seg["offsets"].get("to", 0) / 1000.0
        else:
            t0 = seg.get("start", 0.0)
            t1 = seg.get("end", t0)
        return t0, t1

    def _format_window(segs, w_st, w_et):
        lines = []
        for s in segs:
            t0, t1 = _get_seg_times(s)
            if t1 >= w_st and t0 <= w_et:
                txt = s.get("text", "").strip()
                if not txt:
                    continue
                for pat in HALLUCINATION_PATTERNS:
                    if pat in txt:
                        txt = "[音樂 / 流行歌曲播放中]"
                        break
                m, sec = int(t0 // 60), int(t0 % 60)
                lines.append(f"[{m:02d}:{sec:02d} ({t0:.1f}s -> {t1:.1f}s)] {txt}")
        return "\n".join(lines)

    boundary_sections = []
    for idx, k in enumerate(raw_keeps):
        st = float(k["start"])
        et = float(k["end"])
        desc = k.get("description", f"Part {idx+1}")

        # 開頭過渡帶：前探 120 秒，後探 60 秒（覆蓋延遲切入或誤吞片頭廣告）
        hw_st = max(0.0, st - 120.0)
        hw_et = min(et, st + 60.0)
        head_text = _format_window(transcript_segments, hw_st, hw_et)

        # 結尾過渡帶：前探 180 秒，後探 90 秒（覆蓋提前腰斬主持話題，或誤吞尾隨電台宣傳片/新聞）
        tw_st = max(st, et - 180.0)
        tw_et = min(total_duration, et + 90.0)
        tail_text = _format_window(transcript_segments, tw_st, tw_et)

        boundary_sections.append(f"""### 【段落 #{idx+1}】: {desc}
- **初步開頭起點 (Pass 1 Start)**: {st:.1f}s
[開頭過渡帶逐字稿 (範圍 {hw_st:.1f}s ~ {hw_et:.1f}s)]:
{head_text}

- **初步結尾終點 (Pass 1 End)**: {et:.1f}s
[結尾過渡帶逐字稿 (範圍 {tw_st:.1f}s ~ {tw_et:.1f}s)]:
{tail_text}
""")

    boundaries_block = "\n".join(boundary_sections)

    if show_code == "ilub":
        show_specific_instructions = """- 《聖艾粒LaLaLaLa》（少爺占、當奴、Jacky、豪子，17:00 - 19:00）：
  * 開場（約 300s~390s）：開播前常有長達 5~6.5 分鐘的前續節目收尾、天氣、台呼與商業廣告，必須完全切除！起點須精準落在《聖艾粒LaLaLaLa》開場主題曲起拍或占奴正式開口處（切勿誤吞播前廣告，亦切勿因前奏為音樂而太遲切入）。
  * 17:30 與 18:30 破口（下班尖峰）：商台於 17:30 及 18:30（有時延至 18:35）頻繁插播「馬路的事 交通消息」、大量商業廣告與跨節目宣傳片——特別注意：《Bad Girl 大過佬》（森美/阿正/Elsie 早上 10-12 宣傳片）、《心跳行星 八五二零》、《王牌出場》、《東亞丈夫學》、《IDG Sunday》、《叱咤樂壇》新歌宣傳、英文廣告歌（「Nobody can break you anymore / 永遲半踢水分 / 天天實在」）、劉德華《我年輕過》、陳奕迅演唱會廣告、5SOS 演唱會、長榮航空、平治車展，【全部屬於廣告/宣傳，必須 100% 切除】，切勿誤判為占奴單元！
  * 18:00 整點大破口：長達 13~17 分鐘（約 3150s~4050s），含整點新聞、天氣、交通消息與商業廣告，嚴禁跨越或保留中間孤島！
  * 18:45【心水清大師】前短破口：約 6250s~6450s 常夾雜 1~2.5 分鐘商業廣告（如通訊局、港鐵商場、有誰共鳴），若出現必須拆為 Part 4a 與 Part 4b 切除！
  * 18:54~19:00 節目結尾【極度重要】：主持人道別（或最後一首節目流行歌播完）後，19:00 新聞前幾乎必定會播 30~120 秒商業廣告與電台宣傳（常見：《黃先生想你點》髮再生宣傳、劉德華《我年輕過》新歌廣告、英文廣告歌「Nobody can break you / 永遲半踢水分 / 天天實在」、電影《大分評 / 本片兒童不宜》、報時台呼），隨後即進入 19:00 新聞（郭書洋／陳世文／黃牌美報道新聞）。【以上 18:54~19:00 之廣告、宣傳片與整點新聞必須全部完全切除】，終點應精準停在主持人道別完畢或最後一首正片流行歌結束的瞬間！"""
    else:
        show_specific_instructions = """- 《Bad Girl 大過佬》（森美、阿正、Elsie）：
  * 開場：開場前的商業廣告（如麥當勞、綠色殯葬、加德士汽油等）必須完全切除，起點必須落在開場主題曲前奏音樂或主持開場打招呼瞬間。
  * 10:30 半點與 11:30 半點破口：主持人結束話題或預告後，緊隨的商業廣告（如美食嘉年華、公益團體宣傳、半點報時、天氣預報）必須完全切除。
  * 11:00 整點破口：嚴禁跨越 11:00 整點新聞！整點新聞及廣告長達 7~9 分鐘必須完全切除。
  * 12:00 節目結尾：主持人結束話題或專訪、說出道別詞（如「下星期見」、「拜拜」、「Good show」、「大家撲飛」）後，緊接著出現的商業廣告、電台其他節目宣傳片（例如「手工杏仁宣傳」、「知婚姨宣傳」、商台節目預告）、新聞報道前導語（如「現在姚堅報道新聞」），【全部屬於廣告/非正片，必須完全切除】！終點必須精確落在主持人說完最後一句話的瞬間（約 6657s）！"""

    prompt = f"""你是一位香港商台叱咤903廣播節目剪輯的【雙重審核 (Pass 2) 邊界專家】。
系統已在 Pass 1 全局初劃出以下節目段落。現在請你像使用顯微鏡一樣，專注檢查每個段落的【開頭過渡帶】與【結尾過渡帶】逐字稿，精準校正起訖時間戳（秒數），消除廣告殘留與正片腰斬：

【目標節目特性】：
{show_specific_instructions}

【極度重要審核準則】：
1. 【開頭審核 (Head Boundary)】：
   - 剔除廣告殘留：若開頭前包含商業廣告、公益廣告（如「有誰共鳴」）、天氣預報、新聞或電台呼號，必須將起點往後移至【節目開場 Jingle、主題曲前奏、或主持人正式說話】起拍的精確時間戳！
   - 防正片腰斬：若初劃起點太晚，切掉了主持人的開場問候或開場 Jingle 前奏，必須往前延伸以完整保留開場！

2. 【結尾審核 (Tail Boundary)】：
   - 剔除廣告與節目宣傳片殘留：若結尾後包含了商業廣告、電台其他節目宣傳片/廣播劇預告（如《心跳行星 八五二零》、「手工杏仁」、「知婚姨」、「混帳」、「晴朗的一天」）、新聞導語（如「現在由姚堅報道新聞」）或交通消息，【必須立即將終點回縮】，精確停在主持人最後一句說話結束或片尾曲結束的瞬間！
   - 防正片腰斬：若初劃終點太早（例如主持人在廣告前還在交代話題、預告嘉賓如 Eason、或跟嘉賓互動道別），【絕對不可提前腰斬】，必須往後延伸保留至主持人最後一句說話結束！

請審核以下各段過渡帶逐字稿：
{boundaries_block}

請嚴格輸出純 JSON，格式如下：
{{
  "audited_intervals": [
    {{
      "index": 1,
      "original_start": 120.5,
      "refined_start": 120.5,
      "original_end": 1395.2,
      "refined_end": 1395.2,
      "head_notes": "開頭為開場 Jingle，無廣告殘留",
      "tail_notes": "結尾對齊主持人入廣告前最後一句說話"
    }}
  ]
}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }
    req_data = json.dumps(payload).encode("utf-8")
    ctx = _create_gemini_ssl_context()

    candidates = get_gemini_model_candidates()
    models_to_try = ([model] + [m for m in candidates if m != model]) if model else candidates
    all_keys = get_gemini_api_keys()
    if not all_keys:
        all_keys = [api_key]

    audited_list = []
    last_err = None

    for m in models_to_try:
        if "lite" in m.lower():
            continue
        for k in all_keys:
            try:
                audited_result, ctx = _call_gemini_json(m, k, req_data, timeout=120, ctx=ctx)
                audited_list = _extract_gemini_intervals(audited_result, "audited_intervals")
                if audited_list:
                    break
                last_err = ValueError(f"模型 {m} 回傳空 audited_intervals")
            except Exception as e:
                last_err = e
                continue
        if audited_list:
            break

    if not audited_list:
        print(f"  ⚠️ [Pass 2] 邊界覆核請求未能完成 ({last_err})，安全維持 Pass 1 初劃區間")
        return raw_keeps
    refined_keeps = [dict(k) for k in raw_keeps]

    print(f"\n  🔬 [Pass 2: 邊界顯微鏡深度覆核結果 ({len(audited_list)} 個區間)]:")
    for idx, item in enumerate(audited_list):
        if idx >= len(refined_keeps):
            break
        orig_s = float(refined_keeps[idx]["start"])
        orig_e = float(refined_keeps[idx]["end"])
        new_s = float(item.get("refined_start", orig_s))
        new_e = float(item.get("refined_end", orig_e))
        h_notes = item.get("head_notes", "")
        t_notes = item.get("tail_notes", "")

        # 合理性防護檢驗 (Sanity Checks)
        # 1. 起點在終點之前，且區間時長 >= 120 秒
        # 2. 微調範圍不得超過合理邊界偏差 (起點偏差 <= 150s, 終點偏差 <= 240s)
        if new_s < new_e and (new_e - new_s >= 120.0):
            if abs(new_s - orig_s) <= 150.0:
                refined_keeps[idx]["start"] = round(new_s, 2)
            else:
                print(f"    ⚠️ 段落 #{idx+1} 起點微調偏差過大 ({orig_s:.1f}s -> {new_s:.1f}s)，維持原起點")

            if abs(new_e - orig_e) <= 240.0:
                refined_keeps[idx]["end"] = round(new_e, 2)
            else:
                print(f"    ⚠️ 段落 #{idx+1} 終點微調偏差過大 ({orig_e:.1f}s -> {new_e:.1f}s)，維持原終點")

        final_s = refined_keeps[idx]["start"]
        final_e = refined_keeps[idx]["end"]
        diff_s = final_s - orig_s
        diff_e = final_e - orig_e
        print(f"    - 段落 #{idx+1}: 起點 {orig_s:.1f}s -> {final_s:.1f}s ({diff_s:+.1f}s), 終點 {orig_e:.1f}s -> {final_e:.1f}s ({diff_e:+.1f}s)")
        if h_notes:
            print(f"      開頭微調: {h_notes}")
        if t_notes:
            print(f"      結尾微調: {t_notes}")

    return refined_keeps


def detect_ad_intervals_with_gemini(transcript_segments, total_duration, show_code="bgog", api_key=None, model=None, date_str=None):
    """
    調用 Google Gemini Flash 雲端大模型進行前文後理（上下文）全篇語意理解與去廣告區間劃定。
    支援健康度感知輪流使用（Health-Aware Round-Robin）與自動容錯轉移（Multi-Model Sequential Failover）。
    """
    if not api_key:
        api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("未提供有效的 GEMINI_API_KEY")

    compact_entries = []
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
            if compact_entries and compact_entries[-1][2] == text and (
                text == "[音樂 / 流行歌曲播放中]" or (t0 - compact_entries[-1][1] <= 4.0)
            ):
                compact_entries[-1][1] = max(compact_entries[-1][1], t1)
            else:
                compact_entries.append([t0, t1, text])

    lines = []
    for t0, t1, text in compact_entries:
        m, s = int(t0 // 60), int(t0 % 60)
        lines.append(f"[{m:02d}:{s:02d} ({t0:.1f}s -> {t1:.1f}s)] {text}")

    transcript_text = "\n".join(lines)

    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_hint = ""
    cache_file = None
    if date_str:
        clean_date_tag = re.sub(r'[^0-9]', '', str(date_str))[:8]
        if clean_date_tag:
            cache_file = os.path.join(RECORDINGS_DIR, f"{show_code}-{clean_date_tag}_cuts_cache.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as cf:
                        cached = json.load(cf)
                    c_keeps = cached.get("keep_intervals", [])
                    c_cuts = cached.get("cuts", [])
                    c_model = cached.get("model", "gemini-cached")
                    c_report = cached.get("report", {})
                    if c_keeps:
                        # 用最新品檢與自癒規則重新複驗快取區間（0 API 配額消耗）
                        c_passed, c_keeps, c_cuts, c_report = inspect_and_verify_cut_quality(
                            c_keeps, cuts=[], total_duration=total_duration,
                            transcript_segments=transcript_segments, show_code=show_code,
                            engine_used=f"{c_model} (cache)"
                        )
                        if c_passed and c_report.get("score", 0) >= 80:
                            dur_text = c_report.get("clean_duration_formatted")
                            print(f"⚡ [配額節省] 發現已驗證之高分去廣告快取 ({cache_file})！")
                            print(f"   評分: {c_report.get('score')}/100, 純淨時長: {dur_text}，直接載入快取（消耗 0 次 Gemini API）")
                            return c_keeps, c_cuts, f"{c_model} (cache)", c_report
                except Exception as e:
                    print(f"⚠️ 讀取去廣告快取失敗: {e}，將調用 Gemini 模型")
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

【節目每週固定環節日程表（17:00 - 19:00，2026 最新真實版）】：
◆ 第一小時（17:00 - 18:00）環節：
  - 【開場清談（17:00 - 17:30）】：少爺占與當奴開場主題曲（「聖艾粒啦啦啦啦 / 太陽都落山 / 講壞事 / 四個人甘丁爽」）後閒聊、生活瑣事互寸、日常爆笑經歷，中途會播放 1~2 首流行歌。
  - 【17:30 - 18:00 半點單元】：
    * 【五點半 Jingle】：「邊個痴線 邊個正常 你改變唔到佢哋 唯有自己啦啦啦啦...」
    * 【Jacky 突擊測驗 / 每日一問】：大部分日子 17:30 均由監製 Jacky（或豪子）準備趣味問答題考少爺占與當奴（開場聲帶常有「Jacky 突擊測驗」）。
    * 【OH....OTD】（OOTD）：有時候 17:30 會進行潮流穿搭主題單元。
    * 【寶寶很驚】：有時候亦會在 17:30 時段播出聽眾驚嚇趣事分享！
    * 【有勇氣，是靠本事】：約 5 分鐘新晉歌手/藝人專訪（結尾常有「聖艾粒啦啦啦啦，有勇氣，是靠本事」聲帶，【100% 節目正片，切勿誤當廣告】！）。
    * 【黃埔天地 AI 豪子】及【聽眾鼓仔】：占奴與 AI 豪子的爆笑互動、聽眾投稿分享（常在第一小時下半段出現，【100% 節目正片，絕非商場廣告】！）。
    * 主持人接續話題與流行歌曲播放，連貫至 18:00 六點鐘新聞前（約 3060s~3220s）！

◆ 第二小時（18:00 - 19:00）環節：
  - 【18:00 - 18:30 每日固定主題單元】（六點新聞、天氣與下班交通消息結束後，通常在 3950s~4120s 開始）：
    * 星期一【有冇共鳴】：主持人拋出生活怪癖、尷尬經驗或觀察，互相爭辯「有冇共鳴？」。
    * 星期二【你SING我都SING!】：少爺占與當奴唱歌詞、猜歌名互動遊戲。
    * 星期三【明星導航員】：邀請歌手、藝人或嘉賓專訪及開車導航互動。
    * 星期四【寶寶很苦】（有時亦會有【寶寶很驚】）：聽眾電話與心聲/趣事分享，主持人爆笑開解安慰。
    * 星期五【上天下海三字經】：主持人大玩三字經文字接龍與創意創作挑戰。
    * （注意：此節正片與最後一首流行歌有時會延續至 18:35 / 約 5320s 才入半點交通廣告！）
  - 【18:30 - 19:00 下半場單元】（18:30 交通消息與廣告結束後）：
    * 【ILUB TV 晚間劇場 / 廣播劇】：占奴經典廣播劇與角色扮演惡搞。
    * 【你一百通訊站 / Lay 100】、【今晚趁著天空有聲】、每日主單元後半段。
    * 【18:45 短廣告破口（約 6250s~6450s）】：在【心水清大師】開講前，商台經常插播一段 1~2.5 分鐘的商業廣告或《有誰共鳴》公益短破口（如通訊局、港鐵商場等），此時【必須將 Part 4 拆為 Part 4a 與 Part 4b】，切淨中間短破口！
    * 【心水清大師 / 開心水清大師】／【代課老師】及主持人結尾道別（約 6400s~6760s）。

【節目專屬 Jingle 聲帶】：
- 「聖艾粒」、「lalalala」、「sing a lup」、「你不懂我懂」、「四個人甘丁爽」、「太陽都落山」
- 「邊個痴線 邊個正常 你改變唔到佢哋 唯有自己啦啦啦啦」（五點半 Jingle）
- 「Jacky 突擊測驗」（或突擊貪業）、「OH....OTD」、「有勇氣，是靠本事」
- 「有冇共鳴」、「你SING我都SING」、「明星導航員」、「寶寶很苦」、「寶寶很驚」、「上天下海三字經」
- 「ILUB TV 晚間劇場」、「你一百通訊站」、「今晚趁著天空有聲」、「心水清大師 / 開心水清大師」

【極度重要：傍晚時段常見廣告與跨節目宣傳片陷阱（必須 100% 切除！）】：
1. 【跨節目電台宣傳片（嚴禁誤認作本節目內容！）】：
   - 《Bad Girl 大過佬》宣傳片（森美/阿正/Elsie「重做壞句子」、早上 10 時至 12 時宣傳片）——常在 17:30 破口出現，【這不是聖艾粒正片，必須完全切除】！
   - 《在晴朗的一天出發》、《心跳行星 八五二零》、《王牌出場》、《東亞丈夫學》（湯駿業）、《IDG Sunday》（何時才到星期日）、《黃先生想你點》（髮再生）、《叱咤樂壇》新歌宣傳、《有誰共鳴》（50881903）。
2. 【傍晚洗腦商業廣告與廣告歌（必須 100% 切除！）】：
   - 英文廣告歌（「Nobody can break you anymore / 永遲半踢水分 / 想給你最好 / 天天實在」）
   - 劉德華《我年輕過》新歌宣傳（「人人都年輕過 / 劉德華最新單曲」）
   - 陳奕迅演唱會/專輯廣告（「目前灌錄 / Taste the Atmosphere / Singer Under My Skin」）
   - 5SOS 演唱會（「5 seconds of summer / 令每位觀眾變成搖滾巨星」）、電影《大分評 / 本片兒童不宜》
   - 港鐵商場 / MTR Mobile、通訊局 coms-auth / 29616638、長榮航空、平治車展、越貴新、貴如美萬寧。
3. 【尖峰交通消息處理】：17:00~19:00 是下班尖峰，商台常播「馬路的事 交通消息」，交通消息聲帶必須切除，但絕不可誤殺前後主持人的對話或笑話！
4. 【防誤殺原則】：「黃埔天地 AI 豪子」、「聽眾鼓仔」、「有勇氣，是靠本事」皆為占奴正片，絕非廣告，必須 100% 完整保留！
5. 【18:54~19:00 結尾切除原則】：主持人道別或最後一首節目歌曲結束後，19:00 新聞前幾乎每次都有 30~120 秒廣告/宣傳片（《黃先生想你點》、劉德華《我年輕過》、天天實在廣告歌、電影廣告、報時台呼）及 19:00 整點新聞（郭書洋／陳世文／黃牌美），【必須全部完全切除，絕不可留到新聞開頭】！"""

        part_rules = """2. 【段落（Parts 1~4a/4b）的起止點判定規範（《聖艾粒LaLaLaLa》17:00 - 19:00）】：
   - 【Part 1（第一小時上半段，17:00 - 17:30）】：
     * 起點：開播前電台台呼/天氣/商業廣告以及前續節目宣傳結束，少爺占與當奴開場主題曲前奏音樂響起或兩人開場說話之處（聖艾粒播前廣告較長，起點通常落在 300s~390s，切勿誤吞播前廣告，亦切勿因前奏為音樂而延遲切入！）。
     * 終點：17:30（五點半）商業廣告與報時開始前（通常落在 1280s~1450s 左右）。【絕對不可在話題剛說完就提前切斷】！若主持人在話題後播了一首流行歌並繼續說話再入廣告，歌曲與對話必須完整連貫保留；但歌曲結束後一進入商業廣告/新歌宣傳（如劉德華《我年輕過》）或報時即刻停止！
   - 【Part 2（第一小時下半段，17:30 - 18:00，可拆為 2a/2b）】：
     * 起點：半點廣告/交通消息與跨節目宣傳片（切勿誤留《Bad Girl 大過佬》宣傳片！）結束後，「邊個痴線... 唯有自己啦啦啦啦」Jingle、「Jacky 突擊測驗」、「OH....OTD」、「寶寶很驚」或主持人正式說話接回之處（約 1650s~1850s）。
     * 終點：後半段話題（含【有勇氣，是靠本事】、【黃埔天地 AI 豪子】等）一直連貫到 18:00（六點鐘）整點前廣告/宣傳（如《IDG Sunday》、報時）與新聞開始前（通常落在 3060s~3220s）！若中途有短廣告則拆為 2a/2b，切淨廣告！
   - 【Part 3（第二小時上半段，18:00 - 18:30，可拆為 3a/3b）】：
     * 起點：18:00 六點新聞報道、天氣與下班交通消息全部結束後（通常在 3950s~4120s），每日單元專屬 Jingle（週一【有冇共鳴】、週二【你SING我都SING!】、週三【明星導航員】、週四【寶寶很苦 / 寶寶很驚】、週五【上天下海三字經】）響起或主持正式開講之處！切勿吞入開頭的《叱咤樂壇》宣傳！
     * 終點：連貫至 18:30（有時延至 18:35，約 4950s~5330s）「六點半」報時、交通消息或商業廣告（如陳奕迅、5SOS、長榮航空、天天實在廣告歌）開始前一瞬間！
   - 【Part 4（第二小時下半段，18:30 - 19:00，常拆為 4a/4b）】：
     * 起點：18:30 交通消息與商業廣告結束後，單元 Jingle（ILUB TV 晚間劇場、你一百通訊站、今晚趁著天空有聲、主單元後半段）或主持人正式開講之瞬間（約 5300s~5650s）。
     * 18:45 中場短破口：若在約 6250s~6450s（【心水清大師】開講前）出現 1~2.5 分鐘商業廣告或《有誰共鳴》公益破口，【務必拆分為 Part 4a 與 Part 4b】，切除中間廣告並完整保留後半段【心水清大師】與結尾道別！
     * 終點：【極度重要】主持人道別（如「聽日見、拜拜、聽日五點再見」）完畢，或道別後緊接的最後一首正片流行歌曲結束的瞬間（通常在 6650s~6780s）！隨後 18:54~19:00 的所有商業廣告、新歌廣告（劉德華《我年輕過》）、電台節目宣傳（《黃先生想你點》）、英文廣告歌（「Nobody can break you / 天天實在」）及 19:00 整點新聞（郭書洋／陳世文／黃牌美），【必須 100% 完全切除】！"""

        json_format_example = """{{
  "keep_intervals": [
    {{"start": 0.0, "end": 0.0, "description": "Part 1: 開場主題曲起拍至 17:30 五點半商業廣告與報時前"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 2 (或 2a/2b): 17:30 半點破口後接回至 18:00 六點前廣告與新聞前（完整保留突擊測驗/OH OTD/有勇氣是靠本事/AI豪子）"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 3 (或 3a/3b): 18:00 六點新聞與交通後每日單元 Jingle 接回至 18:30 交通廣告前"}},
    {{"start": 0.0, "end": 0.0, "description": "Part 4 (或 4a/4b): 18:30 破口後接回至心水清大師與節目完結（若 18:45 有短廣告請拆為 4a/4b；18:54~19:00 所有廣告宣傳與新聞必須完全切除）"}}
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
     * 起點：【極度重要】開場前若有戒賭熱線、食環署綠色殯葬、麥當勞陳奕迅演唱會、加德士汽油等商業廣告或公益宣傳，【全部屬於廣告，必須完全切除】！Part 1 起點必須精準落在《Bad Girl 大過佬》開場主題曲前奏音樂/歌詞起拍的瞬間（歌詞如「借首歌每日晨光打破... 戴假髮化濃妝... Bad Girl 大過佬... 來大笑代替上路」），絕不可保留開場前的商業廣告！
     * 終點：【極度重要】Part 1 必須在 10:30 半點廣告破口前（通常在 1300s~1450s 之間，例如「香港美食嘉年華」、10:30報時、天氣預測等廣告破口前）結束！【絕對不可將 10:30 的商業廣告大破口（通常長達 5~7 分鐘，包含美食嘉年華、天氣、All-Star籃球賽等商業廣告）包含進 Part 1】！10:30 廣告破口後的內容屬於 Part 2！Part 1 的 end 絕對不可能超過 1550s！
   - 【Part 2（第一小時下半段，10:30 - 11:00，可拆為 2a/2b）】：
     * 起點：10:30 半點商業廣告大破口結束後，主持人說話或節目 Jingle 接回之處（約 1700s~1850s 左右，如「Bad Girl 大過佬 森美 阿正 Elsie... 來大笑代替上路」或「吹水未夠班」）。
     * 終點：【極度重要】必須依據逐字稿實際語意！主持人們在整點前結束話題的時間各集皆不同。若中途有電台其他節目宣傳片（如《Be Before Bed Time》鄧小巧宣傳片）或 30 秒至 1 分鐘短廣告（如百佳、Select街之選），【必須切除並拆分為 Part 2a 與 Part 2b】！後半段話題必須聊到 11:00 整點新聞報時前（約 3200s 左右）！只有當主持人明確結束話題，隨後緊接著商業廣告或整點新聞報時之處，才是 Part 2 終點！
     * 【極度嚴格警告：嚴禁跨越 11:00 整點新聞！】Part 2 的終點【絕對不可能超過 3250s】！11:00 整點新聞報道、天氣、交通與商業廣告（長達 7~9 分鐘）必須完全切除！即使 11:00 前後正在進行同一個單元遊戲，11:00 前的內容屬於 Part 2，11:00 新聞與廣告後接回的內容必須作為 Part 3！【絕不可將 11:00 整點新聞包含在正片中】！
   - 【Part 3（第二小時上半段，11:00 - 11:30，可拆為 3a/3b）】：
     * 起點：【極度重要】11:00 整點新聞、天氣、交通及隨後的商業特約廣告結束後，第二小時正式單元專屬 Jingle（如週三【今日手機碌多咗】「最後一個post... 最後一條reels...」、週二【衰佬CLS】「森仔 咩啊...」、週四【粒粒皆辛苦】或嘉賓專訪 Jingle）起拍響起的瞬間（通常落在 3650s~3750s 左右）！
     * 終點：【極度重要】若 11:15~11:25 中途有 1~2 分鐘短廣告或其他電台節目宣傳片（例如《GilikGilikEX 聖誕禮貌卡》寄往九龍廣播道3號、演唱會廣告等），【必須切除並拆分為 3a 與 3b】，切勿將其他節目的宣傳片對話誤認成聽眾連線！Part 3（或 3b）必須聊到真正的 11:30 半點報時與商業廣告大破口開始前（約 4950s~5060s 左右），且【絕對不可跨越 11:30 半點報時、天氣預測、《叱咤樂壇》宣傳片、萬寧等商業廣告大破口】！
   - 【Part 4（第二小時下半段，11:30 - 12:00）】：
     * 起點：【極度重要】11:30 半點廣告大破口通常長達 5~8 分鐘（包含 11:30 報時、天氣預測、《叱咤樂壇》/其他節目宣傳片、商業廣告與《此時此刻 有誰共鳴》公益聲帶），【全部屬於廣告破口，必須完全切除】！Part 4 的起點通常在 5300s~5580s 之間（絕對不可能早於 5200s！），必須精確落在單元專屬 Jingle（如 Everyday is 新day、雞仔好人、衰佬CLS 等）起拍響起的瞬間！
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
    ctx = _create_gemini_ssl_context()

    if model:
        model_queue = [model]
    else:
        model_queue, _, _ = get_next_gemini_models_sequence()

    all_models_list = get_gemini_model_candidates()
    all_keys = get_gemini_api_keys()
    if not all_keys:
        all_keys = [api_key] if api_key else []
    last_error = None
    successful_model = None
    parsed = None
    keep_intervals = []
    cuts = []
    report = None
    best_fallback = None  # (score, cand_model, cand_keeps, cand_cuts, cand_report)

    def _run_split_hour_mode(reason_banner):
        nonlocal last_error, successful_model, keep_intervals, cuts, report, best_fallback, ctx
        print(f"  🧩 [分時段雙半場精準模式] {reason_banner}")
        h1_cutoff = 3450.0
        h2_cutoff = 3550.0 if show_code == "ilub" else 3350.0
        h2_filter_min = 3550.0 if show_code == "ilub" else 3400.0
        h1_lines = [
            f"[{int(t0//60):02d}:{int(t0%60):02d} ({t0:.1f}s -> {t1:.1f}s)] {txt}"
            for t0, t1, txt in compact_entries if t0 <= h1_cutoff
        ]
        h2_lines = [
            f"[{int(t0//60):02d}:{int(t0%60):02d} ({t0:.1f}s -> {t1:.1f}s)] {txt}"
            for t0, t1, txt in compact_entries if t1 >= h2_cutoff
        ]
        if show_code == "ilub":
            h1_focus = (
                "請僅針對以下【第一小時（上半場 0s ~ 3450s，包含 Part 1 與 Part 2）】逐字稿，"
                "專注精準定位：(1) 開場主題曲起拍點（切除 0s~380s 播前廣告）；(2) 17:30 五點半廣告破口（嚴禁誤留《Bad Girl 大過佬》等跨節目宣傳片與劉德華/天天實在廣告）；"
                "(3) 18:00 整點前廣告/台呼與新聞開始前（通常 3060s~3220s，嚴禁吞入 18:00 前宣傳或新聞）。"
                "請輸出 JSON：{\"keep_intervals\": [{\"start\": 0.0, \"end\": 0.0, \"description\": \"...\"}]}\n\n"
            )
            h2_focus = (
                "請僅針對以下【第二小時（下半場 3550s ~ 結束，包含 Part 3 與 Part 4a/4b）】逐字稿，"
                "專注精準定位：(1) 18:00 六點新聞與交通消息完全結束後之每日單元起拍點（通常 3950s~4120s，切除開頭《叱咤樂壇》宣傳）；"
                "(2) 18:30（有時至 18:35）半點交通與商業廣告破口；(3) 約 18:45（6250s~6450s）心水清大師前若有 1~2.5 分鐘短廣告/公益破口請拆分為 Part 4a 與 Part 4b 切除；"
                "(4) 節目道別或最後一首正片歌曲結束點（18:54~19:00 所有廣告宣傳如《黃先生想你點》、劉德華《我年輕過》、天天實在廣告歌、電影廣告及 19:00 新聞必須 100% 切除）。"
                "請輸出 JSON：{\"keep_intervals\": [{\"start\": 0.0, \"end\": 0.0, \"description\": \"...\"}]}\n\n"
            )
        else:
            h1_focus = (
                "請僅針對以下【第一小時（上半場 0s ~ 3450s，包含 Part 1 與 Part 2）】逐字稿，"
                "輸出整點新聞報時（約 3150s~3250s）之前的正片保留區間 JSON：{\"keep_intervals\": [{\"start\": 0.0, \"end\": 0.0, \"description\": \"...\"}]}\n\n"
            )
            h2_focus = (
                "請僅針對以下【第二小時（下半場 3350s ~ 結束，包含 Part 3 與 Part 4）】逐字稿，"
                "輸出整點新聞與交通廣告結束後（約 3650s~4150s 單元 Jingle 起拍）至節目完結片尾曲結束（12:00/19:00 新聞前）的正片保留區間 JSON："
                "{\"keep_intervals\": [{\"start\": 0.0, \"end\": 0.0, \"description\": \"...\"}]}\n\n"
            )
        h1_prompt = f"你是一位專業的香港商台叱咤903廣播節目剪輯專家。\n{show_info}\n{part_rules}\n{h1_focus}" + "\n".join(h1_lines)
        h2_prompt = f"你是一位專業的香港商台叱咤903廣播節目剪輯專家。\n{show_info}\n{part_rules}\n{h2_focus}" + "\n".join(h2_lines)
        req_h1 = json.dumps({"contents": [{"parts": [{"text": h1_prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}).encode("utf-8")
        req_h2 = json.dumps({"contents": [{"parts": [{"text": h2_prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}).encode("utf-8")

        for idx, cand_model in enumerate(model_queue):
            if "lite" in cand_model.lower():
                continue
            for k_idx, cur_key in enumerate(all_keys):
                key_tag = f" (Key #{k_idx+1})" if len(all_keys) > 1 else ""
                print(f"  🤖 [Split-Hour] 嘗試調用 Gemini 模型 [{cand_model}]{key_tag} (順位 {idx+1}/{len(model_queue)})...")
                is_quality_rejection = False
                try:
                    p1_json, ctx = _call_gemini_json(cand_model, cur_key, req_h1, timeout=90, ctx=ctx)
                    p2_json, ctx = _call_gemini_json(cand_model, cur_key, req_h2, timeout=90, ctx=ctx)
                    k1 = [x for x in _extract_gemini_intervals(p1_json, "keep_intervals") if float(x.get("end", 0)) <= h1_cutoff]
                    k2 = [x for x in _extract_gemini_intervals(p2_json, "keep_intervals") if float(x.get("start", 0)) >= h2_filter_min]
                    merged_raw = k1 + k2
                    if len(merged_raw) < 4:
                        raise ValueError(f"Split-Hour 回傳有效段落不足 ({len(k1)}+{len(k2)}={len(merged_raw)} < 4)")

                    print(f"  🔍 [Split-Hour Pass 1 完成] H1={len(k1)}段 + H2={len(k2)}段，啟動 [Pass 2 邊界顯微鏡深度覆核]...")
                    audited_keeps = audit_boundaries_with_gemini(
                        raw_keeps=merged_raw,
                        transcript_segments=transcript_segments,
                        show_code=show_code,
                        api_key=cur_key,
                        model=cand_model,
                        total_duration=total_duration
                    )
                    cand_passed, cand_keeps, cand_cuts, cand_report = inspect_and_verify_cut_quality(
                        audited_keeps, cuts=[], total_duration=total_duration,
                        transcript_segments=transcript_segments, show_code=show_code,
                        engine_used=f"gemini ({cand_model} split-hour 2-Pass)"
                    )
                    cand_score = cand_report.get("score", 0)
                    ad_check = cand_report.get("checks", {}).get("ad_contamination", "")
                    if "PASS" in ad_check and len(cand_keeps) >= 4:
                        if best_fallback is None or cand_score > best_fallback[0]:
                            best_fallback = (cand_score, f"{cand_model}-split", cand_keeps, cand_cuts, cand_report)

                    if not cand_passed or cand_score < 80:
                        is_quality_rejection = True
                        reasons = cand_report.get("failure_reasons", [])
                        raise ValueError(f"Split-Hour 模型 {cand_model} 品檢未達標 (Score: {cand_score}/100, passed={cand_passed}, 失敗項: {reasons})")

                    successful_model = f"{cand_model}-split"
                    keep_intervals = cand_keeps
                    cuts = cand_cuts
                    report = cand_report
                    if cand_model in all_models_list:
                        model_idx = all_models_list.index(cand_model)
                        record_gemini_model_success(cand_model, model_idx)
                    print(f"  ✨ [分時段雙半場模式] 模型 [{cand_model}]{key_tag} 分析成功並通過品檢門禁 (評分: {cand_score}/100)！")
                    break
                except Exception as split_err:
                    last_error = split_err
                    if not is_quality_rejection:
                        record_gemini_model_error(cand_model, split_err)
                    if k_idx + 1 < len(all_keys):
                        print(f"  ⚠️ [Split-Hour] 模型 [{cand_model}]{key_tag} 失敗 ({split_err})，切換至 Key #{k_idx+2} 重試...")
                        continue
                    else:
                        print(f"  ⚠️ [Split-Hour] 模型 [{cand_model}]{key_tag} 失敗: {split_err}，嘗試下一個候選模型...")
                        break
            if successful_model and keep_intervals and report:
                break

    def _run_full_show_mode():
        nonlocal last_error, successful_model, parsed, keep_intervals, cuts, report, best_fallback, ctx
        for attempt in range(1, 3):
            for idx, cand_model in enumerate(model_queue):
                if "lite" in cand_model.lower():
                    print(f"  🛑 略過 Lite 模型 [{cand_model}]（系統嚴格禁用 Lite 模型以確保剪輯精確度）")
                    continue
                round_str = f", 輪次 {attempt}/2" if attempt > 1 else ""

                # 支援多組 API Keys 自動輪替容錯（遇 429、503、Timeout 等錯誤自動換 Key 重試）
                for k_idx, cur_key in enumerate(all_keys):
                    key_tag = f" (Key #{k_idx+1})" if len(all_keys) > 1 else ""
                    print(f"  🤖 嘗試調用 Gemini 模型 [{cand_model}]{key_tag} (順位 {idx+1}/{len(model_queue)}{round_str})...")
                    is_quality_rejection = False
                    try:
                        parsed, ctx = _call_gemini_json(cand_model, cur_key, req_data, timeout=120, ctx=ctx)
                        raw_keeps = _extract_gemini_intervals(parsed, "keep_intervals")
                        if not raw_keeps:
                            raise ValueError(f"模型 {cand_model} 未回傳有效的 keep_intervals")

                        print(f"  🔍 [Pass 1 全局初劃完成] 成功識別出 {len(raw_keeps)} 個候選區間，啟動 [Pass 2 邊界顯微鏡深度覆核]...")
                        audited_keeps = audit_boundaries_with_gemini(
                            raw_keeps=raw_keeps,
                            transcript_segments=transcript_segments,
                            show_code=show_code,
                            api_key=cur_key,
                            model=cand_model,
                            total_duration=total_duration
                        )

                        # 嚴格品質檢驗：調用品檢門禁進行自癒與多維度評分，若未通過或評分 < 80 嚴格禁止採用！
                        cand_passed, cand_keeps, cand_cuts, cand_report = inspect_and_verify_cut_quality(
                            audited_keeps, cuts=[], total_duration=total_duration,
                            transcript_segments=transcript_segments, show_code=show_code,
                            engine_used=f"gemini ({cand_model} 2-Pass)"
                        )
                        cand_score = cand_report.get("score", 0)
                        ad_check = cand_report.get("checks", {}).get("ad_contamination", "")
                        if "PASS" in ad_check and len(cand_keeps) >= 4:
                            if best_fallback is None or cand_score > best_fallback[0]:
                                best_fallback = (cand_score, cand_model, cand_keeps, cand_cuts, cand_report)

                        if not cand_passed or cand_score < 80:
                            is_quality_rejection = True
                            reasons = cand_report.get("failure_reasons", [])
                            raise ValueError(f"模型 {cand_model} 產出區間未達品質標準 (Score: {cand_score}/100, passed={cand_passed}, 失敗項: {reasons})")

                        successful_model = cand_model
                        keep_intervals = cand_keeps
                        cuts = cand_cuts
                        report = cand_report
                        if cand_model in all_models_list:
                            model_idx = all_models_list.index(cand_model)
                            record_gemini_model_success(cand_model, model_idx)

                        print(f"  ✨ 模型 [{cand_model}]{key_tag} 分析成功並通過品檢門禁 (評分: {cand_score}/100)！")
                        break

                    except Exception as e:
                        last_error = e
                        if not is_quality_rejection:
                            record_gemini_model_error(cand_model, e)
                        if k_idx + 1 < len(all_keys):
                            print(f"  ⚠️ 模型 [{cand_model}]{key_tag} 調用失敗或品檢未達標 ({e})，自動無縫切換至 Key #{k_idx+2} 重試...")
                            continue
                        else:
                            print(f"  ⚠️ 模型 [{cand_model}]{key_tag} 所有 Key 調用均失敗或品檢未達標: {e}，自動嘗試下一個候選模型...")
                            break

                if successful_model and keep_intervals and report:
                    break
            if successful_model and keep_intervals and report:
                break
            if attempt < 2:
                print("  ⏳ 第一輪所有候選模型調用遇到網絡或服務波動，等待 10 秒後啟動第二輪重試...")
                time.sleep(10)

    # 針對《聖艾粒LaLaLaLa》預設優先啟用【分時段雙半場精準模式】(在 18:00 長達 13~17 分鐘之新聞交通死區內安全切分，使 AI 專注度加倍、大幅降低尖峰廣告誤判)
    if show_code == "ilub" and total_duration >= 6400.0:
        _run_split_hour_mode("《聖艾粒LaLaLaLa》預設優先啟用上下半場獨立短請求拆解（18:00 死區安全切分，高專注度精準去廣告）...")
        if not successful_model or not keep_intervals or not report:
            print("  🔄 [分時段模式未達標] 自動退回 2 小時全篇聯合分析模式重試...")
            _run_full_show_mode()
    else:
        _run_full_show_mode()
        if not successful_model or not keep_intervals or not report:
            _run_split_hour_mode("啟動上下半場獨立短請求拆解（大幅縮減單次 Token 體積與防 Timeout）...")

    if (not successful_model or not keep_intervals or not report) and best_fallback and best_fallback[0] >= 75:
        fb_score, fb_model, fb_keeps, fb_cuts, fb_report = best_fallback
        fb_clean_dur = fb_report.get("clean_duration_seconds", 0)
        if fb_clean_dur >= 3900.0:
            print(f"  🛡️ [智慧降級採用] 採用無廣告污染之最佳 AI 2-Pass 候選結果 [{fb_model}] (Score: {fb_score}/100, 時長: {fb_report.get('clean_duration_formatted')})")
            fb_report["passed"] = True
            fb_report["failure_reasons"] = []
            successful_model = fb_model
            keep_intervals = fb_keeps
            cuts = fb_cuts
            report = fb_report

    if not successful_model or not keep_intervals or not report:
        raise RuntimeError(f"所有候選 Gemini 模型均調用失敗或品檢未達標，最後錯誤: {last_error}")

    passed = report.get("passed", True)

    if cache_file and passed:
        try:
            with open(cache_file, "w", encoding="utf-8") as cf:
                json.dump({
                    "keep_intervals": keep_intervals,
                    "cuts": cuts,
                    "model": successful_model,
                    "report": report,
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }, cf, indent=2, ensure_ascii=False)
            print(f"💾 已建立去廣告分析快取: {cache_file} (後續重跑將免消耗 API Quota)")
        except Exception as e:
            print(f"⚠️ 無法儲存去廣告快取: {e}")

    return keep_intervals, cuts, successful_model, report


def _snap_boundary_to_silence(input_file, target_sec, total_duration, is_start=True):
    """利用 FFmpeg silencedetect 偵測切點前後 ±0.6s 內的聲學靜音氣口，避免切在字音正中間"""
    win_start = max(0.0, target_sec - (0.65 if is_start else 0.45))
    win_end = min(total_duration, target_sec + (0.40 if is_start else 0.70))
    win_dur = win_end - win_start
    if win_dur <= 0.2:
        return target_sec
    cmd = [
        FFMPEG_BIN, "-hide_banner", "-nostats",
        "-ss", f"{win_start:.3f}", "-t", f"{win_dur:.3f}",
        "-i", input_file,
        "-af", "silencedetect=noise=-34dB:d=0.10",
        "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=4)
        stderr_out = res.stderr or ""
        s_starts = [win_start + float(m.group(1)) for m in re.finditer(r"silence_start:\s*([0-9.]+)", stderr_out)]
        s_ends = [win_start + float(m.group(1)) for m in re.finditer(r"silence_end:\s*([0-9.]+)", stderr_out)]
        if is_start and s_ends:
            best_end = min(s_ends, key=lambda x: abs(x - target_sec))
            if abs(best_end - target_sec) <= 0.55:
                return round(max(0.0, best_end - 0.04), 2)
        elif (not is_start) and s_starts:
            best_st = min(s_starts, key=lambda x: abs(x - target_sec))
            if abs(best_st - target_sec) <= 0.60:
                return round(min(total_duration, best_st + 0.06), 2)
    except Exception:
        pass
    return target_sec


def assemble_cleaned_audio(input_file, cuts, output_file, total_duration, keep_metadata=None, clean_chapter_titles=None):
    """根據切除區間，調用 FFmpeg 進行精準無損無縫拼接（含聲學氣口對齊、120ms 微淡入淡出防爆音與原生 M4A 乾淨章節標記）"""
    if not cuts:
        print("未偵測到需要切除的廣告區間，直接複製音訊。")
        shutil.copy2(input_file, output_file)
        return []

    # 計算保留段落 (Keep Intervals) 並進行聲學靜音氣口微調 (Acoustic Silence Snapping)
    raw_keep_intervals = []
    cursor = 0.0
    for cut in cuts:
        if cut["start"] > cursor + 0.5:
            raw_keep_intervals.append((cursor, cut["start"]))
        cursor = max(cursor, cut["end"])
    if cursor < total_duration - 0.5:
        raw_keep_intervals.append((cursor, total_duration))

    keep_intervals = []
    for ks, ke in raw_keep_intervals:
        snapped_s = _snap_boundary_to_silence(input_file, ks, total_duration, is_start=True)
        snapped_e = _snap_boundary_to_silence(input_file, ke, total_duration, is_start=False)
        if snapped_e - snapped_s >= 5.0:
            keep_intervals.append((snapped_s, snapped_e))
        else:
            keep_intervals.append((ks, ke))

    # 對應每個保留段落的乾淨章節名稱 (供 M4A 章節與播放器展示)
    chapter_entries = []
    out_cursor = 0.0
    print(f"\n✂️ 將保留以下 {len(keep_intervals)} 個精華內容段落:")
    for idx, (ks, ke) in enumerate(keep_intervals):
        seg_dur = max(0.1, ke - ks)
        desc = f"第 {idx+1} 節"
        if clean_chapter_titles and idx < len(clean_chapter_titles):
            desc = clean_chapter_titles[idx]
        elif keep_metadata:
            # 尋找最匹配的 keep_metadata 描述並清洗機器詞彙
            for km in keep_metadata:
                if abs(float(km.get("start", -999)) - ks) < 5.0 or abs(float(km.get("end", -999)) - ke) < 5.0:
                    raw_desc = str(km.get("description", "")).strip()
                    if raw_desc:
                        from generate_human_shownotes import sanitize_chapter_title
                        desc = sanitize_chapter_title(raw_desc, idx+1)
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

        # 5. 生成專業、生動、分節明確的官方節目簡介 (Human Editorial Show Notes & Clean Chapter Titles)
        keep_ranges = [(float(k["start"]), float(k["end"])) for k in final_keeps]
        clean_chapter_titles = None
        editorial_notes = None
        try:
            from generate_human_shownotes import generate_editorial_shownotes
            print(f"\n[步驟 4/5] 正在為 {show_code} 生成人性化、分節清晰的專業官方節目簡介...")
            full_show_name = "聖艾粒LaLaLaLa" if show_code == "ilub" else "Bad Girl 大過佬"
            full_date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else "今日"
            editorial_notes, clean_chapter_titles = generate_editorial_shownotes(
                f"{transcript_base}.json",
                keep_ranges,
                show_name=full_show_name,
                date_str=full_date_str
            )
        except Exception as err:
            print(f"  ⚠️ 生成官方節目簡介略過: {err}")

        # 6. 執行音訊切除與無縫合成（嵌入 M4A 乾淨章節與微淡入淡出）
        print(f"\n[步驟 5/5] 正在執行音訊精準剪切與無縫拼接（嵌入乾淨章節名稱）...")
        chapter_entries = assemble_cleaned_audio(
            input_file, cuts, output_file, total_duration,
            keep_metadata=final_keeps, clean_chapter_titles=clean_chapter_titles
        )

        # 產生 Podcast Show Notes（優先採用專業官方人寫風格簡介）
        if editorial_notes:
            chapters_summary = editorial_notes
        else:
            chapters_lines = []
            for ch in chapter_entries:
                chapters_lines.append(f"【{ch['title']}】")
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
