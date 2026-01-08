import subprocess
import re
import os
from datetime import datetime, timedelta, timezone

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
SHOW_PAGE = "https://hkfm903.live/?show=Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC"
RSS_FILE = "rss.xml"

def get_html_via_curl(url):
    """使用 curl 抓取網頁原始碼"""
    try:
        cmd = [
            'curl', '-s', '-L', 
            '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout
    except:
        return ""

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 正在解析網頁尋找今日 ({today_str}) 檔案...")

    # 1. 抓取網頁內容
    html_content = get_html_via_curl(SHOW_PAGE)
    
    # 2. 尋找包含今天日期的 .aac 連結
    # 搜尋格式如：recordings/.../20260108_1009_...aac
    pattern = rf'recordings/[^"\'\s>]*{today_str}_[^"\'\s>]*\.aac'
    matches = re.findall(pattern, html_content)
    
    found_url = None
    if matches:
        # 取得最後一個（通常是最新的）
        found_path = matches[-1].lstrip('/')
        found_url = f"https://hkfm903.live/{found_path}"
        print(f"✅ 從網頁成功解析出網址: {found_url}")
    else:
        # 如果網頁被擋或沒內容，使用你確認過的 10:09 作為保險
        print(f"⚠️ 網頁沒看到連結，使用保險時間 10:09 更新...")
        found_url = f"https://hkfm903.live/recordings/Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC/{today_str}_1009_Bad_Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac"

    # 3. 更新 RSS
    update_rss_file(found_url, today_str, now_hk)

def update_rss_file(url, date_str, now_obj):
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f: content = f.read()
    
    guid = f"bgog-{date_str}"
    if guid not in content:
        pub_date = now_obj.strftime("%a, %d %b %Y 12:05:00 +0800")
        new_item = f"""    <item>
      <title>{now_obj.strftime("%Y-%m-%d")} Bad Girl 大過佬</title>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
        with open(RSS_FILE, "w", encoding="utf-8") as f:
            f.write(content.replace("    <item>", new_item + "    <item>", 1))
        print(f"✅ RSS 已更新！")
    else:
        print("集數已存在，跳過。")

if __name__ == "__main__":
    check_and_update()
