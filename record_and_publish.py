#!/usr/bin/env python3
import os
import sys
import re
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import ssl
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

def load_env():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO", "siucheung0524/CR2")
    worker_url = os.environ.get("CLOUDFLARE_WORKER_URL", "")
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                elif line.startswith("GITHUB_REPO="):
                    repo = line.split("=", 1)[1].strip()
                elif line.startswith("CLOUDFLARE_WORKER_URL="):
                    worker_url = line.split("=", 1)[1].strip()
    return token, repo, worker_url

GITHUB_TOKEN, GITHUB_REPO, CLOUDFLARE_WORKER_URL = load_env()

# 配置：
# Bad Girl 大過佬：10:07 開始錄，錄到 12:02 (1小時55分 + 2分鐘緩衝 = 116分鐘 / 6960秒)
# 聖艾粒LaLaLaLa：17:07 開始錄，錄到 19:02 (1小時55分 + 2分鐘緩衝 = 116分鐘 / 6960秒)
SHOWS = {
    "bgog": {
        "name": "Bad Girl 大過佬",
        "channel": "903",
        "rss_file": "rss.xml",
        "guid_prefix": "bgog-",
        "default_duration": 6960, # 116 分鐘 (10:07 -> 12:03)
        "time_str": "12:05:00",
        "itunes_duration": "01:56:00"
    },
    "ilub": {
        "name": "聖艾粒LaLaLaLa",
        "channel": "903",
        "rss_file": "ilub.xml",
        "guid_prefix": "ilub-",
        "default_duration": 6960, # 116 分鐘 (17:07 -> 19:03)
        "time_str": "19:05:00",
        "itunes_duration": "01:56:00"
    }
}

def get_stream_url(channel="903"):
    """使用 Playwright 載入商台直播頁面以獲取本地 IP 簽名之 M3U8 串流與 CloudFront Cookies"""
    print(f"[{datetime.now()}] 正在獲取 881903 (頻道 {channel}) 的串流網址與憑證...")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        playlist_url = None
        fallback_m3u8_url = None

        def handle_response(response):
            nonlocal playlist_url, fallback_m3u8_url
            url = response.url
            if "playlist.m3u8" in url and response.ok:
                playlist_url = url
            elif not fallback_m3u8_url and ".m3u8" in url and response.ok:
                fallback_m3u8_url = url

        page.on("response", handle_response)
        page_url = f"https://www.881903.com/live/{channel}"
        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

        for _ in range(40):
            if playlist_url:
                break
            page.wait_for_timeout(500)

        cookies = context.cookies()
        browser.close()

        m3u8_url = playlist_url or fallback_m3u8_url
        if not m3u8_url:
            raise RuntimeError("無法從商台頁面擷取到 M3U8 串流網址！")

        cookie_parts = [f"{c['name']}={c['value']}" for c in cookies if "CloudFront" in c.get("name", "")]
        cookie_str = "; ".join(cookie_parts)

        headers_str = f"Cookie: {cookie_str}\r\nReferer: https://www.881903.com/\r\nUser-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"

        print(f"[{datetime.now()}] 成功取得串流網址: {m3u8_url} (取得 CloudFront Cookies: {len(cookie_parts)} 個)")
        return m3u8_url, headers_str

def record_stream(m3u8_url, duration, output_file, headers_str=None):
    """呼叫 FFmpeg 錄製純音訊流"""
    print(f"[{datetime.now()}] 開始錄音，目標檔案: {output_file}，時長: {duration} 秒 ({duration/60:.1f} 分鐘)...")
    ffmpeg_bin = "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg_bin):
        ffmpeg_bin = "ffmpeg"

    cmd = [
        ffmpeg_bin,
        "-y"
    ]
    if headers_str:
        cmd.extend(["-headers", headers_str])

    cmd.extend([
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", m3u8_url,
        "-vn",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-t", str(duration),
        output_file
    ])

    subprocess.run(cmd, check=True)
    size_bytes = os.path.getsize(output_file)
    print(f"[{datetime.now()}] 錄音完成！檔案大小: {size_bytes / (1024*1024):.2f} MB")
    return size_bytes

def github_api_request(url, method="GET", data=None, headers=None):
    """呼叫 GitHub API"""
    if headers is None:
        headers = {}
    headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    headers["Accept"] = "application/vnd.github.v3+json"
    headers["User-Agent"] = "CR2-Recorder"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
        content = resp.read()
        if resp.headers.get_content_type() == "application/json":
            return json.loads(content.decode("utf-8"))
        return content

