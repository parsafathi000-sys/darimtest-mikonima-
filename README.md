# 3x-ui + XHTTP Hybrid — نسخه‌ی ارتقا‌یافته (upgrade-v3)

رفع سه مشکل اساسی نسخه‌ی قبلی:
۱. **پورت ۴۴۳**: دیگه مجبور نیستی پورت رو ۸۰۸۰ بزنی — هر پورتی (۴۴۳، ۲۰۸۷، …) بزنی کار می‌کنه.
۲. **چند inbound همزمان**: می‌تونی چندتا inbound (ws / xhttp / grpc) با pathهای متفاوت بسازی که هیچ‌کدوم اون یکی رو قطع نکنه.
۳. **XHTTP از پنل**: می‌تونی مستقیماً توی پنل inbound با شبکه‌ی **xhttp** بسازی.

---

## معماری جدید

```
کاربر (دامنه Railway)
   │ HTTPS :443  (TLS رو Railway قطع می‌کنه)
   ▼
Railway Edge  ──►  کانتینر :$PORT (پیش‌فرض ۳۰۰۰)
                       │
                       ▼
                  NGINX (مسیریابی خودکار بر اساس PATH)
   ├── /panel/        ──► x-ui panel :2053
   ├── /sub/          ──► subscription :2096
   ├── /xhttp-siz10/ ──► X4G relay (python) :9999   ← برای سازگاری با کانفیگ‌های قبلی
   ├── /ws1/          ──► xray :<پورت-داخلی-۱>      ← ws inbound اول
   ├── /ws2/          ──► xray :<پورت-داخلی-۲>      ← ws inbound دوم
   ├── /xh1/          ──► xray :<پورت-داخلی-۳>      ← xhttp inbound
   └── (بقیه pathها) ──► xray :<پورت همون inbound>
```

**کلید کار**: nginx دیگه همه‌ی ترافیک رو نمی‌فرسته به یه پورت ثابت (۸۰۸۰) با socat.
به‌جاش، یه اسکریپت (`gen_nginx.py`) هر چند ثانیه دیتابیس ۳x-ui رو چک
می‌کنه و برای **هر inbound فعال** یه بلوک `location` می‌سازه که مسیر (path) رو
مستقیم به **پورت داخلی xray اون inbound** وصل می‌کنه. پس:
- پورت داخلی هرچی باشه فرقی نمی‌کنه (مسیریابی با path انجام می‌شه).
- چند inbound با pathهای متفاوت همزمان زنده‌ان بدون تداخل.

---

## راه‌اندازی ۰ تا ۱۰۰

### ۱. آپلود به GitHub
فایل‌های داخل این پوشه (`upgrade-v3`) رو توی یه ریپازیتوری جدید آپلود کن:
```
Dockerfile  gen_nginx.py  start.sh  relay_vless.py  xhttp_relay.py  requirements.txt
```
راه خط فرمان (از داخل پوشه):
```bash
git init
git add .
git commit -m "3x-ui upgrade-v3 (dynamic routing + xhttp)"
git remote add origin https://github.com/USER/NAME.git
git branch -M main
git push -u origin main
```

### ۲. دیپلوی روی Railway
۱. railway.app → New Project → Deploy from GitHub repo
۲. ریپازیتوری رو انتخاب کن
۳. ۲-۳ دقیقه صبر کن تا بیلد تموم بشه
۴. Settings → Networking → Generate Domain (دامنه‌ای مثل `x.up.railway.app` می‌ده)

### ۳. ورود به پنل
```
آدرس:  https://YOUR-DOMAIN.up.railway.app/panel/
یوزر:  admin
پسورد: admin     ← فوراً بعد از ورود عوضش کن!
```

---

## ساخت WebSocket (مشکل ۱ و ۲ حل‌شده)

توی پنل `+` بزن:
| فیلد | مقدار |
|------|--------|
| Remark | ws1 |
| Protocol | VLESS |
| Port | **443** (یا هر پورت دلخواه — فقط ترجیحاً ۴۴۳) |
| Listen | 0.0.0.0 |
| Network | ws |
| Security | none |
| Path | /ws1 |

ذخیره کن. ۵ ثانیه صبر کن (ژنراتور nginx خودش بلوک رو اضافه می‌کنه).

