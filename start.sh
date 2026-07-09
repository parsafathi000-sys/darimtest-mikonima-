#!/bin/bash
set -e

NGINX_PORT=${PORT:-3000}
DB_PATH="/etc/x-ui/x-ui.db"

cd /usr/local/x-ui

echo "🔧 Configuring panel..."
./x-ui setting -port 2053 -webBasePath /panel/ || true

echo "🔧 Building nginx config..."
envsubst '${NGINX_PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "▶️ Starting x-ui..."
./x-ui &
XUI_PID=$!
sleep 4

echo "▶️ Starting XHTTP relay..."
cd /relay
nohup python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
RELAY_PID=$!
cd /usr/local/x-ui
sleep 3

cleanup_socat() {
    for pid in $(pgrep -f "socat.*TCP-LISTEN:8080" 2>/dev/null); do
        kill "$pid" 2>/dev/null || true
    done
    sleep 1
}

get_xray_port() {
    sqlite3 "$DB_PATH" "SELECT port FROM (SELECT port, COUNT(*) as cnt FROM inbounds WHERE enable = 1 AND port NOT IN (2053, 2096) AND protocol IN ('vless', 'vmess', 'trojan', 'shadowsocks') GROUP BY port ORDER BY cnt DESC) LIMIT 1;" 2>/dev/null || echo ""
}

setup_bridge() {
    if [ ! -f "$DB_PATH" ]; then return; fi
    local target_port
    target_port=$(get_xray_port)
    if [ -n "$target_port" ] && [ "$target_port" != "8080" ]; then
        cleanup_socat
        echo "🔄 Bridging 8080 -> $target_port"
        socat TCP-LISTEN:8080,reuseaddr,fork TCP:127.0.0.1:"$target_port" &
    fi
}

setup_bridge

watchdog() {
    while true; do
        sleep 15
        if ! kill -0 "$XUI_PID" 2>/dev/null; then
            echo "Restarting x-ui..."
            cd /usr/local/x-ui && ./x-ui &
            XUI_PID=$!
            sleep 4
            setup_bridge
        fi
        if ! kill -0 "$RELAY_PID" 2>/dev/null; then
            echo "Restarting XHTTP relay..."
            cd /relay && nohup python3 xhttp_relay.py &
            RELAY_PID=$!
            cd /usr/local/x-ui
        fi
        if [ -f "$DB_PATH" ]; then
            local current_bridge_port
            current_bridge_port=$(ps aux 2>/dev/null | grep "socat.*TCP-LISTEN:8080" | grep -oP 'TCP:127.0.0.1:\K[0-9]+' || echo "")
            local db_port
            db_port=$(get_xray_port)
            if [ -n "$db_port" ] && [ "$db_port" != "8080" ]; then
                if [ "$current_bridge_port" != "$db_port" ]; then
                    cleanup_socat
                    socat TCP-LISTEN:8080,reuseaddr,fork TCP:127.0.0.1:"$db_port" &
                fi
            else
                if [ -n "$current_bridge_port" ]; then
                    cleanup_socat
                fi
            fi
        fi
    done
}

watchdog &

echo "▶️ Testing nginx..."
nginx -t

echo "✅ Starting nginx on port $NGINX_PORT"
exec nginx -g "daemon off;"
