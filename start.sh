#!/bin/bash

export NGINX_PORT=3000
DB_PATH="/etc/x-ui/x-ui.db"
XUI_DIR="/usr/local/x-ui"
RELAY_DIR="/relay"
LOCATIONS_DIR="/etc/nginx/xray-locations"
NGINX_CONF="/etc/nginx/nginx.conf"

mkdir -p "$LOCATIONS_DIR"

cd "$XUI_DIR"

echo "Configuring panel..."
./x-ui setting -port 2053 -webBasePath /panel/ || true

echo "Building nginx config..."
envsubst '${NGINX_PORT}' < /etc/nginx/nginx.conf.template > "$NGINX_CONF"

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

generate_inbound_locations() {
    rm -f "$LOCATIONS_DIR"/*.conf

    if [ ! -f "$DB_PATH" ]; then return; fi

    local rows
    rows=$(sqlite3 "$DB_PATH" "SELECT id, port, stream_settings FROM inbounds WHERE enable = 1 AND port NOT IN (2053, 2096);" 2>/dev/null)

    if [ -z "$rows" ]; then return; fi

    echo "$rows" | while IFS='|' read -r id port stream_settings; do
        [ -z "$port" ] && continue

        local path
        path=$(echo "$stream_settings" | sed 's/.*"path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
        [ -z "$path" ] && path="/"

        local conf_file="$LOCATIONS_DIR/inbound_${id}.conf"
        cat > "$conf_file" << NGINX
location $path {
    proxy_pass http://127.0.0.1:$port;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
}
NGINX
    done
}

reload_nginx() {
    if nginx -t 2>/dev/null; then
        nginx -s reload 2>/dev/null || true
    fi
}

generate_inbound_locations
reload_nginx

watchdog() {
    while true; do
        sleep 15

        if ! kill -0 "$XUI_PID" 2>/dev/null; then
            cd "$XUI_DIR" && ./x-ui &
            XUI_PID=$!
            sleep 4
        fi

        if ! kill -0 "$RELAY_PID" 2>/dev/null; then
            cd "$RELAY_DIR" && python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
            RELAY_PID=$!
            cd "$XUI_DIR"
        fi

        generate_inbound_locations
        reload_nginx
    done
}

watchdog &

echo "Starting nginx..."
nginx -t && exec nginx -g "daemon off;"
