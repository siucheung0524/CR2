import requests
import re
from datetime import datetime, timedelta, timezone
import os

# --- 配置資訊 ---
PODCAST_NAME = "聖艾粒LaLaLaLa"
SHOW_PAGE_URL = "https://hkfm903.live/?show=%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa"
RSS_FILE = "ilub.xml"
# ----------------

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    
    print(f"[{PODCAST_NAME}] 開始檢查網頁: {SHOW_PAGE_URL}")

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        page_response = requests.get(SHOW_PAGE_URL, headers=headers, timeout=15)
        page_response.encoding = 'utf-8'
        html_content = page_response.text

        # 1. 抓取所有包含 .aac 的字串，不論它是 http, https 還是相對路徑 (/)
        # 這個正則會抓取任何引號內的 .aac 連結
        raw_links = re.findall(r'/[^"\'\s>]+\.aac|http[^"\'\s>]+\.aac', html_content)
        
        valid_links = []
        for link in raw_links:
            # 2. 處理網址：確保它是完整的 https 網址
            if link.startswith('http'):
                # 如果是 http，強制換成 https
                target_url = link.replace("http://", "https://")
            else:
                # 如果是相對路徑 /recordings/...，補上 https 前綴
                target_url = f"https://hkfm903.live{link}"
            
            # 3. 從網址中提取 8 位數日期
            date_match = re.search(r'(\d{8})', target_url)
            if date_match:
                date_str = date_match.group(1)
                valid_links.append((date_str, target_url))

        # 去重並按日期排序
        valid_links = sorted(list(set(valid_links)), key=lambda x: x[0], reverse=True)

        if not valid_links:
            print(f"[{PODCAST_NAME}] 網頁搜尋失敗，嘗試直接拼接今日網址...")
            # 最後手段：直接猜測 (以 1700 或 1701 這種常見格式)
            today_str = now_hk.strftime("%Y%m%d")
            # 這裡我們試著撞幾個常見的時間點
            for minute in ["1700", "1701", "1702", "1705"]:
                test_url = f"https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/{today_str}_{minute}_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"
                if requests.head(test_url).status_code == 200:
                    valid_links = [(today_str, test_url)]
                    break
            
            if not valid_links: return

    except Exception as e:
        print(f"執行出錯: {e}")
        return

    # 4. 更新 RSS 檔案
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    updated = False
    for date_str, target_url in valid_links[:5]:
        guid = f"ilub-{date_str}"
        if guid not in rss_content:
            print(f"發現新集數 {date_str}，網址已轉為 HTTPS: {target_url}")
            item_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=hk_tz)
            pub_date_str = item_date.strftime("%a, %d %b %Y 19:05:00 +0800")
            
            new_item = f"""    <item>
      <title>{item_date.strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
      <pubDate>{pub_date_str}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{target_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
            rss_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
            updated = True

    if updated:
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{PODCAST_NAME}] RSS 已成功更新並強制 Https！")

if __name__ == "__main__":
    check_and_update()
