#!/bin/bash

export NGINX_PORT=3000
DB_PATH="/etc/x-ui/x-ui.db"
XUI_DIR="/usr/local/x-ui"
RELAY_DIR="/relay"

SOCAT_PID=""
SOCAT_PORT=""

cleanup_socat() {
    [ -n "$SOCAT_PID" ] && kill "$SOCAT_PID" 2>/dev/null || true
    SOCAT_PID=""
    SOCAT_PORT=""
}

start_socat() {
    local target="$1"
    [ -z "$target" ] && { cleanup_socat; return; }
    cleanup_socat
    socat TCP-LISTEN:8080,reuseaddr,fork TCP:127.0.0.1:"$target" &
    SOCAT_PID=$!
    SOCAT_PORT="$target"
}

get_xray_port() {
    sqlite3 "$DB_PATH" "
        SELECT port FROM inbounds
        WHERE enable = 1
          AND port NOT IN (2053, 2096)
          AND LOWER(protocol) IN ('vless','vmess','trojan','shadowsocks')
        LIMIT 1;
    " 2>/dev/null || echo ""
}

cd "$XUI_DIR"

echo "Configuring panel..."
./x-ui setting -port 2053 -webBasePath /panel/ || true

echo "Building nginx config..."
envsubst '${NGINX_PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "Starting x-ui..."
./x-ui &
XUI_PID=$!
sleep 5

echo "Starting XHTTP relay..."
cd "$RELAY_DIR"
nohup python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
RELAY_PID=$!
cd "$XUI_DIR"

echo "Setting up socat bridge..."
start_socat "$(get_xray_port)"

watchdog() {
    while true; do
        sleep 15

        if ! kill -0 "$XUI_PID" 2>/dev/null; then
            cd "$XUI_DIR" && ./x-ui &
            XUI_PID=$!
            sleep 4
        fi

        if ! kill -0 "$RELAY_PID" 2>/dev/null; then
            cd "$RELAY_DIR" && nohup python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
            RELAY_PID=$!
            cd "$XUI_DIR"
        fi

        local db_port
        db_port=$(get_xray_port)
        if [ -n "$db_port" ] && [ "$db_port" != "8080" ] && [ "$SOCAT_PORT" != "$db_port" ]; then
            echo "Updating socat: 8080 → $db_port"
            start_socat "$db_port"
        elif [ -z "$db_port" ] && [ -n "$SOCAT_PORT" ]; then
            echo "No inbounds found, stopping socat"
            cleanup_socat
        fi
    done
}

watchdog &

echo "Starting nginx..."
exec nginx -g "daemon off;"
