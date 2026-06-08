import requests
import time
import re
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
SHOW_PAGE = "https://hkfm903.live/?show=Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC"
RSS_FILE = "rss.xml"
# 貼上你剛剛部署的 Google 腳本網址
GAS_PROXY_URL = "https://script.google.com/macros/s/AKfycbxy7W2J6pTtUCjghmXQ-HMYf6T-h2vLeT5NizNvaTrMUyAuYLnV83R13iLMrYbYrP3a/exec"

def fetch_html_via_proxy(url):
    """透過 Google Proxy 抓取 HTML"""
    proxy_url = f"{GAS_PROXY_URL}?url={urllib.parse.quote(url)}"
    try:
        r = requests.get(proxy_url, timeout=20)
        return r.text
    except:
        return ""

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    
    # 1. 檢查並補回過去 7 天缺少的集數 (i from 1 to 7)
    for i in range(1, 8):
        target_date = now_hk - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        if target_date.weekday() >= 5: continue
        if is_in_rss(date_str): continue

        print(f"[{PODCAST_NAME}] 補抓過去日期: {date_str}")
        html = fetch_html_via_proxy(SHOW_PAGE)
        pattern = rf'src="([^"]*{date_str}[^"]*\.aac)"'
        match = re.search(pattern, html)
        if match:
            actual_url = match.group(1)
            if actual_url.startswith("/"):
                actual_url = "https://hkfm903.live" + actual_url
            add_to_rss(actual_url, date_str, target_date)
    
    # 2. 檢查今天的集數
    target_date = now_hk
    date_str = target_date.strftime("%Y%m%d")
    if target_date.weekday() >= 5:
        return
        
    if is_in_rss(date_str):
        print(f"[{PODCAST_NAME}] 今天的集數 {date_str} 已經在 RSS 中。")
        return
        
    broadcast_time = 1200
    
    # 輪詢檢查今天新集數 (最長輪詢 30 次，每次間隔 60 秒，共約 30 分鐘)
    max_attempts = 30
    for attempt in range(max_attempts):
        current_time = datetime.now(hk_tz)
        if int(current_time.strftime("%H%M")) < broadcast_time:
            print(f"[{PODCAST_NAME}] 當前時間 {current_time.strftime('%H:%M')} 尚未到上架時間 {broadcast_time}，今天先跳過。")
            break
            
        print(f"[{PODCAST_NAME}] 第 {attempt+1}/{max_attempts} 次嘗試抓取今天 ({date_str}) 的連結...")
        html = fetch_html_via_proxy(SHOW_PAGE)
        pattern = rf'src="([^"]*{date_str}[^"]*\.aac)"'
        match = re.search(pattern, html)
        
        if match:
            actual_url = match.group(1)
            if actual_url.startswith("/"):
                actual_url = "https://hkfm903.live" + actual_url
            add_to_rss(actual_url, date_str, target_date)
            break
        else:
            if attempt < max_attempts - 1:
                print(f"[{PODCAST_NAME}] 暫時未有連結，等待 60 秒後重試...")
                time.sleep(60)
            else:
                print(f"[{PODCAST_NAME}] 已達到最大嘗試次數，今日未找到新連結。")

def is_in_rss(date_str):
    if not os.path.exists(RSS_FILE): return False
    with open(RSS_FILE, "r", encoding="utf-8") as f: return f"bgog-{date_str}" in f.read()

def add_to_rss(url, date_str, date_obj):
    with open(RSS_FILE, "r", encoding="utf-8") as f: content = f.read()
    pub_date = date_obj.strftime("%a, %d %b %Y 12:05:00 +0800")
    new_item = f"""    <item>
      <title>{date_obj.strftime("%Y-%m-%d")} Bad Girl 大過佬</title>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">bgog-{date_str}</guid>
      <enclosure url="{url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(content.replace("    <item>", new_item + "    <item>", 1))
    print(f"✅ {date_str} 成功更新！")

if __name__ == "__main__":
    check_and_update()
