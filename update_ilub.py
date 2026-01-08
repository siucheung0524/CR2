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
    today_str = now_hk.strftime("%Y%m%d")
    
    print(f"[{PODCAST_NAME}] 開始檢查日期: {today_str}")

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        page_response = requests.get(SHOW_PAGE_URL, headers=headers, timeout=15)
        page_response.encoding = 'utf-8'
        
        pattern = rf'/[^"\'\s>]*{today_str}_[^"\'\s>]*\.aac|http[^"\'\s>]*{today_str}_[^"\'\s>]*\.aac'
        raw_links = re.findall(pattern, page_response.text)
        
        valid_links = []
        for link in raw_links:
            target_url = link.replace("http://", "https://") if link.startswith('http') else f"https://hkfm903.live{link}"
            valid_links.append(target_url)

        # 猜測模式 (17:00-17:15)
        if not valid_links:
            print(f"[{PODCAST_NAME}] 進入猜測模式 (17:00-17:15)...")
            for m in range(0, 16):
                minute_str = f"{m:02d}"
                # 同時嘗試編碼格式
                test_url = f"https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/{today_str}_17{minute_str}_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"
                try:
                    if requests.head(test_url, timeout=5).status_code == 200:
                        print(f"成功找到網址: {test_url}")
                        valid_links = [test_url]
                        break
                except: continue
                if valid_links: break

        if not valid_links:
            # 這是正常的，如果現在還沒到晚上 7 點的話
            print(f"[{PODCAST_NAME}] 尚未找到今日檔案（通常晚上 7 點後上架）。")
            return

    except Exception as e:
        print(f"執行錯誤: {e}")
        return

    # 3. 更新 RSS
    if not os.path.exists(RSS_FILE): return
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    target_url = valid_links[0]
    guid = f"ilub-{today_str}"
    
    if guid not in rss_content:
        pub_date_str = now_hk.strftime("%a, %d %b %Y 19:05:00 +0800")
        new_item = f"""    <item>
      <title>{now_hk.strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
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

if __name__ == "__main__":
    check_and_update()
