import requests
from datetime import datetime, timedelta, timezone
import os

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
RSS_FILE = "rss.xml"
# ----------------

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 檢查日期: {today_str}")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://hkfm903.live/'
    })

    found_url = None
    
    # 這裡我們用最原始的字串，避免 Python 自動編碼出錯
    # 資料夾名：Bad%20Girl大過佬
    base_folder = "https://hkfm903.live/recordings/Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC"
    
    print(f"[{PODCAST_NAME}] 開始暴力搜尋 10:00 - 10:20...")

    for m in range(0, 21):
        time_str = f"10{m:02d}"
        
        # 同時嘗試「底線版」和「空格編碼版」的檔名
        filenames = [
            f"{today_str}_{time_str}_Bad_Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac",
            f"{today_str}_{time_str}_Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac"
        ]
        
        for fname in filenames:
            test_url = f"{base_folder}/{fname}"
            try:
                # 使用 stream=True 快速檢查 headers
                r = session.get(test_url, timeout=5, stream=True)
                if r.status_code == 200:
                    print(f"--- [找到檔案!] 狀態 200: {test_url} ---")
                    found_url = test_url
                    break
            except:
                continue
        if found_url: break

    if not found_url:
        print(f"[{PODCAST_NAME}] 依然找不到檔案。請檢查 Action 日誌中上面測試的網址與你手動複製的是否一致。")
        return

    # 更新 RSS
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    guid = f"bgog-{today_str}"
    if guid not in rss_content:
        pub_date_str = now_hk.strftime("%a, %d %b %Y 12:05:00 +0800")
        new_item = f"""    <item>
      <title>{now_hk.strftime("%Y-%m-%d")} Bad Girl 大過佬</title>
      <pub_date>{pub_date_str}</pub_date>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{found_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
        rss_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{PODCAST_NAME}] RSS 更新成功！")
    else:
        print(f"[{PODCAST_NAME}] 該日期集數已存在。")

if __name__ == "__main__":
    check_and_update()