کانفیگ کلاینت (از پنل کپی کن، یا دستی):
```
vless://UUID@YOUR-DOMAIN.up.railway.app:443?encryption=none&security=tls&type=ws&host=YOUR-DOMAIN.up.railway.app&path=%2Fws1#WS1
```
> نکته: چون Railway خودش TLS رو انجام می‌ده، توی پنل **Security = none** بزن
> ولی توی کلاینت **security=tls** باشه (دامنه‌ی ریلوی TLS داره).

**چندتا WS همزمان؟** فقط path رو فرق کن: `/ws2`، `/ws3` … هیچ‌کدوم بقیه رو قطع نمی‌کنه.

---

## ساخت XHTTP (از پنل — مشکل ۳)

توی پنل `+` بزن:
| فیلد | مقدار |
|------|--------|
| Remark | xh1 |
| Protocol | VLESS |
| Port | 443 |
| Listen | 0.0.0.0 |
| Network | **xhttp** |
| Security | **none** |
| Path | /xh1 |
| (XHTTP mode) | stream-up (یا packet-up) |

کانفیگ کلاینت (دستی روی همون UUID):
```
vless://UUID@YOUR-DOMAIN.up.railway.app:443?encryption=none&security=tls&type=xhttp&mode=stream-up&host=YOUR-DOMAIN.up.railway.app&path=%2Fxh1&sni=YOUR-DOMAIN.up.railway.app&fp=chrome#XHTTP
```
> دوباره تأکید: پنل `security=none`، کلاینت `security=tls`.

این ترکیب دقیقاً همون حس «ترافیک مثل وب معمولی روی دامنه‌ی ریلوی» رو می‌ده
و سانسورپذیر نیست.

---

## درباره‌ی XHTTP + Reality (محدودیت مهم)

**Reality از پروکسی HTTP ریلوی رد نمی‌شه** و دلیلش فنیه:
- Reality برای کار نیاز داره کلاینت **مستقیم** به xray وصل بشه و TLS fingerprint
  (ClientHello) رو خودش بسازه.
- ریلوی توی لبه‌ی شبکه **TLS رو قطع می‌کنه** و به کانتینر **Plaintext (HTTP)** می‌فرسته.
  پس xray اصلاً TLS رو نمی‌بینه و Reality شکست می‌خوره.

پس حتی اگه توی پنل inbound با `security=reality` بسازی، از بیرون (روی دامنه‌ی
اصلی ریلوی) وصل نمی‌شه. راه‌حل قبلی (Railway TCP Proxy با دامنه‌ی `*.rlwy.net`)
هم که طبق گفته‌ی شما توی ایران فیلتر شده.

**راه‌حل جایگزین (همین XHTTP + TLS بالا)** دقیقاً همون هدف — عبور ناپیدای
سانسور — رو بدون نیاز به TCP خام تأمین می‌کنه. اگه واقعاً به Reality نیاز
داری، باید روی یه VPS واقعی (مثلاً Hetzner / Contabo) با IP ثابت و دسترسی
TCP خام پیاده‌ش کنی — روی Railway با HTTP proxy غیرممکنه.

---

## فایل‌ها
| فایل | کارکرد |
|------|---------|
| `Dockerfile` | Alpine + 3x-ui v3.4.2 + nginx + python relay |
| `gen_nginx.py` | ژنراتور خودکار nginx از دیتابیس ۳x-ui (مسیریابی بر اساس path) |
| `start.sh` | راه‌اندازی x-ui + relay + nginx + watchdog + ژنراتور |
| `xhttp_relay.py` / `relay_vless.py` | رله‌ی X4G (سازگاری با کانفیگ‌های قبلی) |
| `requirements.txt` | fastapi + uvicorn |

---

## عیب‌یابی
- **پنل باز نمی‌شه**: لاگ ریلوی رو چک کن؛ صبر کن بیلد تموم بشه.
- **WS کار نمی‌کنه**: پورت رو ۴۴۳ (یا همون‌چیزی که توی کلاینت زدی) و path رو درست بزن.
- **XHTTP کار نمی‌کنه**: `mode=stream-up` باشه و توی کلاینت `security=tls` باشه (نه none).
- **تغییرات inbound دیده نمی‌شه**: ژنراتور هر ۵ ثانیه چک می‌کنه؛ کمی صبر کن یا لاگ کانتینر رو ببین.
- **همه‌ی inboundها قطع شدن**: مطمئن شو هیچ دوتا inbound path یکسان ندارن و path با
  `/panel` ، `/sub` ، `/xhttp-siz10` شروع نشه.
