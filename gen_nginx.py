#!/usr/bin/env python3
"""Generate nginx config dynamically from 3x-ui inbounds.

Each enabled HTTP-based inbound (ws / xhttp / httpupgrade / grpc) gets a
`location` block that proxies its *path* to the internal xray port.

Why this fixes the old problems:
  * Old design: nginx sent ALL traffic to a single socat -> one xray port.
    -> only one inbound worked, and the port had to be 8080.
  * New design: routing is by PATH, not port. The inbound port is only an
    internal bind; the client always connects on Railway's HTTPS port (443)
    and nginx routes by path to the right xray port. So ANY port works and
    many inbounds can coexist on different paths.
"""
import sqlite3
import json
import os
import sys
import subprocess

DB_PATH = "/etc/x-ui/x-ui.db"
NGINX_CONF = "/etc/nginx/nginx.conf"
NGINX_PORT = os.environ.get("NGINX_PORT", "3000")
RELAY_PORT = "9999"

# Paths we must never let a user inbound hijack.
RESERVED = ("/panel", "/sub", "/xhttp-siz10")

TPL = r"""worker_processes auto;
events {
    worker_connections 4096;
    multi_accept on;
}

http {
    server {
        listen __PORT__;

        # 3x-ui admin panel
        location /panel/ {
            proxy_pass http://127.0.0.1:2053/panel/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # subscription
        location /sub/ {
            proxy_pass http://127.0.0.1:2096/sub/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # X4G XHTTP relay (python) - kept for backwards compatibility
        location /xhttp-siz10/ {
            proxy_pass http://127.0.0.1:__RELAY__;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_buffering off;
            proxy_request_buffering off;
        }

__LOCATIONS__

        # catch-all (also keeps Railway health checks happy)
        location / {
            return 200 'ok';
            add_header Content-Type text/plain;
        }
    }
}
"""


def parse_json(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def get_inbounds():
    if not os.path.exists(DB_PATH):
        return []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT port, protocol, stream_settings, enable FROM inbounds")
        rows = cur.fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        sys.stderr.write("DB read error: %s\n" % e)
        return []


def inbound_path(ss):
    net = (ss.get("network") or "tcp").lower()
    path = ""
    if net == "ws":
        path = (ss.get("wsSettings") or {}).get("path", "")
    elif net == "xhttp":
        path = (ss.get("xhttpSettings") or {}).get("path", "")
    elif net == "httpupgrade":
        path = (ss.get("httpupgradeSettings") or {}).get("path", "")
    elif net == "grpc":
        path = (ss.get("grpcSettings") or {}).get("serviceName", "")
    return net, path


def build_locations(inbounds):
    blocks = []
    seen = set()
    for ib in inbounds:
        if not ib.get("enable"):
            continue
        port = ib.get("port")
        if port in (2053, 2096):
            continue
        ss = parse_json(ib.get("stream_settings"), {})
        net, path = inbound_path(ss)
        if net not in ("ws", "xhttp", "httpupgrade", "grpc"):
            continue
        if not path:
            continue
        if not path.startswith("/"):
            path = "/" + path
        base = path.rstrip("/")
        if base in seen:
            continue
        if base in RESERVED or base.startswith(RESERVED):
            continue
        seen.add(base)
        blocks.append(render_block(net, base, port))
    return "\n".join(blocks)


def render_block(net, base, port):
    head = (
        "        # %s inbound -> xray:%s (path %s)\n"
        "        location %s {\n"
        "            proxy_http_version 1.1;\n"
        "            proxy_set_header Host $host;\n"
        "            proxy_set_header X-Real-IP $remote_addr;\n"
        "            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "            proxy_set_header X-Forwarded-Proto $scheme;\n"
    ) % (net, port, base, base)

    if net == "grpc":
        body = (
            "            grpc_pass grpc://127.0.0.1:%s;\n"
            "            grpc_read_timeout 300s;\n"
            "            grpc_send_timeout 300s;\n"
        ) % port
    else:
        body = "            proxy_pass http://127.0.0.1:%s;\n" % port
        if net in ("ws", "httpupgrade"):
            body += (
                '            proxy_set_header Upgrade $http_upgrade;\n'
                '            proxy_set_header Connection "upgrade";\n'
                "            proxy_buffering off;\n"
                "            proxy_request_buffering off;\n"
            )
        elif net == "xhttp":
            body += (
                "            proxy_buffering off;\n"
                "            proxy_request_buffering off;\n"
                "            proxy_read_timeout 300s;\n"
            )
    return head + body + "        }\n"


def generate():
    inbounds = get_inbounds()
    locations = build_locations(inbounds)
    return (TPL
            .replace("__PORT__", str(NGINX_PORT))
            .replace("__RELAY__", str(RELAY_PORT))
            .replace("__LOCATIONS__", locations))


def main():
    conf = generate()
    try:
        with open(NGINX_CONF, "r") as f:
            current = f.read()
    except Exception:
        current = ""
    if conf == current:
        print("[gen_nginx] no change")
        return 0
    # validate before applying
    with open(NGINX_CONF, "w") as f:
        f.write(conf)
    r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("[gen_nginx] nginx -t failed:\n%s\n" % (r.stderr or r.stdout))
        with open(NGINX_CONF, "w") as f:
            f.write(current)
        return 1
    subprocess.run(["nginx", "-s", "reload"], capture_output=True)
    print("[gen_nginx] nginx config updated (%d inbound route(s))"
          % conf.count("xray:"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
