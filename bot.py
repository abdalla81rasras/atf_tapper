#!/usr/bin/env python3
"""
ATF Max Speed Tapper — السكريبت النهائي
==========================================
السكريبت ده بيعمل الآتي:
  1. بيسألك على رابط الموقع (اللي بتفتحه من بوت التيليجرام)
  2. بيستخرج منه initData و tg_id أوتوماتيك
  3. بيعمل login ويجيب TMA session token
  4. بيشغّل التعدين (لو متوقف)
  5. بيدوس على الزرار بسرعة ماكس:
     - boost كل 8 ثواني (max speed)
     - claim بعد كل boost (يضيف الرصيد)
  6. يطالب بالمهام المتكررة كل ساعتين
  7. مفيش سحب — بس claim يضيف للرصيد
  8. يفضل شغّال 24/7

الاستخدام:
  python3 atf_tapper.py
  python3 atf_tapper.py --url "ضع_الرابط_هنا"
"""

import sys, time, re, json, os, secrets, random, urllib.parse
import requests
from datetime import datetime, timezone

BASE_URL = "https://atfminers.asloni.online/miner/index.php"
LOG_FILE = "/home/z/my-project/download/atf_tapper.log"
DEVICE_ID = "dev-tapper-" + secrets.token_hex(8)

# المهام المتكررة (كل ساعتين)
REPEATABLE_TASKS = [
    {"id": "website_visit",         "min_seconds": 12, "reward": 3},
    {"id": "telegram_react_latest", "min_seconds": 22, "reward": 3},
    {"id": "youtube_like_comment", "min_seconds": 32, "reward": 3},
    {"id": "twitter_retweet",       "min_seconds": 32, "reward": 3},
]

BOOST_WAIT = 8.3   # ثواني بين boost و claim (دورة الـ boost)
CLAIM_COOLDOWN = 0.5  # بعد claim قبل الـ boost الجاي

# ============================================================
# Parser: استخراج initData و tg_id من الرابط
# ============================================================
def parse_url(url):
    """استخراج initData و tg_id من رابط التيليجرام TMA"""
    # الحصول على الـ hash fragment (بعد #)
    if '#' not in url:
        raise ValueError("الرابط مش صحيح — مفيش #tgWebAppData")
    fragment = url.split('#', 1)[1]
    # فصل الـ params
    params = urllib.parse.parse_qs(fragment)
    if 'tgWebAppData' not in params:
        raise ValueError("مفيش tgWebAppData في الرابط")
    init_data_raw = params['tgWebAppData'][0]

    # الـ tgWebAppData متعمله encode مرة واحدة. عشان نرجّع الـ initData الأصلي
    # (زي ما السيرفر بيستقبله) لازم نفك الطبقة الأولى فقط:
    #   %3D → =    (separator بين key/value)
    #   %26 → &    (separator بين params)
    #   %25 → %    (literal percent)
    # الطبقة التانية (%7B, %22, %E2%9B%A5, إلخ) لازم تفضل زي ما هي
    # عشان الـ hash verification على السيرفر ينجح.
    init_data = (init_data_raw
                 .replace('%3D', '=')
                 .replace('%26', '&')
                 .replace('%25', '%'))

    # استخراج tg_id من user (بعد فك الـ URL encoding مرة واحدة للقراءة فقط)
    user_decoded = urllib.parse.unquote(init_data)
    user_match = re.search(r'"id"\s*:\s*(\d+)', user_decoded)
    if not user_match:
        raise ValueError("تعذّر استخراج tg_id من الرابط")
    tg_id = user_match.group(1)
    username_match = re.search(r'"username"\s*:\s*"([^"]+)"', user_decoded)
    username = username_match.group(1) if username_match else ""
    return {
        "init_data": init_data,
        "tg_id": tg_id,
        "username": username,
    }