def create_release_and_upload(tag, title, file_path, filename):
    """在 GitHub 建立 Release 並上傳音檔資產"""
    print(f"[{datetime.now()}] 正在建立 GitHub Release ({tag})...")
    create_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    payload = json.dumps({
        "tag_name": tag,
        "name": title,
        "body": f"商台錄音存檔 - {title}\n自動錄音產生",
        "draft": False,
        "prerelease": False
    }).encode("utf-8")

    try:
        release_info = github_api_request(create_url, method="POST", data=payload, headers={"Content-Type": "application/json"})
    except urllib.error.HTTPError as e:
        if e.code == 422:
            print(f"[{datetime.now()}] Release {tag} 已存在或衝突 (422)，嘗試讀取現有 Release...")
            get_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
            release_info = github_api_request(get_url)
        else:
            raise

    # 檢查是否已存在同名 asset，若有則先刪除，避免上傳失敗
    for asset in release_info.get("assets", []):
        if asset.get("name") == filename:
            print(f"[{datetime.now()}] 發現已存在同名資產 {filename} (ID: {asset['id']})，正在刪除舊資產...")
            del_asset_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/assets/{asset['id']}"
            github_api_request(del_asset_url, method="DELETE")

    upload_url_template = release_info.get("upload_url", "")
    upload_url = upload_url_template.split("{")[0] + f"?name={filename}"

    print(f"[{datetime.now()}] 正在上傳音檔到 GitHub Release 附件...")
    with open(file_path, "rb") as f:
        file_data = f.read()

    mime_type = "audio/x-m4a" if filename.endswith(".m4a") else ("audio/mpeg" if filename.endswith(".mp3") else "audio/aac")
    upload_headers = {"Content-Type": mime_type}
    asset_info = github_api_request(upload_url, method="POST", data=file_data, headers=upload_headers)
    download_url = asset_info.get("browser_download_url")
    print(f"[{datetime.now()}] 音檔上傳成功！下載連結: {download_url}")
    return download_url

