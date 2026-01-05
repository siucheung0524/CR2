import requests
from datetime import datetime
import os

# 配置資訊 (針對聖艾粒)
PODCAST_NAME = "聖艾粒LaLaLaLa"
# 這裡使用了 URL 編碼，確保中文字元正確
BASE_URL = "https://hkfm903.live/recordings/%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa/"
RSS_FILE = "ilub.xml" # 另存為 ilub.xml

def check_and_update():
    today_str = datetime.now().strftime("%Y%m%d")
    # 檔案命名通常是 1700 開頭
    file_name = f"{today_str}_1700_%E8%81%96%E8%89%BE%E7%B2%92LaLaLaLa.aac"
    target_url = BASE_URL + file_name
    
    response = requests.head(target_url)
    if response.status_code != 200:
        print(f"《聖艾粒》今日 ({today_str}) 尚未上架。")
        return

    if not os.path.exists(RSS_FILE):
        print(f"找不到 {RSS_FILE}。")
        return
        
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_content = f.read()

    guid = f"ilub-{today_str}"
    if guid in rss_content:
        print(f"今日節目 ({today_str}) 已經存在。")
        return

    new_item = f"""    <item>
      <title>{datetime.now().strftime("%Y-%m-%d")} 聖艾粒LaLaLaLa</title>
      <pubDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{target_url}" length="0" type="audio/aac" />
      <itunes:duration>02:00:00</itunes:duration>
    </item>
"""
    updated_content = rss_content.replace("    <item>", new_item + "    <item>", 1)

    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(updated_content)
    print(f"成功更新 ilub.xml！")

if __name__ == "__main__":
    check_and_update()
