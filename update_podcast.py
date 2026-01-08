import requests
import re
from datetime import datetime, timedelta, timezone
import os

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
SHOW_PAGE_URL = "https://hkfm903.live/?show=Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC"
RSS_FILE = "rss.xml"
# ----------------

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    
    print(f"[{PODCAST_NAME}] 正在掃描網頁，尋找所有可用的節目存檔...")

    try:
        # 1. 抓取網頁
        page_response = requests.get(SHOW_PAGE_URL, timeout=15)
        page_response.encoding = 'utf-8'
        
        # 2. 找出所有 .aac 連結 (格式: recordings/.../YYYYMMDD_xxxx_....aac)
        # 我們抓取包含 8 位數字日期的所有連結
        links = re.findall(rf'recordings/[^"\']*(\d{{8}})[^"\']*\.aac', page_response.text)
        # 去重並排序（新的在前）
        unique_dates = sorted(list(set(links)), reverse=True)
        
        if not unique_dates:
            print(f"[{PODCAST_NAME}] 在網頁上找不到任何符合格式的 .aac 檔案。")
            return

    except Exception as e:
        print(f"爬取網頁出錯: {e}")
        return

    # 3. 讀取現有 RSS
    if not os.path.exists(RSS_FILE):
        print(f"錯誤: 找不到 {RSS_FILE}")
        return
        
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    updated = False
    
    # 4. 遍歷找到的日期，補齊缺失的集數 (只檢查最近 5 集，避免 RSS 過長)
    for date_str in unique_dates[:5]:
        guid = f"ilub-{date_str}"
        
        if guid not in rss_content:
            print(f"發現漏掉的集數: {date_str}，準備補入 RSS...")
            
            # 重新在網頁中找出該日期對應的完整網址
            match = re.search(rf'recordings/[^"\']*{date_str}_[^"\']*\.aac', page_response.text)
            if match:
                target_url = f"https://hkfm903.live/{match.group(0)}"
                
                # 建立日期物件供顯示
                item_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=hk_tz)
                pub_date_str = item_date.strftime("%a, %d %b %Y 19:00:00 +0800")
                
                new_item = f"""    <item>
      <title>{item_date.strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
      <pubDate>{pub_date_str}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{target_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
                # 插入 RSS
                rss_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
                updated = True
        else:
            print(f"集數 {date_str} 已存在，跳過。")

    # 5. 存回檔案
    if updated:
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{PODCAST_NAME}] RSS 已補齊最新集數！")
    else:
        print(f"[{PODCAST_NAME}] 沒有發現需要補入的新集數。")

if __name__ == "__main__":
    check_and_update()
