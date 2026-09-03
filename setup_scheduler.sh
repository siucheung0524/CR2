#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(which python3)"
fi

mkdir -p ~/Library/LaunchAgents

generate_plists() {
    # 1. 生成 bgog.plist (Bad Girl 大過佬: 逢星期一至五 10:07 啟動，避開前段新聞廣告)
    cat << PLIST > "$DIR/com.siucheung0524.cr2.bgog.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.siucheung0524.cr2.bgog</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$DIR/record_and_publish.py</string>
        <string>--show</string>
        <string>bgog</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>7</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>$DIR/bgog.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/bgog_error.log</string>
</dict>
</plist>
PLIST

    # 2. 生成 ilub.plist (聖艾粒LaLaLaLa: 逢星期一至五 17:07 啟動，避開前段新聞廣告)
    cat << PLIST > "$DIR/com.siucheung0524.cr2.ilub.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.siucheung0524.cr2.ilub</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$DIR/record_and_publish.py</string>
        <string>--show</string>
        <string>ilub</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>7</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>7</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>$DIR/ilub.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/ilub_error.log</string>
</dict>
</plist>
PLIST
}

case "$1" in
    start|load)
        echo "正在依據目前目錄 [$DIR] 產生排程設定..."
        generate_plists
        cp "$DIR/com.siucheung0524.cr2.bgog.plist" ~/Library/LaunchAgents/
        cp "$DIR/com.siucheung0524.cr2.ilub.plist" ~/Library/LaunchAgents/
        launchctl unload ~/Library/LaunchAgents/com.siucheung0524.cr2.bgog.plist 2>/dev/null
        launchctl unload ~/Library/LaunchAgents/com.siucheung0524.cr2.ilub.plist 2>/dev/null
        launchctl load ~/Library/LaunchAgents/com.siucheung0524.cr2.bgog.plist
        launchctl load ~/Library/LaunchAgents/com.siucheung0524.cr2.ilub.plist
        echo "✅ 排程服務已成功註冊與啟動！"
        echo " - 目錄位置: $DIR"
        echo " - Bad Girl 大過佬: 逢星期一至五 10:07 開始錄音 (錄製至 12:03)"
        echo " - 聖艾粒LaLaLaLa: 逢星期一至五 17:07 開始錄音 (錄製至 19:03)"
        ;;
    stop|unload)
        echo "正在停止排程服務..."
        launchctl unload ~/Library/LaunchAgents/com.siucheung0524.cr2.bgog.plist 2>/dev/null
        launchctl unload ~/Library/LaunchAgents/com.siucheung0524.cr2.ilub.plist 2>/dev/null
        rm -f ~/Library/LaunchAgents/com.siucheung0524.cr2.bgog.plist
        rm -f ~/Library/LaunchAgents/com.siucheung0524.cr2.ilub.plist
        echo "🛑 排程服務已停止並移除。"
        ;;
    status)
        echo "檢查排程服務狀態："
        launchctl list | grep "com.siucheung0524.cr2" || echo "排程尚未載入。"
        ;;
    log)
        tail -n 50 -f "$DIR/bgog.log" "$DIR/bgog_error.log"
        ;;
    *)
        echo "用法: $0 {start|stop|status|log}"
        ;;
esac
