import os, re, time, json, threading, html as ihtml, requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = "https://api.app-prg1.zerops.io/api/rest/public"

sessions = {}  # chat_id -> {step, zerops_token, client_id, tg_token, bot_username, project_name, pid}

ZEN_KEY = "oc_sk_f70267f06abb_w_xXuLT4OJn3Fvxo6jwLLtf9at5-MC2i"


def tg(method, payload=None):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                          json=payload or {}, timeout=20)
        return r.json()
    except Exception as e:
        print("tg error:", e, flush=True)
        return {}


def send(chat_id, text, kb=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb:
        p["reply_markup"] = kb
    return tg("sendMessage", p)


def zapi(token, method, path, body=None, timeout=60):
    try:
        r = requests.request(method, API_BASE + path,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"},
                             json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"_raw": r.text[:500]}
    except Exception as e:
        return 0, {"_error": repr(e)[:300]}


def hide(t):
    t = (t or "").strip()
    if len(t) <= 8:
        return "***"
    return t[:4] + "…" + t[-4:]


def build_router_yaml(project):
    return f"""project:
  name: {project}
  description: "hermes + 9router auto install"
  corePackage: LIGHT
services:
  - hostname: router
    type: nodejs@22
    enableSubdomainAccess: true
    minContainers: 1
    maxContainers: 1
    minRam: 1
    maxRam: 1
    minCpu: 1
    maxCpu: 2
    buildFromGit: https://github.com/mmdgk123/ninerouter-zerops
"""


def build_hermes_yaml(tg_token, chat_id, router_key):
    dotenv = (
        f"NEW_TG_TOKEN={tg_token}\n"
        f"NEW_TG_CHAT={chat_id}\n"
        f"ROUTER_KEY={router_key}\n"
    )
    dg = "\n".join("      " + l for l in dotenv.strip().split("\n"))
    return f"""services:
  - hostname: app
    type: nodejs@22
    enableSubdomainAccess: true
    minContainers: 1
    maxContainers: 1
    minRam: 1
    maxRam: 1
    minCpu: 1
    maxCpu: 2
    buildFromGit: https://github.com/mmdgk123/hermes-zerops-template
    dotEnvSecrets: |
{dg}
"""


def get_client_id(zt):
    code, cl = zapi(zt, "get", "/user/client-list")
    if code != 200:
        return None
    try:
        items = cl if isinstance(cl, list) else cl.get("list") or []
        if items:
            first = items[0]
            return (first.get("client") or {}).get("id") or first.get("clientId")
    except Exception:
        pass
    return None


def phase1_router(chat_id, s):
    """Create project with router service only, watch it, then ask for API key."""
    zt = s["zerops_token"]
    send(chat_id, "⏳ توکن معتبره، دارم 9router رو می‌سازم… (مرحله ۱ از ۲)")
    client_id = get_client_id(zt)
    if not client_id:
        send(chat_id, "❌ نتونستم client-id رو پیدا کنم. با /start دوباره شروع کن.")
        sessions.pop(chat_id, None)
        return
    s["client_id"] = client_id
    code, res = zapi(zt, "post", f"/client/{client_id}/project/import",
                     {"yaml": build_router_yaml(s["project_name"])}, timeout=120)
    if code != 200:
        send(chat_id, f"❌ ساخت پروژه ناموفق بود (HTTP {code}):\n<code>" +
             ihtml.escape(json.dumps(res)[:1500]) + "</code>")
        sessions.pop(chat_id, None)
        return
    pid = res.get("projectId", "?")
    s["pid"] = pid
    send(chat_id, f"✅ پروژه ساخته شد!\n🆔 <code>{ihtml.escape(str(pid))}</code>\n\n🔀 9router داره بیلد می‌گیره (~۵ دقیقه). وقتی بالا اومد لینک داشبورد رو میدم.")
    threading.Thread(target=watch_router, args=(chat_id, zt, pid), daemon=True).start()


def watch_router(chat_id, token, pid, tries=40):
    import time as _t
    for _ in range(tries):
        _t.sleep(60)
        try:
            _, data = zapi(token, "get", f"/project/{pid}/service-stack", timeout=30)
            items = data.get("list", []) if isinstance(data, dict) else []
            for sv in items:
                if sv.get("isSystem") or sv.get("name") != "router":
                    continue
                st = sv.get("status", "?")
                if st in ("READY", "RUNNING", "OK", "ACTIVE") or "RUN" in st.upper():
                    send(chat_id, f"🔀 9router بالا اومد!\n\nداشبورد:\nhttps://app.zerops.io/project/{pid}\n(سرویس router → ساب‌دامین → /dashboard، پسورد اول: 123456)\n\nکلید API رو از داشبورد بگیر (بخش API keys) و همین‌جا بفرست تا هرمس رو نصب کنم:")
                    s = sessions.get(chat_id)
                    if s is not None:
                        s["step"] = "rkey"
                    return
                if "FAIL" in st.upper() or "ERROR" in st.upper():
                    send(chat_id, f"❌ بیلد 9router خراب شد ({st}). لاگ رو تو داشبورد ببین.")
                    sessions.pop(chat_id, None)
                    return
        except Exception:
            pass
    send(chat_id, "⏰ 9router بالا نیومد. تو داشبورد چک کن:\nhttps://app.zerops.io/project/" + str(pid))


def phase2_hermes(chat_id, s):
    """Import hermes service into the same project with the router key."""
    zt, pid = s["zerops_token"], s["pid"]
    send(chat_id, "⏳ کلید رو گرفتم، دارم هرمس رو نصب می‌کنم… (مرحله ۲ از ۲)")
    code, res = zapi(zt, "post", f"/project/{pid}/service-stack/import",
                     {"yaml": build_hermes_yaml(s["tg_token"], chat_id, s["router_key"])},
                     timeout=120)
    if code != 200:
        send(chat_id, f"❌ ساخت سرویس هرمس ناموفق بود (HTTP {code}):\n<code>" +
             ihtml.escape(json.dumps(res)[:1500]) + "</code>")
        return
    # inject zen key + allowlist into the new app service
    try:
        _, stacks = zapi(zt, "get", f"/project/{pid}/service-stack", timeout=30)
        for st in (stacks.get("list", []) if isinstance(stacks, dict) else []):
            if st.get("isSystem") or st.get("name") != "app":
                continue
            sid = st.get("id")
            zapi(zt, "post", f"/service-stack/{sid}/user-data",
                 {"key": "OPENCODE_ZEN_API_KEY", "content": ZEN_KEY, "sensitive": True},
                 timeout=30)
            zapi(zt, "post", f"/service-stack/{sid}/user-data",
                 {"key": "TELEGRAM_ALLOWED_USERS", "content": str(chat_id), "sensitive": False},
                 timeout=30)
            break
    except Exception as e:
        print("post-create env inject failed:", e, flush=True)
    send(chat_id, "✅ سرویس هرمس ساخته شد! داره نصب میشه (~۱۰ دقیقه). وقتی بالا اومد خبرت می‌کنم.")
    threading.Thread(target=watch_hermes, args=(chat_id, zt, pid), daemon=True).start()


def watch_hermes(chat_id, token, pid, tries=40):
    import time as _t
    for _ in range(tries):
        _t.sleep(60)
        try:
            _, data = zapi(token, "get", f"/project/{pid}/service-stack", timeout=30)
            items = data.get("list", []) if isinstance(data, dict) else []
            for sv in items:
                if sv.get("isSystem") or sv.get("name") != "app":
                    continue
                st = sv.get("status", "?")
                if st in ("READY", "RUNNING", "OK", "ACTIVE") or "RUN" in st.upper():
                    send(chat_id, f"🎉 هرمس بالا اومد!\n\nبات هرمست رو تو تلگرام باز کن و /start بزن — بدون کد pairing، مستقیم وصل میشی.")
                    sessions.pop(chat_id, None)
                    return
                if "FAIL" in st.upper() or "ERROR" in st.upper():
                    send(chat_id, f"❌ نصب هرمس خراب شد ({st}). لاگ رو تو داشبورد ببین.")
                    return
        except Exception:
            pass
    send(chat_id, "⏰ هنوز بالا نیومده. تو داشبورد چک کن:\nhttps://app.zerops.io/project/" + str(pid))


def handle(chat_id, text):
    text = (text or "").strip()
    s = sessions.get(chat_id)
    if text.startswith("/start") or text.startswith("/cancel"):
        sessions[chat_id] = {"step": "zerops"}
        send(chat_id, "سلام! 🤖\n\nتوکن Zerops رو بفرست (از app.zerops.io بخش Access Token Management):\n\n⚠️ توکن رو فقط همین‌جا بفرست، جایی ذخیره نمیشه.")
        return
    if not s:
        send(chat_id, "با /start شروع کن.")
        return
    step = s.get("step")
    if step == "zerops":
        if len(text) < 20:
            send(chat_id, "❌ این شبیه توکن نیست. دوباره بفرست:")
            return
        s["zerops_token"] = text
        s["step"] = "tg"
        send(chat_id, f"✅ توکن Zerops گرفتم ({hide(text)})\n\nحالا توکن بات تلگرامِ هرمس جدید رو بفرست (از @BotFather با /newbot):")
        return
    if step == "tg":
        if ":" not in text or len(text) < 30:
            send(chat_id, "❌ فرمت توکن بات اشتباهه (باید شامل : باشه). دوباره بفرست:")
            return
        try:
            r = requests.get(f"https://api.telegram.org/bot{text}/getMe", timeout=15).json()
            if not r.get("ok"):
                send(chat_id, "❌ این توکن بات معتبر نیست. دوباره بفرست:")
                return
            s["bot_username"] = r.get("result", {}).get("username", "?")
        except Exception:
            pass
        s["tg_token"] = text
        s["step"] = "name"
        send(chat_id, f"✅ بات @{s.get('bot_username', '?')} تایید شد.\n\nاسم پروژه رو بفرست (فقط حروف کوچیک انگلیسی، مثل hermes2):")
        return
    if step == "name":
        name = re.sub(r"[^a-z0-9-]", "", text.lower())[:25] or "hermes-auto"
        s["project_name"] = name
        s["step"] = "building_router"
        threading.Thread(target=phase1_router, args=(chat_id, dict(s)), daemon=True).start()
        # keep live session ref for step transition
        sessions[chat_id] = s
        return
    if step == "rkey":
        if len(text) < 10:
            send(chat_id, "❌ این شبیه کلید API نیست. دوباره بفرست:")
            return
        s["router_key"] = text
        s["step"] = "building_hermes"
        threading.Thread(target=phase2_hermes, args=(chat_id, dict(s)), daemon=True).start()
        return
    if step in ("building_router", "building_hermes"):
        send(chat_id, "⏳ صبر کن، دارم کار می‌کنم…")
        return


def poll():
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = msg.get("chat") or {}
                t = msg.get("text") or ""
                if not t:
                    continue
                try:
                    mid = msg.get("message_id")
                    if mid and any(k in t for k in ["zcli", "eyJ", ":"]) and len(t) > 30:
                        tg("deleteMessage", {"chat_id": chat.get("id"), "message_id": mid})
                except Exception:
                    pass
                handle(chat.get("id"), t)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env missing")
    print("installer bot polling…", flush=True)
    poll()
