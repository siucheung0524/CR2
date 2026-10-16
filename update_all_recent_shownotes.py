#!/usr/bin/env python3
import json
import os
import re
import subprocess
from generate_human_shownotes import generate_editorial_shownotes

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EPISODES = [
    {
        "guid": "bgog-20261016",
        "show_name": "Bad Girl 大過佬",
        "date_str": "2026-10-16",
        "rss_file": "rss.xml",
        "transcript": "recordings/bgog-20261016.raw_transcript.json",
        "keeps": [(88.9, 1333.9), (1643.4, 3154.6), (3733.8, 3922.0), (4031.4, 4927.4), (5390.9, 6754.2)]
    },
    {
        "guid": "bgog-20261015",
        "show_name": "Bad Girl 大過佬",
        "date_str": "2026-10-15",
        "rss_file": "rss.xml",
        "transcript": "recordings/bgog-20261015.raw_transcript.json",
        "keeps": [(129.2, 1399.8), (1736.5, 3151.2), (3712.3, 4711.3), (4764.1, 5005.0), (5555.8, 6791.1)]
    },
    {
        "guid": "ilub-20261016",
        "show_name": "聖艾粒LaLaLaLa",
        "date_str": "2026-10-16",
        "rss_file": "ilub.xml",
        "transcript": "recordings/ilub-20261016.raw_transcript.json",
        "keeps": [(304.9, 1256.0), (1900.5, 3080.0), (3780.0, 4750.0), (4800.0, 5800.0), (5900.0, 6650.0)]
    },
    {
        "guid": "ilub-20261015",
        "show_name": "聖艾粒LaLaLaLa",
        "date_str": "2026-10-15",
        "rss_file": "ilub.xml",
        "transcript": "recordings/ilub-20261015.raw_transcript.json",
        "keeps": [(352.2, 1316.2), (2005.6, 3102.4), (3918.4, 4921.9), (5216.1, 5275.9), (5452.4, 6350.2), (6380.2, 6685.3)]
    }
]

def update_episode_description_in_rss(rss_file_path, guid, new_description):
    with open(rss_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 正則嚴格匹配單一 item 區塊（絕不跨越其他 item）
    pattern = rf'<item>(?:(?!<item>|</item>)[\s\S])*?<guid[^>]*>{re.escape(guid)}</guid>(?:(?!</item>)[\s\S])*?</item>'
    m = re.search(pattern, content)
    if not m:
        print(f"  ❌ 在 {rss_file_path} 中找不到 {guid}")
        return False

    old_item = m.group(0)
    # 如果已經有 description，替換它；如果沒有，插入在 title 之後
    desc_str = f"\n      <description><![CDATA[{new_description}]]></description>"
    if "<description>" in old_item:
        new_item = re.sub(r'\s*<description>[\s\S]*?</description>', desc_str, old_item, count=1)
    else:
        new_item = re.sub(r'(<title>[^<]+</title>)', r'\1' + desc_str, old_item, count=1)

    content = content.replace(old_item, new_item, 1)
    with open(rss_file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✅ 成功更新 {rss_file_path} 中 {guid} 的官方節目簡介！")
    return True

def main():
    print("🚀 開始為近期集數批量生成並更新高質感官方人寫節目簡介...")
    for ep in EPISODES:
        guid = ep["guid"]
        rss_path = os.path.join(SCRIPT_DIR, ep["rss_file"])
        trans_path = os.path.join(SCRIPT_DIR, ep["transcript"])
        print(f"\n🎙️ 正在處理: {ep['show_name']} ({ep['date_str']}) - {guid}")
        shownotes, titles = generate_editorial_shownotes(trans_path, ep["keeps"], ep["show_name"], ep["date_str"])
        if shownotes:
            print("📝 生成簡介預覽:\n" + shownotes[:300] + "\n...")
            update_episode_description_in_rss(rss_path, guid, shownotes)
        else:
            print(f"  ⚠️ 無法生成 {guid} 簡介")

    print("\n📦 全部更新完畢！")

if __name__ == "__main__":
    main()
