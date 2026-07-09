# 3x-ui + XHTTP Hybrid (X4G Relay)

یک کانتینر که هم 3x-ui (پنل سنایی) داره، هم XHTTP relay از X4G.

## معماری

```
Railway Domain (HTTPS)
        │
        ▼
NGINX (Port 3000)
        │
        ├── /panel/ ──────→ x-ui (2053) ─── پنل سنایی
        ├── /sub/ ────────→ x-ui (2096) ─── ساب 3x-ui
        ├── /xhttp-siz10/ ─→ X4G Relay (9999) ── XHTTP
        └── / (default) ──→ xray (8080) ─── WebSocket
```

## دو نوع کانفیگ قابل ساخت

### ۱. WebSocket (از پنل 3x-ui)

در پنل سنایی یه inbound بسازید:
- Port: 443 (یا 8080)
- Protocol: VLESS
- Network: ws
- Path: هر چی (مثلاً /ws1)

کانفیگ کلاینت:
```
vless://UUID@domain.up.railway.app:443?encryption=none&security=tls&type=ws&host=domain.up.railway.app&path=%2Fws1
```

### ۲. XHTTP (از X4G relay)

این XHTTP مستقیماً از X4G کار می‌کنه. کانفیگها مثل X4G هستن، با این فرمت:
```
vless://UUID@domain.up.railway.app:443?encryption=none&security=tls&type=xhttp&mode=stream-up&host=domain.up.railway.app&path=%2Fxhttp-siz10%2Fstream-up%2FUUID%2FSESSION&sni=domain.up.railway.app&fp=chrome
```

توی v2rayNG و NekoBox این کانفیگها کار می‌کنه.

## نکته مهم

- XHTTP relay **بدون محدودیت و احراز هویت** کار می‌کنه (هر UUIDای قبول می‌کنه)
- WebSocket از پنل سنایی مدیریت میشه (با محدودیت و آمار)
- برای دیدن آمار XHTTP راهی نیست (چون relay ساده‌ست)
