#!/bin/bash

export NGINX_PORT=3000
DB_PATH="/etc/x-ui/x-ui.db"
XUI_DIR="/usr/local/x-ui"
RELAY_DIR="/relay"

cd "$XUI_DIR"

echo "Configuring panel..."
./x-ui setting -port 2053 -webBasePath /panel/ || true

echo "Building nginx config..."
envsubst '${NGINX_PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "Starting x-ui..."
./x-ui &
XUI_PID=$!
sleep 4

echo "Starting XHTTP relay..."
cd "$RELAY_DIR"
python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
RELAY_PID=$!
cd "$XUI_DIR"
sleep 3

get_xray_port() {
    sqlite3 "$DB_PATH" "SELECT port FROM (SELECT port, COUNT(*) as cnt FROM inbounds WHERE enable = 1 AND port NOT IN (2053, 2096) AND protocol IN ('vless', 'vmess', 'trojan', 'shadowsocks') GROUP BY port ORDER BY cnt DESC) LIMIT 1;" 2>/dev/null || echo ""
}

cleanup_socat() {
    for pid in $(pgrep -f "socat.*TCP-LISTEN:8080" 2>/dev/null); do
        kill "$pid" 2>/dev/null || true
    done
}

setup_bridge() {
    if [ ! -f "$DB_PATH" ]; then return; fi
    local target_port
    target_port=$(get_xray_port)
    if [ -n "$target_port" ] && [ "$target_port" != "8080" ]; then
        cleanup_socat
        socat TCP-LISTEN:8080,reuseaddr,fork TCP:127.0.0.1:"$target_port" &
    fi
}

setup_bridge

watchdog() {
    while true; do
        sleep 15
        if ! kill -0 "$XUI_PID" 2>/dev/null; then
            cd "$XUI_DIR" && ./x-ui &
            XUI_PID=$!
            sleep 4
            setup_bridge
        fi
        if ! kill -0 "$RELAY_PID" 2>/dev/null; then
            cd "$RELAY_DIR" && python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
            RELAY_PID=$!
            cd "$XUI_DIR"
        fi
        if [ -f "$DB_PATH" ]; then
            local current_port
            current_port=$(ps aux 2>/dev/null | grep "socat.*TCP-LISTEN:8080" | sed 's/.*TCP:127.0.0.1:\([0-9]*\).*/\1/' || echo "")
            local db_port
            db_port=$(get_xray_port)
            if [ -n "$db_port" ] && [ "$db_port" != "8080" ]; then
                if [ "$current_port" != "$db_port" ]; then
                    cleanup_socat
                    socat TCP-LISTEN:8080,reuseaddr,fork TCP:127.0.0.1:"$db_port" &
                fi
            else
                if [ -n "$current_port" ]; then
                    cleanup_socat
                fi
            fi
        fi
    done
}

watchdog &

echo "Starting nginx..."
nginx -t && exec nginx -g "daemon off;"
