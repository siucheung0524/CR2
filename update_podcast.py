import requests
from datetime import datetime, timedelta, timezone
import os
import urllib.parse

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
RSS_FILE = "rss.xml"

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 檢查日期: {today_str}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://hkfm903.live/'
    }

    found_url = None
    
    # 使用原始中文字串，交給 urllib.parse.quote 處理，防止雙重編碼
    show_path = "Bad Girl大過佬"
    
    print(f"[{PODCAST_NAME}] 開始暴力搜尋 10:00 - 10:20...")

    for m in range(0, 21):
        time_str = f"10{m:02d}"
        # 測試兩種可能的檔名格式：空格版與底線版
        for separator in [" ", "_"]:
            # 建立原始檔名
            raw_filename = f"{today_str}_{time_str}_Bad{separator}Girl大過佬.aac"
            
            # 正確編碼路徑組件
            encoded_show = urllib.parse.quote(show_path)
            encoded_file = urllib.parse.quote(raw_filename)
            
            test_url = f"https://hkfm903.live/recordings/{encoded_show}/{encoded_file}"
            
            # 這是偵錯關鍵：印出測試中的網址
            print(f"測試中 ({time_str}): {test_url}")
            
            try:
                r = requests.get(test_url, headers=headers, timeout=5, stream=True)
                if r.status_code == 200:
                    print(f"✅ 找到檔案！狀態碼 200")
                    found_url = test_url
                    break
            except Exception as e:
                print(f"❌ 連線出錯: {e}")
                continue
        if found_url: break

    if not found_url:
        print(f"[{PODCAST_NAME}] 依然找不到檔案。請對比上方印出的網址與瀏覽器網址。")
        return

    # 寫入 RSS 檔案邏輯
    if not os.path.exists(RSS_FILE):
        print(f"錯誤: 找不到 {RSS_FILE}")
        return
        
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
        # 在第一個 <item> 之前插入
        updated_content = rss_content.replace("    <item>", new_item + "    <item>", 1)
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(updated_content)
        print(f"[{PODCAST_NAME}] RSS 更新成功！")
    else:
        print(f"[{PODCAST_NAME}] 該日期集數已存在，跳過。")

if __name__ == "__main__":
    check_and_update()
