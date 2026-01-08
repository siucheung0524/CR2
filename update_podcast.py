import requests
from datetime import datetime, timedelta, timezone
import os
import urllib.parse
import time

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
RSS_FILE = "rss.xml"

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 檢查日期: {today_str}")

    # 1. 建立 Session 並設定超真實 Headers
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://hkfm903.live/'
    })

    try:
        # 2. 先訪問首頁獲取 Cookies (模擬真人進入網站)
        print("正在建立連線 Session...")
        session.get("https://hkfm903.live/", timeout=10)
        time.sleep(2) # 稍微停一下，不要太像機器人
    except:
        pass

    found_url = None
    show_path = "Bad Girl大過佬"
    
    print(f"[{PODCAST_NAME}] 開始搜尋 10:00 - 10:25...")

    for m in range(0, 26):
        time_str = f"10{m:02d}"
        for separator in [" ", "_"]:
            raw_filename = f"{today_str}_{time_str}_Bad{separator}Girl大過佬.aac"
            encoded_show = urllib.parse.quote(show_path)
            encoded_file = urllib.parse.quote(raw_filename)
            
            # 使用 HTTPS 測試
            test_url = f"https://hkfm903.live/recordings/{encoded_show}/{encoded_file}"
            
            try:
                # 3. 使用 GET 測試並觀察狀態
                r = session.get(test_url, timeout=10, stream=True)
                
                if time_str == "1009":
                    print(f"DEBUG (1009): {test_url} -> 狀態碼: {r.status_code}")

                if r.status_code == 200:
                    print(f"✅ 成功突破 403！找到檔案。")
                    found_url = test_url
                    break
                elif r.status_code == 403:
                    # 如果還是 403，嘗試換一種方式（不帶 Referer）
                    r = session.get(test_url, timeout=10, stream=True, headers={'User-Agent': 'Mozilla/5.0'})
                    if r.status_code == 200:
                        found_url = test_url
                        break
            except:
                continue
        if found_url: break

    if not found_url:
        print(f"[{PODCAST_NAME}] 依然被伺服器阻擋 (403) 或檔案尚未上架。")
        return

    # 4. 更新 RSS 檔案
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
      <enclosure url="{found_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
        updated_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(updated_content)
        print(f"[{PODCAST_NAME}] 更新成功！")

if __name__ == "__main__":
    check_and_update()
