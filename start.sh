#!/bin/bash
# Railway injects $PORT; fall back to 3000 if unset.
export NGINX_PORT="${PORT:-3000}"
DB_PATH="/etc/x-ui/x-ui.db"
XUI_DIR="/usr/local/x-ui"
RELAY_DIR="/relay"

# Force panel settings into the DB so /panel/ and /sub/ work behind nginx.
sqlite3 "$DB_PATH" "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);" 2>/dev/null
sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (key, value) VALUES ('webPort', '2053');" 2>/dev/null
sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (key, value) VALUES ('webBasePath', '/panel/');" 2>/dev/null
sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (key, value) VALUES ('subPort', '2096');" 2>/dev/null
sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (key, value) VALUES ('subPath', '/sub/');" 2>/dev/null
echo "Panel settings forced: webPort=2053 webBasePath=/panel/"

cd "$XUI_DIR"

echo "Starting x-ui..."
./x-ui &
XUI_PID=$!
sleep 5

echo "Starting X4G XHTTP relay..."
cd "$RELAY_DIR"
nohup python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 &
RELAY_PID=$!
cd "$XUI_DIR"

echo "Generating initial nginx config..."
python3 "$RELAY_DIR/gen_nginx.py" || true

echo "Starting nginx on port $NGINX_PORT..."
nginx -g "daemon off;" &
NGINX_PID=$!

watchdog() {
    while true; do
        sleep 5
        if ! kill -0 "$XUI_PID" 2>/dev/null; then
            echo "[watchdog] x-ui down, restarting..."
            cd "$XUI_DIR" && ./x-ui & XUI_PID=$!; sleep 4
        fi
        if ! kill -0 "$RELAY_PID" 2>/dev/null; then
            echo "[watchdog] relay down, restarting..."
            cd "$RELAY_DIR" && nohup python3 xhttp_relay.py > /var/log/xhttp_relay.log 2>&1 & RELAY_PID=$!; cd "$XUI_DIR"
        fi
        if ! kill -0 "$NGINX_PID" 2>/dev/null; then
            echo "[watchdog] nginx down, restarting..."
            nginx -g "daemon off;" & NGINX_PID=$!
        fi
        # Re-generate nginx only when inbounds change (self-manages reload).
        python3 "$RELAY_DIR/gen_nginx.py" || true
    done
}

watchdog &

# Keep the container alive.
wait