# ============================================================
# ATF Client
# ============================================================
class ATFClient:
    def __init__(self, init_data, tg_id, username=""):
        self.init_data = init_data
        self.tg_id = str(tg_id)
        self.username = username
        self.tma_session_token = ""
        self.device_id = DEVICE_ID
        self.s = requests.Session()
        self.s.headers.update({
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://atfminers.asloni.online",
            "Referer": "https://atfminers.asloni.online/miner/index.html",
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
        })

    def _rid(self):
        return "rq-" + secrets.token_hex(8) + "-" + "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8))

    def _payload(self, obj):
        return {
            "initData": self.init_data,
            "request_id": self._rid(),
            "device_id": self.device_id,
            **obj,
        }

    def _headers(self, extra=None):
        h = {
            "X-Telegram-Init-Data": self.init_data,
        }
        if self.tma_session_token:
            h["X-ATF-TMA-Session"] = self.tma_session_token
        if extra:
            h.update(extra)
        return h

    def post(self, action, body, timeout=20):
        url = f"{BASE_URL}?action={action}&t={int(time.time()*1000)}"
        try:
            r = self.s.post(url, headers=self._headers(), json=body, timeout=timeout)
            return r.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get(self, action, timeout=15):
        url = f"{BASE_URL}?action={action}&t={int(time.time()*1000)}"
        try:
            r = self.s.get(url, headers=self._headers(), timeout=timeout)
            return r.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ===== Auth =====
    def login(self):
        """تسجيل الدخول والحصول على TMA session token"""
        body = self._payload({"tg_id": self.tg_id})
        d = self.post("login", body)
        if d.get("status") == "success" and d.get("tma_session_token"):
            self.tma_session_token = d["tma_session_token"]
            return True
        return False

    # ===== State =====
    def state(self):
        d = self.post("sync_mining_state", self._payload({"tg_id": self.tg_id}))
        if d.get("status") != "success":
            return None
        u = d.get("user", {})
        return {
            "balance": float(u.get("mined_balance", 0)),
            "level": int(u.get("miner_level", 1)),
            "assets": float(u.get("assets_total", 0)),
            "pending": float(u.get("pending_reward", 0)),
            "boost_ready_at": int(u.get("boost_ready_at", 0)),
            "boost_active_until": int(u.get("boost_active_until", 0)),
            "mining_freezes_at": int(u.get("mining_freezes_at", 0)),
            "mining_start": int(u.get("last_mining_start", 0)),
            "has_wallet": bool(u.get("wallet_address")),
            "qualified_buyer": int(u.get("qualified_dex_buyer", 0)),
        }

    # ===== Math Challenge =====
    def math_challenge(self, scope):
        return self.post("get_math_challenge", self._payload({"tg_id": self.tg_id, "scope": scope}))

    def solve_math(self, q):
        m = re.match(r"\s*(-?\d+)\s*([+\-*x/])\s*(-?\d+)", q.strip())
        if not m: return None
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op == "+": return a + b
        if op == "-": return a - b
        if op in ("*", "x"): return a * b
        if op == "/": return a // b if b else 0
        return None

    def start_mining(self):
        ch = self.math_challenge("start_mine")
        if ch.get("status") != "success":
            return ch
        ans = self.solve_math(ch["question"])
        if ans is None:
            return {"status": "error", "message": f"unsolvable: {ch['question']}"}
        return self.post("start_mine", self._payload({
            "tg_id": self.tg_id, "math_challenge_id": ch["challenge_id"], "math_answer": str(ans)
        }))

    # ===== Mining Actions (دوس على الزرار) =====
    def tap_boost(self):
        """دوس على الزرار (activate_boost) — ماكس سبيد"""
        return self.post("activate_boost", self._payload({
            "tg_id": self.tg_id, "display_preview": 0.01
        }), timeout=25)

    def tap_claim(self):
        """دوس على claim (يضيف الـ pending للرصيد)"""
        return self.post("claim", self._payload({
            "tg_id": self.tg_id, "claim_preview": 0.01
        }), timeout=25)

    # ===== Tasks =====
    def start_task(self, tid):
        return self.post("start_task", self._payload({
            "tg_id": self.tg_id, "task_id": tid, "client_started_at": int(time.time())
        }))

    def claim_task(self, tid):
        return self.post("claim_task", self._payload({
            "tg_id": self.tg_id, "task_id": tid, "client_started_at": int(time.time()) - 40
        }))

    def check_tasks(self):
        """فحص المهام المتكررة"""
        earned = 0
        claimed = []
        for t in REPEATABLE_TASKS:
            tid = t["id"]
            sr = self.start_task(tid)
            if sr.get("status") != "success":
                continue  # في cooldown
            time.sleep(t["min_seconds"] + 2)
            cr = self.claim_task(tid)
            if cr.get("status") == "success":
                r = cr.get("reward", 0)
                earned += r
                claimed.append(f"{tid}(+{r})")
            time.sleep(1)
        return earned, claimed

    # ===== Activity =====
    def record_daily_interaction(self):
        return self.post("record_daily_interaction", self._payload({
            "tg_id": self.tg_id, "foreground_seconds": 60, "scroll_pixels": 500,
            "unique_menus": 5, "menu_changes": 4, "trusted_input": 1
        }))

    def record_navigation_batch(self, delta=10):
        return self.post("record_navigation_batch", self._payload({
            "tg_id": self.tg_id, "delta": delta, "batch_id": "nav_" + self._rid()
        }))

