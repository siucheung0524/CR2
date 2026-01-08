import requests
import re
from datetime import datetime, timedelta, timezone
import os

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
# 手寫編碼過的節目名稱
URL_SHOW_NAME = "Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC"
RSS_FILE = "rss.xml"
# ----------------

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 開始檢查日期: {today_str}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://hkfm903.live/'
    }

    valid_link = None

    # 強力嘗試 10:00 - 10:20 (針對 10:09 優化)
    print(f"[{PODCAST_NAME}] 正在嘗試精確匹配 10:00 - 10:20...")
    
    for m in range(0, 21):
        min_str = f"{m:02d}"
        # 直接拼接你提供成功的網址格式：日期_1009_Bad_Girl大過佬.aac
        # 注意：資料夾裡有 %20，但檔案名裡通常是底線 _
        test_url = f"https://hkfm903.live/recordings/{URL_SHOW_NAME}/{today_str}_10{min_str}_Bad_Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac"
        
        try:
            # 使用 stream=True 快速測試連結是否存在
            r = requests.get(test_url, headers=headers, timeout=5, stream=True)
            if r.status_code == 200:
                print(f"--- [找到檔案!] 網址正確: {test_url} ---")
                valid_link = test_url
                break
        except:
            continue

    if not valid_link:
        print(f"[{PODCAST_NAME}] 依然找不到今日檔案。")
        return

    # 寫入 RSS 檔案
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    guid = f"bgog-{today_str}"
    if guid not in rss_content:
        pub_date_str = now_hk.strftime("%a, %d %b %Y 12:05:00 +0800")
        new_item = f"""    <item>
      <title>{now_hk.strftime("%Y-%m-%d")} Bad Girl 大過佬</title>
      <pubDate>{pub_date_str}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{valid_link}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
        rss_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{PODCAST_NAME}] RSS 更新成功！")
    else:
        print(f"[{PODCAST_NAME}] 集數已存在。")

if __name__ == "__main__":
    check_and_update()
