import subprocess
import os
from datetime import datetime, timedelta, timezone

# --- 配置資訊 ---
PODCAST_NAME = "Bad Girl 大過佬"
RSS_FILE = "rss.xml"

def get_status_code(url):
    try:
        # 使用 curl 獲取狀態碼
        cmd = ['curl', '-s', '-o', '/dev/null', '-I', '-w', '%{http_code}', '--connect-timeout', '5', '-A', 'Mozilla/5.0', url]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout.strip()
    except: return "000"

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 開始偵測今日有效檔名 ({today_str})...")

    found_url = None
    # 掃描 10:00 到 10:25
    for m in range(0, 26):
        time_str = f"10{m:02d}"
        # 同時嘗試「底線」和「空格(%20)」格式，因為這也可能變
        for sep in ["_", "%20"]:
            test_url = f"https://hkfm903.live/recordings/Bad%20Girl%E5%A4%A7%E9%81%8E%E4%BD%AC/{today_str}_{time_str}_Bad{sep}Girl%E5%A4%A7%E9%81%8E%E4%BD%AC.aac"
            
            code = get_status_code(test_url)
            
            # 如果是 200 或 403，代表「抓到了！」
            if code in ["200", "206", "403"]:
                print(f"🎯 成功定位今日檔案網址: {test_url} (狀態碼: {code})")
                found_url = test_url
                break
        if found_url: break

    if found_url:
        update_rss(found_url, today_str, now_hk)
    else:
        print(f"❌ 在 10:00-10:25 區間內未發現任何 403/200 檔案，今日可能尚未上架。")

def update_rss(url, date_str, now_obj):
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
        print("集數已存在，不重複更新。")

if __name__ == "__main__":
    check_and_update()
