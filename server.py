import os, re, time, json, threading, html as ihtml, requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = "https://api.app-prg1.zerops.io/api/rest/public"

UA = {"User-Agent": "Mozilla/5.0"}
sessions = {}  # chat_id -> {step, zerops_token, tg_token, router_key, project_name}
STATE_FILE = os.path.join(os.path.dirname(__file__), ".sessions.json")


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


def build_import_yaml(project, tg_token, chat_id, router_key):
    # One ubuntu container: installs hermes + 9router, runs both.
    # Secrets go through dotEnvSecrets so they never live in git.
    dotenv = (
        f"NEW_TG_TOKEN={tg_token}\n"
        f"NEW_TG_CHAT={chat_id}\n"
        f"ROUTER_KEY={router_key}\n"
    )
    zerops_yml = r"""
zerops:
  - setup: app
    run:
      base: ubuntu@24.04
      ports:
        - port: 20128
          httpSupport: true
      start: bash /var/www/bootstrap.sh
""".strip()
    # indent zeropsYaml block under service (6 spaces for keys, content literal)
    ind = "\n".join("      " + l for l in zerops_yml.split("\n"))
    dg = "\n".join("      " + l for l in dotenv.strip().split("\n"))
    return f"""project:
  name: {project}
  description: "hermes + 9router auto install"
  corePackage: LIGHT
services:
  - hostname: app
    type: nodejs@22
    enableSubdomainAccess: true
    minContainers: 1
    maxContainers: 1
    # 1GB RAM: npm install 9router OOMs on the 128MB default
    minRam: 1
    maxRam: 1
    minCpu: 1
    maxCpu: 2
    buildFromGit: https://github.com/mmdgk123/hermes-zerops-template
    dotEnvSecrets: |
{dg}
"""


BOOTSTRAP_SH = r"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
export HERMES_HOME=/home/zerops/.hermes
# node for 9router
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g 9router || true
# hermes
if ! command -v hermes >/dev/null 2>&1; then
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --skip-browser --skip-computer-use || true
fi
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$HERMES_HOME"
# .env for the new hermes
ENVF="$HERMES_HOME/.env"
touch "$ENVF"
set_kv() { grep -q "^$1=" "$ENVF" 2>/dev/null && sed -i "s|^$1=.*|$1=$2|" "$ENVF" || echo "$1=$2" >> "$ENVF"; }
set_kv TELEGRAM_BOT_TOKEN "$NEW_TG_TOKEN"
set_kv TELEGRAM_HOME_CHANNEL "$NEW_TG_CHAT"
[ -n "$ROUTER_KEY" ] && set_kv CUSTOM_API_KEY "$ROUTER_KEY"
chmod 600 "$ENVF" || true
# point hermes model at local 9router
hermes config set model.provider custom 2>/dev/null || true
hermes config set model.base_url "http://127.0.0.1:20128/v1" 2>/dev/null || true
# start 9router in background
export PORT=20128 HOSTNAME=0.0.0.0 DATA_DIR=/home/zerops/.9router \
  NEXT_PUBLIC_BASE_URL="http://127.0.0.1:20128" INITIAL_PASSWORD=123456
