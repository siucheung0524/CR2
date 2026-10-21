#!/usr/bin/env python3
import json
import os
import re
import ssl
import time
import urllib.request
import certifi
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def get_gemini_api_keys():
    from ad_remover import get_gemini_api_keys as remover_keys
    return remover_keys()


def get_gemini_api_key():
    keys = get_gemini_api_keys()
    return keys[0] if keys else None

def generate_editorial_shownotes(transcript_json_path, keep_ranges, show_name, date_str):
    """
    調用 Gemini 模型池，為保留的正片段落撰寫專業、生動、分節明確的節目官方簡介
    （100% 人性化，絕無廣告、破口、Jingle、逐字稿等內部詞彙）
    """
    if not os.path.exists(transcript_json_path):
        return None, []

    with open(transcript_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segs = data.get("transcription", [])
    if not segs:
        return None, []

    # 提取每一節的精華對話文字（過濾空白）
    section_texts = []
    for idx, (ks, ke) in enumerate(keep_ranges):
        cur_texts = []
        for s in segs:
            t0 = s.get("offsets", {}).get("from", 0) / 1000.0 if "offsets" in s else s.get("start", 0.0)
            if ks <= t0 <= ke:
                txt = s.get("text", "").strip()
                if txt and not any(h in txt for h in ["请不吝点赞", "明镜需要", "字幕志愿者", "中文字幕"]):
                    cur_texts.append(txt)
        # 取前 120 句代表性文字供 AI 閱讀總結
        sample_str = " ".join(cur_texts[:120])
        section_texts.append(f"=== 第 {idx+1} 節對話節錄 (時長約 {int((ke-ks)/60)} 分鐘) ===\n{sample_str}")

    prompt = f"""你是一位專業的香港商台叱咤903電台節目官方專欄編輯。請閱讀以下《{show_name}》（日期：{date_str}）各節目的真實主持對話節錄。

請為這集節目撰寫一份【真正認真、專業、分節清晰且吸引聽眾收聽的 Podcast 節目簡介 (Show Notes)】。

【嚴格寫作規範】：
1. 【嚴禁出現任何機器/AI/剪輯詞彙】：絕對不可提及「破口」、「廣告」、「切除」、「Jingle」、「逐字稿」、「時間戳」、「前奏」、「商業贊助」、「剪輯」、「秒數」等字眼！讓讀者感覺這是一篇充滿人情味與趣味的電台官方節目導讀。
2. 【結構分明（一節一節分好）】：嚴格按照下方提供的 {len(keep_ranges)} 個節數，依序輸出【第一節】至【第{len(keep_ranges)}節】。
3. 【每一節包含吸睛標題與精華內文】：
   - 標題：生動有趣、點出該節核心話題或爆笑事件（10~25字）。
   - 簡介：2~3 句生動流暢的語意總結，詳細介紹主持人聊了什麼主題、分享了什麼搞笑經歷或日常奇事、聽眾電話互動故事、嘉賓專訪心聲、或播了哪首流行歌。
4. 【輸出純文字格式】：直接輸出格式如下，不要輸出 JSON，也不要有任何 Markdown 代碼塊標記：

📻 {date_str} 叱咤903《{show_name}》節目精華

【第一節】標題
內文簡介...

【第二節】標題
內文簡介...
"""

    payload = {
        "contents": [{"parts": [{"text": prompt + "\n\n各節對話內容：\n\n" + "\n\n".join(section_texts)}]}],
        "generationConfig": {"temperature": 0.3}
    }
    req_data = json.dumps(payload).encode("utf-8")

    all_keys = get_gemini_api_keys()
    if not all_keys:
        return None, []

    SHOWNOTES_GEMINI_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest"
    ]

    ctx = ssl.create_default_context(cafile=certifi.where())
    shownotes_text = None

    for cand_model in SHOWNOTES_GEMINI_MODELS:
        for k_idx, cur_key in enumerate(all_keys):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{cand_model}:generateContent?key={cur_key}"
                req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, data=req_data, context=ctx, timeout=45) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    shownotes_text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if shownotes_text and "【第一節】" in shownotes_text:
                        print(f"  ✨ 成功使用模型 [{cand_model}] (Key #{k_idx+1}) 生成人性化節目簡介！(高配額 Lite 池)")
                        break
            except Exception as e:
                print(f"  ⚠️ 模型 [{cand_model}] (Key #{k_idx+1}) 生成簡介暫時繁忙: {e}")
        if shownotes_text:
            break

    if not shownotes_text:
        return None, []

    # 解析出各節的乾淨標題供 M4A 章節使用 (例如 "第 1 節：名校舊生聯誼激罕聯乘女校")
    clean_titles = []
    lines = shownotes_text.splitlines()
    for line in lines:
        m = re.match(r'^[#*\s]*【第([一二三四五六七八九十\d]+)節】\s*(.+?)[#*\s]*$', line.strip())
        if m:
            sec_idx = m.group(1)
            raw_title = m.group(2).strip()
            # 轉換中文數字為阿拉伯數字方便播放器展示
            num_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
            norm_num = num_map.get(sec_idx, sec_idx)
            clean_titles.append(f"第 {norm_num} 節：{raw_title[:35]}")

    return shownotes_text, clean_titles


def sanitize_chapter_title(raw_title, index=1):
    """備用：將剪輯日誌描述清洗為適合聽眾播放器的優雅章節名稱"""
    title = str(raw_title).strip()
    title = re.sub(r'^(?:Part\s*\d+[a-z]?|段落\s*#?\d+[a-z]?)\s*[:：]\s*', '', title, flags=re.I)
    title = re.sub(r'（.*?切除.*?）', '', title)
    title = re.sub(r'\(.*?切除.*?\)', '', title)
    title = re.sub(r'(?:商業|新聞|廣告|公益)?(?:破口|廣告|新聞|Jingle|前奏|起拍|逐字稿|秒數|接回).*?(?:至|後|前)', '', title)
    title = re.sub(r'至.*?(?:結束|前)$', '', title)
    title = re.sub(r'[，,、\s]+$', '', title).strip()
    if not title or len(title) < 2:
        return f"第 {index} 節"
    if not title.startswith("第"):
        title = f"第 {index} 節：{title}"
    return title[:40]

