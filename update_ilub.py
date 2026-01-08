import subprocess
import re
import os
from datetime import datetime, timedelta, timezone

# --- 配置資訊 ---
PODCAST_NAME = "聖艾粒LaLaLaLa"
SHOW_PAGE = "https://hkfm903.live/?show=%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa"
RSS_FILE = "ilub.xml"

# 聖艾粒常見的上架時間點 (按優先順序)
LIKELY_TIMES = ["1700", "1710", "1705", "1715", "1701"]

def get_html_via_curl(url):
    try:
        cmd = ['curl', '-s', '-L', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', url]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout
    except: return ""

def check_and_update():
    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)
    
    # 同時檢查 今天、昨天、前天 (補課機制)
    for i in range(3):
        target_date = now_hk - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        
        # 如果是假日（週六、週日）通常沒節目，可跳過
        if target_date.weekday() >= 5: continue 
        
        # 如果檢查的是「今天」，且還沒到晚上 7 點，就先跳過
        if i == 0 and int(now_hk.strftime("%H%M")) < 1900: continue

        print(f"[{PODCAST_NAME}] 正在檢查日期: {date_str}")
        
        if is_already_in_rss(date_str):
            print(f"{date_str} 已存在於 RSS，跳過。")
            continue

        # 1. 嘗試從網頁解析正確時間
        html_content = get_html_via_curl(SHOW_PAGE)
        pattern = rf'recordings/[^"\'\s>]*{date_str}_(\d{{4}})[^"\'\s>]*\.aac'
        matches = re.search(pattern, html_content)
        
        found_url = None
        if matches:
            actual_time = matches.group(1)
            found_url = f"https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/{date_str}_{actual_time}_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"
            print(f"✅ 從網頁成功解析出正確時間: {actual_time}")
        else:
            # 2. 網頁被擋 (403)，使用保險時間邏輯
            # 針對 1月7日 特別設定為 1710，其餘預設 1700
            insurance_time = "1710" if date_str == "20260107" else "1700"
            print(f"⚠️ 網頁解析失敗，使用保險時間 {insurance_time} 更新...")
            found_url = f"https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/{date_str}_{insurance_time}_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"

        if found_url:
            add_to_rss(found_url, date_str, target_date)

def is_already_in_rss(date_str):
    if not os.path.exists(RSS_FILE): return False
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        return f"ilub-{date_str}" in f.read()

def add_to_rss(url, date_str, date_obj):
    with open(RSS_FILE, "r", encoding="utf-8") as f: content = f.read()
    guid = f"ilub-{date_str}"
    pub_date = date_obj.strftime("%a, %d %b %Y 19:10:00 +0800")
    
    new_item = f"""    <item>
      <title>{date_obj.strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
    updated_content = content.replace("    <item>", new_item + "    <item>", 1)
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(updated_content)
    print(f"✅ {date_str} 已補入 RSS。")

if __name__ == "__main__":
    check_and_update()