nohup 9router --no-browser --port 20128 > /home/zerops/9router.log 2>&1 &
# start hermes gateway
nohup hermes gateway run > /home/zerops/gateway.log 2>&1 &
echo "bootstrap done, waiting..."
wait
"""


def do_install(chat_id, s):
    zt = s["zerops_token"]
    send(chat_id, "⏳ توکن معتبره، دارم پروژه می‌سازم…")
    # 1. validate
    code, me = zapi(zt, "get", "/user/info")
    if code != 200:
        send(chat_id, f"❌ توکن Zerops قبول نشد (HTTP {code}). دوباره با /start شروع کن.")
        sessions.pop(chat_id, None)
        return
    # 2. client id (client-list returns clientUser entries; real client id is in .client.id)
    code, cl = zapi(zt, "get", "/user/client-list")
    client_id = None
    try:
        items = cl if isinstance(cl, list) else cl.get("list") or cl.get("items") or cl.get("clients") or []
        if items and isinstance(items, list):
            first = items[0]
            client_id = (first.get("client") or {}).get("id") or first.get("clientId")
    except Exception:
        pass
    if not client_id:
        send(chat_id, "❌ نتونستم client-id رو پیدا کنم. پاسخ API:\n<code>" +
             ihtml.escape(json.dumps(cl)[:800]) + "</code>")
        sessions.pop(chat_id, None)
        return
    # 3. import project
    pname = s.get("project_name", "hermes-auto")
    yaml_text = build_import_yaml(pname, s["tg_token"], chat_id, s.get("router_key", ""))
    code, res = zapi(zt, "post", f"/client/{client_id}/project/import",
                     {"yaml": yaml_text}, timeout=120)
    if code != 200:
        send(chat_id, f"❌ ساخت پروژه ناموفق بود (HTTP {code}):\n<code>" +
             ihtml.escape(json.dumps(res)[:1500]) + "</code>")
        sessions.pop(chat_id, None)
        return
    pid = res.get("projectId", "?")
    # find the app service id and inject the extra envs the template needs
    try:
        _, stacks = zapi(zt, "get", f"/project/{pid}/service-stack", timeout=30)
        for st in (stacks.get("list", []) if isinstance(stacks, dict) else []):
            if st.get("isSystem"):
                continue
            sid = st.get("id")
            zapi(zt, "post", f"/service-stack/{sid}/user-data",
                 {"key": "OPENCODE_ZEN_API_KEY",
                  "content": "oc_sk_f70267f06abb_w_xXuLT4OJn3Fvxo6jwLLtf9at5-MC2i",
                  "sensitive": True}, timeout=30)
            zapi(zt, "post", f"/service-stack/{sid}/user-data",
                 {"key": "TELEGRAM_ALLOWED_USERS",
                  "content": str(chat_id),
                  "sensitive": False}, timeout=30)
            break
    except Exception as e:
        print("post-create env inject failed:", e, flush=True)
    send(chat_id, f"✅ پروژه ساخته شد!\n🆔 <code>{ihtml.escape(str(pid))}</code>\n\n🔗 لینک پروژه:\nhttps://app.zerops.io/project/{pid}\n\n⏳ سرویس app داره از روی تمپلیت بیلد می‌گیره (~۵ دقیقه). کاری لازم نیست بکنی — وقتی بالا اومد خبرت می‌کنم.")
    sessions.pop(chat_id, None)
    threading.Thread(target=watch_deploy, args=(chat_id, zt, pid), daemon=True).start()


def watch_deploy(chat_id, token, pid, tries=40):
    """Poll service status; notify when running or failed."""
    import time as _t
    sid = None
    for _ in range(tries):
        _t.sleep(60)
        try:
            _, data = zapi(token, "get", f"/project/{pid}/service-stack", timeout=30)
            items = data.get("list", []) if isinstance(data, dict) else []
            for s in items:
                if s.get("isSystem"):
                    continue
                sid = s.get("id")
                st = s.get("status", "?")
                if st in ("READY", "RUNNING", "OK", "ACTIVE") or "RUN" in st.upper():
                    send(chat_id, f"🎉 هرمس بالا اومد!\nلینک سرویس:\nhttps://app.zerops.io/project/{pid}\n\nتوکن بات هرمست رو تو تلگرام باز کن و /start بزن.")
                    return
                if "FAIL" in st.upper() or "ERROR" in st.upper():
                    send(chat_id, f"❌ دیپلوی fail شد (وضعیت: {st}). لاگ رو تو داشبورد ببین یا بگو برات چک کنم.")
                    return
        except Exception:
            pass
    send(chat_id, "⏰ هنوز بالا نیومده بعد ~۴۰ دقیقه. وضعیت رو تو داشبورد چک کن:\nhttps://app.zerops.io/project/" + str(pid))


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
        # verify bot token quickly
        try:
            r = requests.get(f"https://api.telegram.org/bot{text}/getMe", timeout=15).json()
            if not r.get("ok"):
                send(chat_id, "❌ این توکن بات معتبر نیست. دوباره بفرست:")
                return
            s["bot_username"] = r.get("result", {}).get("username", "?")
        except Exception:
            pass
        s["tg_token"] = text
        s["step"] = "router"
        send(chat_id, f"✅ بات @{s.get('bot_username', '?')} تایید شد.\n\nکلید 9router رو بفرست (از داشبورد 9router بخش API keys). اگه نداری بنویس <code>skip</code>:")
        return
    if step == "router":
        if text.lower() != "skip":
            s["router_key"] = text
        else:
            s["router_key"] = ""
        s["step"] = "name"
        send(chat_id, "اسم پروژه رو بفرست (فقط حروف کوچیک انگلیسی، مثل hermes2):")
        return
    if step == "name":
        name = re.sub(r"[^a-z0-9-]", "", text.lower())[:25] or "hermes-auto"
        s["project_name"] = name
        threading.Thread(target=do_install, args=(chat_id, dict(s)), daemon=True).start()
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
                # delete token messages for hygiene
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
