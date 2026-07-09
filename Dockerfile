FROM alpine:3.19

RUN apk add --no-cache \
    curl bash ca-certificates socat tzdata sqlite nginx gettext jq \
    python3 py3-pip

RUN ln -sf /usr/share/zoneinfo/Asia/Tehran /etc/localtime

# 3x-ui panel (bundles its own xray-core download on first run)
RUN curl -L https://github.com/mhsanaei/3x-ui/releases/download/v3.4.2/x-ui-linux-amd64.tar.gz -o /tmp/x-ui.tar.gz \
    && tar -xzf /tmp/x-ui.tar.gz -C /usr/local/ \
    && rm /tmp/x-ui.tar.gz \
    && chmod +x /usr/local/x-ui/x-ui

RUN mkdir -p /etc/x-ui /var/log/x-ui /relay

COPY requirements.txt /relay/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /relay/requirements.txt

# X4G XHTTP relay (kept for backwards compatibility)
COPY relay_vless.py /relay/relay_vless.py
COPY xhttp_relay.py /relay/xhttp_relay.py

# Dynamic nginx generator (routes each inbound by its path)
COPY gen_nginx.py /relay/gen_nginx.py

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
