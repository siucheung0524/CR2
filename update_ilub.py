import requests
import re
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

# --- 配置資訊 ---
PODCAST_NAME = "聖艾粒LaLaLaLa"
SHOW_PAGE = "https://hkfm903.live/?show=%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa"
RSS_FILE = "ilub.xml"
GAS_PROXY_URL = "https://script.google.com/macros/s/AKfycbxy7W2J6pTtUCjghmXQ-HMYf6T-h2vLeT5NizNvaTrMUyAuYLnV83R13iLMrYbYrP3a/exec"

def fetch_html_via_proxy(url):
    proxy_url = f"{GAS_PROXY_URL}?url={urllib.parse.quote(url)}"
    try:
        r = requests.get(proxy_url, timeout=20)
        return r.text
    except: return ""

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    
    for i in range(3):
        target_date = now_hk - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        if target_date.weekday() >= 5: continue
        if i == 0 and int(now_hk.strftime("%H%M")) < 1915: continue
        
        if is_in_rss(date_str): continue

        print(f"[{PODCAST_NAME}] 正在透過 Google 抓取日期: {date_str}")
        html = fetch_html_via_proxy(SHOW_PAGE)
        
        # 尋找聖艾粒的連結
        pattern = rf'recordings/[^"\'\s>]*{date_str}_(\d{{4}})[^"\'\s>]*\.aac'
        match = re.search(pattern, html)
        
        if match:
            found_time = match.group(1)
            actual_url = f"https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/{date_str}_{found_time}_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"
            add_to_rss(actual_url, date_str, target_date)

def is_in_rss(date_str):
    if not os.path.exists(RSS_FILE): return False
    with open(RSS_FILE, "r", encoding="utf-8") as f: return f"ilub-{date_str}" in f.read()

def add_to_rss(url, date_str, date_obj):
    with open(RSS_FILE, "r", encoding="utf-8") as f: content = f.read()
    pub_date = date_obj.strftime("%a, %d %b %Y 19:15:00 +0800")
    new_item = f"""    <item>
      <title>{date_obj.strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">ilub-{date_str}</guid>
      <enclosure url="{url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(content.replace("    <item>", new_item + "    <item>", 1))
    print(f"✅ {date_str} 成功更新！")

if __name__ == "__main__":
    check_and_update()
