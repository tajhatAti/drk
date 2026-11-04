import asyncio
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

# Set these in Termux before starting:
# export TG_API_ID="123456"
# export TG_API_HASH="your_api_hash"
API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
SESSION_PATH = os.environ.get("TG_SESSION", str(Path.home() / ".telegram_local_client" / "telegram"))
Path(SESSION_PATH).parent.mkdir(parents=True, exist_ok=True)

if not API_ID or not API_HASH:
    raise SystemExit(
        "Missing TG_API_ID/TG_API_HASH. Get them from https://my.telegram.org and export them first."
    )

app = Flask(__name__)
loop = asyncio.new_event_loop()
client = TelegramClient(SESSION_PATH, API_ID, API_HASH, loop=loop)
phone_code_hash = None
current_phone = None
state_lock = threading.Lock()


def loop_runner():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=loop_runner, daemon=True).start()


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=90)


async def ensure_connected():
    if not client.is_connected():
        await client.connect()


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/status")
def status():
    try:
        connected = run(client.is_user_authorized())
        return jsonify({"ok": True, "authorized": connected})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/login/send-code")
def send_code():
    global phone_code_hash, current_phone
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()
    if not phone.startswith("+"):
        return jsonify({"ok": False, "error": "Use international format, e.g. +8801XXXXXXXXX"}), 400
    try:
        async def task():
            await ensure_connected()
            return await client.send_code_request(phone)

        sent = run(task())
        phone_code_hash = sent.phone_code_hash
        current_phone = phone
        return jsonify({"ok": True, "message": "OTP sent. Check Telegram/SMS."})
    except PhoneNumberInvalidError:
        return jsonify({"ok": False, "error": "Invalid phone number."}), 400
    except ApiIdInvalidError:
        return jsonify({"ok": False, "error": "Invalid TG_API_ID or TG_API_HASH."}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/login/verify")
def verify_code():
    global phone_code_hash
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip().replace(" ", "")
    if not current_phone or not phone_code_hash:
        return jsonify({"ok": False, "error": "Request an OTP first."}), 400
    if not code:
        return jsonify({"ok": False, "error": "OTP is required."}), 400

    async def task():
        await ensure_connected()
        try:
            await client.sign_in(current_phone, code=code, phone_code_hash=phone_code_hash)
            return {"authorized": True, "two_fa": False}
        except SessionPasswordNeededError:
            return {"authorized": False, "two_fa": True}

    try:
        result = run(task())
        return jsonify({"ok": True, **result})
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/login/2fa")
def verify_2fa():
    data = request.get_json(silent=True) or {}
    password = str(data.get("password", ""))
    if not password:
        return jsonify({"ok": False, "error": "2FA password is required."}), 400

    async def task():
        await ensure_connected()
        await client.sign_in(password=password)
        return await client.is_user_authorized()

    try:
        return jsonify({"ok": True, "authorized": bool(run(task()))})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/dialogs")
def dialogs():
    async def task():
        await ensure_connected()
        if not await client.is_user_authorized():
            raise RuntimeError("Not logged in")
        result = []
        async for dialog in client.iter_dialogs(limit=100):
            entity = dialog.entity
            result.append({
                "id": dialog.id,
                "name": dialog.name or "Unknown",
                "unread_count": dialog.unread_count,
                "is_group": bool(getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False)),
            })
        return result

    try:
        return jsonify({"ok": True, "dialogs": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401


@app.get("/api/messages")
def messages():
    try:
        peer = int(request.args.get("peer", "0"))
        limit = min(max(int(request.args.get("limit", "30")), 1), 100)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid peer or limit."}), 400

    async def task():
        await ensure_connected()
        if not await client.is_user_authorized():
            raise RuntimeError("Not logged in")
        result = []
        async for message in client.iter_messages(peer, limit=limit):
            sender = await message.get_sender()
            result.append({
                "id": message.id,
                "date": message.date.isoformat() if message.date else None,
                "sender": getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Unknown",
                "text": message.message or "[media/non-text message]",
                "out": bool(message.out),
            })
        return list(reversed(result))

    try:
        return jsonify({"ok": True, "messages": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/messages/send")
def send_message():
    data = request.get_json(silent=True) or {}
    try:
        peer = int(data.get("peer"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid peer."}), 400
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "error": "Message is empty."}), 400
    if len(text) > 4096:
        return jsonify({"ok": False, "error": "Message is too long."}), 400

    async def task():
        await ensure_connected()
        if not await client.is_user_authorized():
            raise RuntimeError("Not logged in")
        message = await client.send_message(peer, text)
        return {"id": message.id, "text": message.message}

    try:
        return jsonify({"ok": True, "message": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Telegram Client</title>
<style>
body{font-family:system-ui;background:#15202b;color:#eef;max-width:900px;margin:auto;padding:18px}
input,button{font-size:16px;padding:10px;margin:4px;border-radius:8px;border:1px solid #506070}
input{background:#202f3d;color:#fff}button{background:#2aabee;color:#fff;border:0;cursor:pointer}
.card{background:#1d2d3b;padding:14px;border-radius:12px;margin:10px 0}.row{display:flex;gap:8px;flex-wrap:wrap}
#dialogs button{display:block;width:100%;text-align:left;background:#263d50;margin:5px 0}.msg{padding:8px;margin:5px;border-radius:9px;background:#263d50}.out{background:#245b66;margin-left:15%}
#messages{min-height:180px;max-height:420px;overflow:auto}
small{color:#aab8c2}.error{color:#ff9b9b}
</style></head>
<body>
<h1>Local Telegram Client</h1>
<div class="card"><h3>1. Login</h3>
<div class="row"><input id="phone" placeholder="+8801XXXXXXXXX"><button onclick="sendCode()">Send OTP</button></div>
<div class="row"><input id="code" placeholder="OTP code"><button onclick="verifyCode()">Verify OTP</button></div>
<div class="row"><input id="password" type="password" placeholder="2FA password"><button onclick="verify2FA()">Verify 2FA</button></div>
<div id="loginStatus"></div></div>
<div class="card"><h3>2. Chats</h3><button onclick="loadDialogs()">Load chats</button><div id="dialogs"></div></div>
<div class="card"><h3>3. Messages</h3><div id="selected"><small>Select a chat.</small></div><div id="messages"></div>
<div class="row"><input id="text" style="flex:1" placeholder="Message"><button onclick="sendMessage()">Send</button></div></div>
<script>
let peer=null;
const $=id=>document.getElementById(id);
async function api(url,opt={}){let r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});let j=await r.json();if(!j.ok)throw Error(j.error||'Request failed');return j}
function show(x){$('loginStatus').innerHTML='<small>'+x+'</small>'}
async function sendCode(){try{let j=await api('/api/login/send-code',{method:'POST',body:JSON.stringify({phone:$('phone').value})});show(j.message)}catch(e){show('<span class=error>'+e.message+'</span>')}}
async function verifyCode(){try{let j=await api('/api/login/verify',{method:'POST',body:JSON.stringify({code:$('code').value})});show(j.two_fa?'OTP accepted. Enter 2FA password.':'Logged in.');if(j.authorized)loadDialogs()}catch(e){show('<span class=error>'+e.message+'</span>')}}
async function verify2FA(){try{let j=await api('/api/login/2fa',{method:'POST',body:JSON.stringify({password:$('password').value})});show('2FA login successful.');loadDialogs()}catch(e){show('<span class=error>'+e.message+'</span>')}}
async function loadDialogs(){try{let j=await api('/api/dialogs');$('dialogs').innerHTML=j.dialogs.map(d=>`<button onclick="openChat(${d.id},${JSON.stringify(d.name)})">${esc(d.name)} <small>(${d.unread_count})</small></button>`).join('')}catch(e){$('dialogs').innerHTML='<span class=error>'+e.message+'</span>'}}
async function openChat(id,name){peer=id;$('selected').textContent=name;try{let j=await api('/api/messages?peer='+id);$('messages').innerHTML=j.messages.map(m=>`<div class="msg ${m.out?'out':''}"><small>${esc(m.sender)} · ${esc(m.date||'')}</small><br>${esc(m.text)}</div>`).join('');$('messages').scrollTop=$('messages').scrollHeight}catch(e){$('messages').textContent=e.message}}
async function sendMessage(){if(!peer)return alert('Select a chat first');let text=$('text').value.trim();if(!text)return;try{await api('/api/messages/send',{method:'POST',body:JSON.stringify({peer,text})});$('text').value='';openChat(peer,'Chat')}catch(e){alert(e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script></body></html>'''


if __name__ == "__main__":
    print("Open http://127.0.0.1:8080 in the same phone/browser.")
    app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