# ============================================================
# Farmer - الـ Tapper الرئيسي
# ============================================================
class Tapper:
    def __init__(self, client):
        self.c = client
        self.start_balance = None
        self.start_time = time.time()
        self.stats = {"boosts": 0, "claims": 0, "tasks": 0, "task_atf": 0.0, "mining_atf": 0.0, "penalties": 0, "errors": 0}
        self.last_task_check = 0
        self.last_activity_check = 0

    def log(self, *a):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        msg = f"[{ts}] " + " ".join(str(x) for x in a)
        print(msg, flush=True)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(msg + "\n")
        except: pass

    def show_balance(self, st=None):
        if not st:
            st = self.c.state()
        if not st:
            self.log("✗ فشل الاتصال")
            return
        elapsed = time.time() - self.start_time
        gain = (st["balance"] - self.start_balance) if self.start_balance else 0
        rate_hr = (gain * 3600 / elapsed) if elapsed > 0 else 0
        rate_day = rate_hr * 24
        self.log(f"📊 {st['balance']:.4f} ATF | Lvl {st['level']} | +{gain:.4f} ({int(elapsed)}s) | ⚡ {rate_hr:.4f}/h ≈ {rate_day:.2f}/day")

    def ensure_mining(self, st):
        if not st["has_wallet"]:
            self.log("⚠️  محفظة غير مربوطة — لازم تربط محفظة TON الأول")
            return False
        if st["mining_start"] > 0:
            return True
        self.log("🚀 بدء التعدين...")
        r = self.c.start_mining()
        if r.get("status") == "success":
            self.log("✓ بدأ التعدين")
            return True
        self.log(f"✗ فشل: {r.get('message','')}")
        return False

    def wait_for_boost_ready(self, st):
        """استنى لحد ما boost يبقى Ready"""
        now = int(time.time())
        wait = max(0, st["boost_ready_at"] - now)
        if wait > 0:
            self.log(f"⏳ Boost مش Ready — استنى {wait}s")
            time.sleep(wait + 0.5)
        return True

    def check_penalty(self, response):
        """لو فيه penalty، استنى لحد ما يخلص"""
        if not isinstance(response, dict):
            return 0
        if response.get("status") == "penalty":
            until = int(response.get("penalty_until", 0))
            wait = max(60, until - int(time.time()) + 5)
            self.log(f"⚠️  Penalty — انتظار {wait}s")
            self.stats["penalties"] += 1
            time.sleep(wait)
            return wait
        if response.get("status") == "rate_limited":
            self.log("⚠️  Rate limited — انتظار 60s")
            time.sleep(60)
            return 60
        return 0

    def check_frozen(self, response):
        """لو الـ mining متجمد، استنى"""
        if not isinstance(response, dict):
            return False
        msg = str(response.get("message", "")).lower()
        if "frozen" in msg or "freeze" in msg:
            self.log(f"❄️  التعدين متجمد — استنى 5 دقايق")
            time.sleep(300)
            return True
        return False

    def tap_loop(self):
        """الحلقة الرئيسية - دوس على الزرار بسرعة ماكس"""
        try:
            st = self.c.state()
            if not st:
                self.log("✗ فشل الاتصال — retry في 10s")
                time.sleep(10)
                return

            if not self.ensure_mining(st):
                return

            # فحص التجمد
            if st["mining_freezes_at"] > 0:
                mins = (st["mining_freezes_at"] - int(time.time())) // 60
                if mins < 0:
                    self.log("❄️  التعدين متجمد — لازم تبدأه تاني")
                    # حاول تبدأه
                    r = self.c.start_mining()
                    if r.get("status") != "success":
                        self.log(f"✗ فشل إعادة بدء التعدين: {r.get('message','')}")
                        time.sleep(60)
                        return

            # فحص المهام كل ساعتين
            now = int(time.time())
            if now - self.last_task_check > 7200:
                self.last_task_check = now
                self.log("📝 فحص المهام المتكررة...")
                earned, claimed = self.c.check_tasks()
                if earned > 0:
                    self.stats["tasks"] += len(claimed)
                    self.stats["task_atf"] += earned
                    self.log(f"📝 +{earned} ATF: {', '.join(claimed)}")

            # فحص النشاط كل ساعة
            if now - self.last_activity_check > 3600:
                self.last_activity_check = now
                try: self.c.record_daily_interaction()
                except: pass
                try: self.c.record_navigation_batch()
                except: pass

            # انتظر Ready
            self.wait_for_boost_ready(st)

            # === دوس على الزرار (BOOST) ===
            b = self.c.tap_boost()
            if b.get("status") == "success":
                self.stats["boosts"] += 1
                self.log(f"✓ Boost #{self.stats['boosts']}")
            else:
                self.log(f"✗ Boost: {b.get('status')} - {b.get('message','')}")
                self.check_penalty(b)
                self.check_frozen(b)
                return

            # استنى دورة الـ boost تكمل (8 ثواني)
            self.log("⏳ انتظار 8.3s...")
            time.sleep(BOOST_WAIT)

            # === دوس على CLAIM (يضيف الرصيد) ===
            c = self.c.tap_claim()
            if c.get("status") == "success":
                self.stats["claims"] += 1
                amt = float(c.get("claimed_amount", 0))
                self.stats["mining_atf"] += amt
                new_bal = c.get("new_pool_balance")
                self.log(f"💰 Claim: +{amt:.6f} | الرصيد: {new_bal}")
            else:
                self.log(f"✗ Claim: {c.get('status')} - {c.get('message','')}")
                self.check_frozen(c)

            time.sleep(CLAIM_COOLDOWN)
        except Exception as e:
            import traceback
            self.log(f"❌ exception في tap_loop: {e}")
            self.log(traceback.format_exc())
            time.sleep(10)

    def run(self):
        self.log("=" * 70)
        self.log(f"🚀 ATF Max Speed Tapper بدأ: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"👤 User: {self.c.tg_id} ({self.c.username or 'no username'})")
        self.log(f"📁 السجل: {LOG_FILE}")
        self.log("=" * 70)

        st = self.c.state()
        if not st:
            self.log("✗ فشل الاتصال — تأكد إن initData صحيح")
            return
        self.start_balance = st["balance"]
        self.log(f"🎯 رصيد البداية: {self.start_balance:.4f} ATF | المستوى: {st['level']}")

        # تسجيل signal handlers للديباج
        import signal, traceback, faulthandler
        def sig_handler(signum, frame):
            self.log(f"⚠️  Signal {signum} received at frame:\n{traceback.format_stack(frame)}")
            if signum == signal.SIGTERM:
                self.log("👋 SIGTERM — exiting gracefully")
                sys.exit(0)
        signal.signal(signal.SIGTERM, sig_handler)
        signal.signal(signal.SIGINT, sig_handler)
        # تفعيل faulthandler لطباعة stack trace لو segment fault
        try:
            faulthandler.enable()
            faulthandler.dump_traceback_later(60, exit=False)
        except Exception:
            pass

        cycle = 0
        while True:
            cycle += 1
            try:
                self.tap_loop()
            except Exception as e:
                import traceback as tb
                self.log(f"✗ خطأ في الدورة #{cycle}: {e}")
                self.log(tb.format_exc())
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                # لو فيه مشكلة في الاتصال، استنى 30 ثانية وحاول تاني
                time.sleep(30)

            # ملخص كل 10 دورات
            if cycle % 10 == 0:
                try:
                    self.show_balance()
                except Exception:
                    pass

# ============================================================
# Main
# ============================================================
def banner():
    print("=" * 70)
    print("🚀 ATF Max Speed Tapper")
    print("=" * 70)
    print("السكريبت ده بيدوس على الزرار بسرعة ماكس:")
    print("  ⚡ Boost كل 8 ثواني (أقصى سرعة من السيرفر)")
    print("  💰 Claim بعد كل boost (يضيف الرصيد أوتوماتيك)")
    print("  📝 المهام المتكررة كل ساعتين")
    print("  🚫 مفيش سحب — بس claim")
    print("=" * 70)

def ask_for_url():
    """اسأل المستخدم على الرابط"""
    print()
    print("📋 الصق رابط الموقع اللي بتفتحه من بوت التيليجرام:")
    print("   (هو طويل ويبدا بـ https://atfminers.asloni.online/...)")
    print()
    url = input("🔗 الرابط: ").strip()
    if not url:
        print("❌ لازم تدخل الرابط")
        sys.exit(1)
    return url

def main():
    banner()

    # الحصول على الرابط
    url = None
    if len(sys.argv) > 1 and sys.argv[1] == "--url" and len(sys.argv) > 2:
        url = sys.argv[2]
    else:
        url = ask_for_url()

    # استخراج initData و tg_id
    print()
    print("🔍 تحليل الرابط...")
    try:
        info = parse_url(url)
    except Exception as e:
        print(f"❌ خطأ في الرابط: {e}")
        sys.exit(1)

    print(f"✓ User ID: {info['tg_id']}")
    print(f"✓ Username: {info['username'] or '(مش موجود)'}")
    print(f"✓ initData: {info['init_data'][:80]}...")

    # إنشاء client
    print()
    print("🔐 تسجيل الدخول...")
    client = ATFClient(info["init_data"], info["tg_id"], info["username"])

    # محاولة login
    if not client.login():
        print("⚠️  تعذّر تسجيل الدخول — هحاول نشتغل بدون session token")

    # فحص الحالة
    print()
    print("📊 فحص الحالة...")
    st = client.state()
    if not st:
        print("❌ فشل الاتصال بالسيرفر — تأكد إن initData صحيح وغير منتهي")
        print("   (initData ينتهي بعد 24 ساعة — افتح الموقع من جديد من البوت)")
        sys.exit(1)

    print(f"✓ الرصيد: {st['balance']:.4f} ATF")
    print(f"✓ المستوى: {st['level']}")
    print(f"✓ محفظة: {'مربوطة' if st['has_wallet'] else 'غير مربوطة'}")

    if not st["has_wallet"]:
        print()
        print("⚠️  مفيش محفظة مربوطة — لازم تربط محفظة TON الأول من الموقع")
        print("   افتح الموقع من التيليجرام واضغط Connect Wallet")
        sys.exit(1)

    # تشغيل الـ Tapper
    print()
    print("🚀 تشغيل الـ Tapper...")
    print("=" * 70)
    tapper = Tapper(client)
    try:
        tapper.run()
    except KeyboardInterrupt:
        print()
        print("👋 تم الإيقاف")
        tapper.show_balance()

if __name__ == "__main__":
    main()