def update_rss(rss_file_path, show_name, date_str, date_obj, download_url, file_size, show_cfg):
    """將新一集加入 RSS XML 最前端，若已存在則替換該集連結"""
    print(f"[{datetime.now()}] 正在更新 RSS 檔案: {rss_file_path}...")
    with open(rss_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    guid = f"{show_cfg['guid_prefix']}{date_str}"
    pub_date = date_obj.strftime(f"%a, %d %b %Y {show_cfg['time_str']} +0800")
    formatted_date = date_obj.strftime("%Y-%m-%d")
    enclosure_mime = "audio/x-m4a" if download_url.endswith(".m4a") else ("audio/mpeg" if download_url.endswith(".mp3") else "audio/aac")

    new_item = f"""    <item>
      <title>{formatted_date} {show_name}</title>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{download_url}" length="{file_size}" type="{enclosure_mime}" />
      <itunes:duration>{show_cfg['itunes_duration']}</itunes:duration>
    </item>
"""
    if guid in content:
        print(f"[{datetime.now()}] {guid} 已經在 RSS 中，替換為最新 Release 下載連結...")
        pattern = rf'\s*<item>\s*<title>[^<]*</title>[\s\S]*?<guid[^>]*>{re.escape(guid)}</guid>[\s\S]*?</item>'
        content = re.sub(pattern, "\n" + new_item.rstrip(), content, count=1)
    else:
        if "    <item>" in content:
            content = content.replace("    <item>", new_item + "    <item>", 1)
        else:
            content = content.replace("  </channel>", new_item + "  </channel>")

    with open(rss_file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[{datetime.now()}] RSS 更新完成！")

def prune_old_releases(show_cfg, days_to_keep=14):
    """自動清理 14 天前的 Releases 與 RSS 項目"""
    print(f"[{datetime.now()}] 正在檢查並清理超過 {days_to_keep} 天的舊集數...")
    prefix = show_cfg["guid_prefix"]
    rss_file_path = os.path.join(SCRIPT_DIR, show_cfg["rss_file"])

    hk_tz = timezone(timedelta(hours=8))
    cutoff_time = datetime.now(hk_tz) - timedelta(days=days_to_keep)

    releases_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100"
    try:
        releases = github_api_request(releases_url)
    except Exception as e:
        print(f"獲取 Releases 失敗: {e}")
        return

    with open(rss_file_path, "r", encoding="utf-8") as f:
        rss_content = f.read()

    deleted_any = False
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith(prefix):
            continue

        # 判定過期依據：集數標籤日期或 GitHub 建立時間
        is_old = False
        date_match = re.search(r'(\d{8})', tag)
        if date_match:
            try:
                tag_date = datetime.strptime(date_match.group(1), "%Y%m%d").replace(tzinfo=hk_tz)
                if tag_date < cutoff_time:
                    is_old = True
            except ValueError:
                pass

        created_at_str = rel.get("created_at", "")
        if not is_old and created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at < cutoff_time:
                    is_old = True
            except Exception:
                pass

        if is_old:
            print(f"[{datetime.now()}] 發現過期 Release: {tag}，執行刪除...")
            del_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/{rel['id']}"
            try:
                github_api_request(del_url, method="DELETE")
            except Exception as err:
                print(f"刪除 Release {tag} 失敗: {err}")

            del_tag_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/tags/{tag}"
            try:
                github_api_request(del_tag_url, method="DELETE")
            except Exception:
                pass

            pattern = rf'\s*<item>\s*<title>[^<]*</title>[\s\S]*?<guid[^>]*>{re.escape(tag)}</guid>[\s\S]*?</item>'
            rss_content = re.sub(pattern, '', rss_content)
            deleted_any = True
            print(f"[{datetime.now()}] 成功清除過期 Release 與 RSS 項目: {tag}")

    # 同步檢查並清理本地 recordings 資料夾中超過 14 天的舊檔
    recordings_dir = os.path.join(SCRIPT_DIR, "recordings")
    if os.path.exists(recordings_dir):
        for fname in os.listdir(recordings_dir):
            if fname.startswith(prefix):
                m = re.search(r'(\d{8})', fname)
                if m:
                    try:
                        fdate = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=hk_tz)
                        if fdate < cutoff_time:
                            fpath = os.path.join(recordings_dir, fname)
                            os.remove(fpath)
                            print(f"[{datetime.now()}] 成功清除本地過期暫存音檔: {fname}")
                    except Exception:
                        pass

    if deleted_any:
        with open(rss_file_path, "w", encoding="utf-8") as f:
            f.write(rss_content)
        print(f"[{datetime.now()}] 過期集數清理完畢並已同步至 RSS 檔案。")
    else:
        print(f"[{datetime.now()}] 沒有發現超過 {days_to_keep} 天的過期項目。")

def git_commit_and_push(rss_file, show_name, date_str):
    """將更新的 RSS 檔案 commit 並 push 至 GitHub"""
    print(f"[{datetime.now()}] 正在檢查並推送到 GitHub main 分支...")
    try:
        status = subprocess.run(["git", "-C", SCRIPT_DIR, "status", "--porcelain", rss_file], capture_output=True, text=True, check=True)
        if not status.stdout.strip():
            print(f"[{datetime.now()}] RSS 檔案沒有任何變更，略過 Git Commit 與 Push。")
            return

        subprocess.run(["git", "-C", SCRIPT_DIR, "add", rss_file], check=True)
        commit_msg = f"Update {show_name} ({date_str}) & prune old episodes"
        subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "-C", SCRIPT_DIR, "push", "origin", "main"], check=True)
        print(f"[{datetime.now()}] 成功推送到 GitHub！Podcast 訂閱源已即時更新。")
    except subprocess.CalledProcessError as e:
        print(f"Git 操作發生錯誤: {e}")

