import requests
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
    
    # 檢查最近 8 天 (補回 2/24 之後缺少的集數)
    for i in range(8):
        target_date = now_hk - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        if target_date.weekday() >= 5: continue
        
        # 排除尚未上架的今天
        if i == 0 and int(now_hk.strftime("%H%M")) < 1200: continue
        
        # 檢查 RSS
        if is_in_rss(date_str): continue

        print(f"[{PODCAST_NAME}] 正在透過 Google 抓取日期: {date_str}")
        html = fetch_html_via_proxy(SHOW_PAGE)
        
        # 解析正確連結
        pattern = rf'src="(https?://[^"]*{date_str}[^"]*\.aac)"'
        match = re.search(pattern, html)
        
        if match:
            actual_url = match.group(1)
            add_to_rss(actual_url, date_str, target_date)
        else:
            print(f"[{PODCAST_NAME}] 網頁上暫無 {date_str} 的連結。")

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
