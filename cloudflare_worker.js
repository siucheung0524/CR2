/**
 * Cloudflare Worker: CR2 Podcast Proxy
 * 
 * 功能：
 * 1. 代理 GitHub Releases 音訊檔案下載
 * 2. 自動修改 Header 為正確的音訊 MIME (audio/x-m4a, audio/mpeg)
 * 3. 移除強制下載的 Content-Disposition: attachment 標頭，允許 Apple Podcasts 串流
 * 4. 完美透傳 HTTP Range 請求 (206 Partial Content)，支援拖曳進度條與續傳
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 根目錄健康檢查
    if (url.pathname === "/" || url.pathname === "") {
      return new Response("✅ CR2 Podcast Proxy is running normally!", {
        status: 200,
        headers: { "Content-Type": "text/plain; charset=utf-8" }
      });
    }

    // 構建 GitHub Release 目標網址
    // 支援 /releases/download/:tag/:filename (預設對應 siucheung0524/CR2)
    let targetPath = url.pathname;
    if (!targetPath.startsWith("/siucheung0524/CR2/")) {
      targetPath = "/siucheung0524/CR2" + targetPath;
    }
    const targetUrl = "https://github.com" + targetPath;

    // 轉發請求標頭（包含 Apple Podcasts 的 Range 請求）
    const forwardHeaders = new Headers();
    if (request.headers.has("Range")) {
      forwardHeaders.set("Range", request.headers.get("Range"));
    }
    forwardHeaders.set("User-Agent", request.headers.get("User-Agent") || "Mozilla/5.0 AppleCoreMedia");

    // 請求 GitHub Release（自動跟隨 302 重新導向至 Azure CDN）
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: forwardHeaders,
      redirect: "follow",
    });

    // 複製回應標頭並修正音訊 MIME 格式
    const responseHeaders = new Headers(response.headers);

    // 依副檔名設定正確的 MIME Type，避免被辨識為 application/octet-stream
    if (url.pathname.endsWith(".m4a")) {
      responseHeaders.set("Content-Type", "audio/x-m4a");
    } else if (url.pathname.endsWith(".mp3")) {
      responseHeaders.set("Content-Type", "audio/mpeg");
    } else if (url.pathname.endsWith(".aac")) {
      responseHeaders.set("Content-Type", "audio/aac");
    } else {
      responseHeaders.set("Content-Type", "audio/x-m4a");
    }

    // 關鍵：刪除強制下載 (attachment) 標頭，允許 Apple Podcasts / AVPlayer 內嵌串流
    responseHeaders.delete("Content-Disposition");
    responseHeaders.set("Accept-Ranges", "bytes");
    responseHeaders.set("Access-Control-Allow-Origin", "*");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  }
};