def main():
    parser = argparse.ArgumentParser(description="CR2 商台廣播自動錄製與 Podcast 同步工具")
    parser.add_argument("--show", choices=["bgog", "ilub"], default="bgog", help="選擇節目 (預設: bgog Bad Girl大過佬)")
    parser.add_argument("--duration", type=int, default=None, help="錄音秒數 (預設: 116 分鐘)")
    parser.add_argument("--stream-url", type=str, default=None, help="自訂 M3U8 串流 (不指定則自動從網頁獲取)")
    parser.add_argument("--input-file", type=str, default=None, help="指定現有音檔路徑 (略過即時錄音，直接發布)")
    parser.add_argument("--date", type=str, default=None, help="指定集數日期 (格式: YYYYMMDD，預設從檔名或當日推算)")
    parser.add_argument("--clean-local", action="store_true", help="發布後立即刪除本地暫存音檔 (預設會保留並於 14 天後自動清理)")
    parser.add_argument("--skip-prune", action="store_true", help="跳過清理 14 天前舊檔")
    parser.add_argument("--dry-run", action="store_true", help="測試模式：不推送到 GitHub")

    args = parser.parse_args()
    show_cfg = SHOWS[args.show]

    hk_tz = timezone(timedelta(hours=8))
    now_hk = datetime.now(hk_tz)

    temp_dir = os.path.join(SCRIPT_DIR, "recordings")
    os.makedirs(temp_dir, exist_ok=True)

    if args.input_file:
        if not os.path.exists(args.input_file):
            raise FileNotFoundError(f"找不到指定的輸入音檔: {args.input_file}")

        if args.date:
            date_str = args.date
        else:
            m = re.search(r'(20\d{6})', os.path.basename(args.input_file))
            date_str = m.group(1) if m else now_hk.strftime("%Y%m%d")

        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=hk_tz)
        except ValueError:
            date_obj = now_hk

        print("=" * 60)
        print(f"開始執行現有音檔發布排程: {show_cfg['name']}")
        print(f"指定集數日期: {date_obj.strftime('%Y-%m-%d')} ({date_str})")
        print(f"來源檔案: {args.input_file}")
        print("=" * 60)

        asset_filename = f"{show_cfg['guid_prefix']}{date_str}.m4a"
        output_path = os.path.join(temp_dir, asset_filename)

        if os.path.abspath(args.input_file) == os.path.abspath(output_path):
            file_size = os.path.getsize(output_path)
        else:
            print(f"[{datetime.now()}] 正在將音檔轉換/封裝為 M4A (FastStart) 格式: {output_path}...")
            ffmpeg_bin = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", args.input_file,
                "-vn",
                "-c:a", "copy",
                "-movflags", "+faststart",
                output_path
            ]
            subprocess.run(cmd, check=True)
            file_size = os.path.getsize(output_path)
            print(f"[{datetime.now()}] 格式處理完成！檔案大小: {file_size / (1024*1024):.2f} MB")
    else:
        date_str = now_hk.strftime("%Y%m%d")
        date_obj = now_hk
        duration = args.duration or show_cfg["default_duration"]

        print("=" * 60)
        print(f"開始執行節目錄製排程: {show_cfg['name']}")
        print(f"執行時間: {now_hk.strftime('%Y-%m-%d %H:%M:%S')} (HKT)")
        print(f"錄音時長: {duration} 秒 ({duration/60:.1f} 分鐘)")
        print("=" * 60)

        # 1. 取得串流網址
        if args.stream_url:
            stream_url = args.stream_url
            headers_str = None
        else:
            stream_url, headers_str = get_stream_url(show_cfg["channel"])

        # 2. 錄製音檔至暫存
        asset_filename = f"{show_cfg['guid_prefix']}{date_str}.m4a"
        output_path = os.path.join(temp_dir, asset_filename)

        file_size = record_stream(stream_url, duration, output_path, headers_str=headers_str)

    if args.dry_run:
        print(f"[DRY-RUN] 測試模式完成，產出音檔: {output_path}")
        return

    # 3. 建立 Release 並上傳
    tag = f"{show_cfg['guid_prefix']}{date_str}"
    release_title = f"{date_obj.strftime('%Y-%m-%d')} {show_cfg['name']}"
    raw_download_url = create_release_and_upload(tag, release_title, output_path, asset_filename)

    if CLOUDFLARE_WORKER_URL:
        proxy_base = CLOUDFLARE_WORKER_URL.rstrip('/')
        download_url = f"{proxy_base}/releases/download/{tag}/{asset_filename}"
        print(f"[{datetime.now()}] 套用 Cloudflare Worker Proxy 連結: {download_url}")
    else:
        download_url = raw_download_url

    # 4. 更新 RSS XML
    rss_path = os.path.join(SCRIPT_DIR, show_cfg["rss_file"])
    update_rss(rss_path, show_cfg["name"], date_str, date_obj, download_url, file_size, show_cfg)

    # 5. 清理 14 天前的 Releases
    if not args.skip_prune:
        prune_old_releases(show_cfg, days_to_keep=14)

    # 6. Push 至 GitHub
    git_commit_and_push(show_cfg["rss_file"], show_cfg["name"], date_str)

    # 7. 本地暫存檔案保留（預設保留以防上傳失敗或需要回溯，14天後由 prune_old_releases 自動清理）
    if args.clean_local:
        if not (args.input_file and os.path.abspath(args.input_file) == os.path.abspath(output_path)):
            if os.path.exists(output_path):
                os.remove(output_path)
                print(f"[{datetime.now()}] 本地暫存音檔已依要求清理完畢。")
    else:
        print(f"[{datetime.now()}] 本地音檔已安全保留於: {output_path} (將在 14 天後自動清理)")

    print(f"[{datetime.now()}] 全部流程圓滿完成！")

if __name__ == "__main__":
    main()
