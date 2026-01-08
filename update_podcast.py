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
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 開始檢查網頁日期: {today_str}")

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        page_response = requests.get(SHOW_PAGE_URL, headers=headers, timeout=15)
        page_response.encoding = 'utf-8'
        html_content = page_response.text

        # 1. 抓取所有包含當天日期且以 .aac 結尾的連結 (不論 http 或相對路徑)
        # 修正：正則表達式改為更寬鬆的模式，專門抓取包含日期和 aac 的部分
        pattern = rf'/[^"\'\s>]*{today_str}_[^"\'\s>]*\.aac|http[^"\'\s>]*{today_str}_[^"\'\s>]*\.aac'
        raw_links = re.findall(pattern, html_content)
        
        valid_links = []
        for link in raw_links:
            # 確保為 https
            if link.startswith('http'):
                target_url = link.replace("http://", "https://")
            else:
                target_url = f"https://hkfm903.live{link}"
            valid_links.append(target_url)

        # 2. 如果網頁爬蟲失敗 (JavaScript 渲染問題)，進入「強力猜測模式」
        if not valid_links:
            print(f"[{PODCAST_NAME}] 網頁原始碼未見連結，進入強力猜測模式 (10:00 - 10:15)...")
            # 迴圈嘗試 00 到 15 分鐘
            for m in range(0, 16):
                minute_str = f"{m:02d}" # 格式化為 00, 01, 09...
                test_url = f"https://hkfm903.live/recordings/Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC/{today_str}_10{minute_str}_Bad_Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac"
                try:
                    # 使用 get 請求前 1KB 資料來確認檔案是否存在，比 head 更可靠
                    r = requests.get(test_url, headers=headers, timeout=5, stream=True)
                    if r.status_code == 200:
                        print(f"成功撞到正確網址: {test_url}")
                        valid_links = [test_url]
                        break
                except: continue

        if not valid_links:
            print(f"[{PODCAST_NAME}] 依然找不到今日檔案。")
            return

    except Exception as e:
        print(f"執行出錯: {e}")
        return

    # 3. 更新 RSS 檔案
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    target_url = valid_links[0]
    guid = f"bgog-{today_str}"
    
    if guid not in rss_content:
        print(f"正在寫入新集數: {today_str}")
        pub_date_str = now_hk.strftime("%a, %d %b %Y 12:05:00 +0800")
        
        new_item = f"""    <item>
      <title>{now_hk.strftime("%Y-%m-%d")} Bad Girl 大過佬</title>
      <pubDate>{pub_date_str}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{target_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
        rss_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{PODCAST_NAME}] 更新完成！")
    else:
        print(f"[{PODCAST_NAME}] 集數已存在。")

if __name__ == "__main__":
    check_and_update()
