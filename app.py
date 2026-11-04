import asyncio
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string
from telethon import TelegramClient, functions, types
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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DRK Telegram Client</title>
<style>
body{font-family:system-ui;background:#101b26;color:#eef;max-width:980px;margin:auto;padding:12px}input,button,select{font-size:15px;padding:9px;margin:3px;border-radius:8px;border:1px solid #536b7d}input,select{background:#1c2b38;color:#fff}button{background:#2aabee;color:#fff;border:0;cursor:pointer}.card{background:#192b3a;padding:13px;border-radius:12px;margin:10px 0}.row{display:flex;gap:6px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.wide{width:100%;box-sizing:border-box}#dialogs button{display:block;width:100%;text-align:left;background:#243e52;margin:4px 0}.msg{padding:8px;margin:5px;border-radius:9px;background:#263d50}.out{background:#245b66;margin-left:15%}#messages{min-height:180px;max-height:420px;overflow:auto}.tools{border-top:1px solid #385064;margin-top:10px;padding-top:10px}small{color:#aab8c2}.error{color:#ff9b9b}.ok{color:#83e6a2}.result{white-space:pre-wrap;background:#12202c;padding:8px;border-radius:8px}
</style></head><body>
<h1>DRK Telegram Client</h1>
<div class="card"><h3>1. OTP + 2FA Login</h3><div class="row"><input id="phone" placeholder="+8801XXXXXXXXX"><button onclick="sendCode()">Send OTP</button><input id="code" placeholder="OTP"><button onclick="verifyCode()">Verify OTP</button><input id="password" type="password" placeholder="2FA password"><button onclick="verify2FA()">Verify 2FA</button></div><div id="loginStatus"></div></div>
<div class="card"><h3>2. Chats & Search</h3><div class="row"><button onclick="loadDialogs()">Load chats</button><input id="search" placeholder="Search messages"><button onclick="searchMessages()">Search</button></div><div id="dialogs"></div><div id="searchResults"></div></div>
<div class="card"><h3>3. Chat</h3><div id="selected"><small>Select a chat.</small></div><div id="messages"></div><div class="row"><input id="text" class="wide" placeholder="Message"><button onclick="sendMessage()">Send</button></div><div class="row"><input id="media" type="file"><input id="caption" placeholder="Caption"><button onclick="sendMedia()">Send file</button></div><div class="tools"><div class="row"><input id="actionId" placeholder="Message ID"><input id="actionText" placeholder="Reply/edit text"><input id="forwardPeer" placeholder="Forward to peer"><select id="action"><option>reply</option><option>forward</option><option>edit</option><option>delete</option><option>pin</option><option>react</option></select><input id="emoji" value="👍" placeholder="Emoji"><button onclick="messageAction()">Run action</button></div></div></div>
<div class="card"><h3>4. Create Group / Channel</h3><div class="grid"><input id="groupTitle" placeholder="Group title"><input id="groupUsers" placeholder="User IDs/usernames, comma separated"><button onclick="createGroup()">Create group</button><input id="channelTitle" placeholder="Channel title"><input id="channelAbout" placeholder="About"><select id="channelType"><option value="false">Broadcast channel</option><option value="true">Megagroup</option></select><button onclick="createChannel()">Create channel</button></div></div>
<div class="card"><h3>5. Admin, Invite, Contacts & BotFather</h3><div class="grid"><input id="adminUser" placeholder="User ID/username"><select id="adminAction"><option>promote</option><option>demote</option><option>ban</option><option>unban</option></select><button onclick="adminAction()">Admin action</button><button onclick="inviteLink()">Create invite link for selected chat</button><button onclick="loadContacts()">Load contacts</button><button onclick="botfather()">Open BotFather /newbot</button></div><div id="featureResult" class="result"></div></div>
<script>
let peer=null;const $=id=>document.getElementById(id);
async function api(url,opt={}){let r=await fetch(url,{...opt,headers:{...(opt.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(opt.headers||{})}});let j=await r.json();if(!j.ok)throw Error(j.error||'Request failed');return j}
function show(x,where='loginStatus'){ $(where).innerHTML='<small>'+x+'</small>' }
async function sendCode(){try{let j=await api('/api/login/send-code',{method:'POST',body:JSON.stringify({phone:$('phone').value})});show(j.message)}catch(e){show('<span class=error>'+esc(e.message)+'</span>')}}
async function verifyCode(){try{let j=await api('/api/login/verify',{method:'POST',body:JSON.stringify({code:$('code').value})});show(j.two_fa?'OTP accepted. Enter 2FA password.':'Logged in.');if(j.authorized)loadDialogs()}catch(e){show('<span class=error>'+esc(e.message)+'</span>')}}
async function verify2FA(){try{await api('/api/login/2fa',{method:'POST',body:JSON.stringify({password:$('password').value})});show('<span class=ok>2FA login successful.</span>');loadDialogs()}catch(e){show('<span class=error>'+esc(e.message)+'</span>')}}
async function loadDialogs(){try{let j=await api('/api/dialogs');$('dialogs').innerHTML=j.dialogs.map(d=>`<button onclick="openChat(${d.id},${JSON.stringify(d.name)})">${esc(d.name)} <small>(${d.unread_count})</small></button>`).join('')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','dialogs')}}
async function openChat(id,name){peer=id;$('selected').textContent=name;try{let j=await api('/api/messages?peer='+id);$('messages').innerHTML=j.messages.map(m=>`<div class="msg ${m.out?'out':''}"><small>${esc(m.sender)} · ${esc(m.date||'')} · #${m.id}</small><br>${esc(m.text)}</div>`).join('');$('messages').scrollTop=$('messages').scrollHeight}catch(e){$('messages').textContent=e.message}}
async function sendMessage(){if(!peer)return alert('Select a chat first');let text=$('text').value.trim();if(!text)return;try{await api('/api/messages/send',{method:'POST',body:JSON.stringify({peer,text})});$('text').value='';openChat(peer,'Chat')}catch(e){alert(e.message)}}
async function sendMedia(){if(!peer)return alert('Select a chat first');let f=$('media').files[0];if(!f)return alert('Select a file');let form=new FormData();form.append('peer',peer);form.append('caption',$('caption').value);form.append('file',f);try{await api('/api/media/send',{method:'POST',body:form});show('<span class=ok>File sent.</span>','featureResult');openChat(peer,'Chat')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function messageAction(){if(!peer)return alert('Select a chat first');let body={peer,message_id:Number($('actionId').value),action:$('action').value,text:$('actionText').value,emoji:$('emoji').value,to_peer:$('forwardPeer').value};try{await api('/api/messages/action',{method:'POST',body:JSON.stringify(body)});show('<span class=ok>Action completed.</span>','featureResult');openChat(peer,'Chat')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function createGroup(){let users=$('groupUsers').value.split(',').map(x=>x.trim()).filter(Boolean);try{let j=await api('/api/groups/create',{method:'POST',body:JSON.stringify({title:$('groupTitle').value,users})});show('Group created: '+j.id,'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function createChannel(){try{let j=await api('/api/channels/create',{method:'POST',body:JSON.stringify({title:$('channelTitle').value,about:$('channelAbout').value,megagroup:$('channelType').value==='true'})});show('Channel created: '+j.id,'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function searchMessages(){try{let j=await api('/api/search?q='+encodeURIComponent($('search').value));$('searchResults').innerHTML=j.results.map(x=>`<div class=result>#${x.id} ${esc(x.text)} <small>${esc(x.date||'')}</small></div>`).join('')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','searchResults')}}
async function adminAction(){if(!peer)return alert('Select a group/channel first');try{let j=await api('/api/admin/action',{method:'POST',body:JSON.stringify({peer,user:$('adminUser').value,action:$('adminAction').value})});show('Admin action completed: '+j.action,'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function inviteLink(){if(!peer)return alert('Select a group/channel first');try{let j=await api('/api/invite-link',{method:'POST',body:JSON.stringify({peer})});show('Invite link: '+esc(j.link),'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function loadContacts(){try{let j=await api('/api/contacts');show(j.contacts.map(x=>x.name+' '+(x.username?'@'+x.username:'')).join('\n'),'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
async function botfather(){try{let j=await api('/api/botfather/start',{method:'POST'});show('BotFather opened. Continue manually. '+j.note,'featureResult')}catch(e){show('<span class=error>'+esc(e.message)+'</span>','featureResult')}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script></body></html>'''


@app.post("/api/groups/create")
def create_group():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    users = data.get("users", [])
    if not title or not isinstance(users, list) or not users:
        return jsonify({"ok": False, "error": "title and at least one user are required"}), 400

    async def task():
        await ensure_connected()
        return await client(functions.messages.CreateChatRequest(users=users, title=title))

    try:
        result = run(task())
        return jsonify({"ok": True, "id": getattr(result.chats[0], "id", None)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/channels/create")
def create_channel():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    about = str(data.get("about", "")).strip()
    megagroup = bool(data.get("megagroup", False))
    if not title:
        return jsonify({"ok": False, "error": "title is required"}), 400

    async def task():
        await ensure_connected()
        return await client(functions.channels.CreateChannelRequest(
            title=title, about=about, megagroup=megagroup, broadcast=not megagroup
        ))

    try:
        result = run(task())
        return jsonify({"ok": True, "id": getattr(result.chats[0], "id", None)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/search")
def search_messages():
    query = str(request.args.get("q", "")).strip()
    limit = min(max(int(request.args.get("limit", "30")), 1), 100)

    async def task():
        await ensure_connected()
        result = []
        async for message in client.iter_messages(None, search=query, limit=limit):
            result.append({"id": message.id, "peer_id": getattr(message.peer_id, "channel_id", None) or getattr(message.peer_id, "user_id", None), "text": message.message or "", "date": message.date.isoformat() if message.date else None})
        return result

    try:
        return jsonify({"ok": True, "results": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/messages/action")
def message_action():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).lower()
    peer = data.get("peer")
    message_id = int(data.get("message_id", 0))
    if not peer or not message_id:
        return jsonify({"ok": False, "error": "peer and message_id are required"}), 400

    async def task():
        await ensure_connected()
        if action == "edit":
            return await client.edit_message(peer, message_id, str(data.get("text", "")))
        if action == "delete":
            return await client.delete_messages(peer, [message_id], revoke=True)
        if action == "pin":
            return await client.pin_message(peer, message_id, notify=bool(data.get("notify", False)))
        if action == "reply":
            return await client.send_message(peer, str(data.get("text", "")), reply_to=message_id)
        if action == "forward":
            return await client.forward_messages(data.get("to_peer"), message_id, from_peer=peer)
        if action == "react":
            emoji = str(data.get("emoji", "👍"))
            return await client(functions.messages.SendReactionRequest(peer=peer, msg_id=message_id, reaction=[types.ReactionEmoji(emoticon=emoji)]))
        raise ValueError("Unsupported action")

    try:
        run(task())
        return jsonify({"ok": True, "action": action})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/media/send")
def send_media():
    peer = request.form.get("peer")
    caption = request.form.get("caption", "")
    upload = request.files.get("file")
    if not peer or not upload:
        return jsonify({"ok": False, "error": "peer and file are required"}), 400
    temp_path = Path("/tmp") / f"tg_upload_{os.getpid()}_{upload.filename}"
    upload.save(temp_path)

    async def task():
        await ensure_connected()
        return await client.send_file(peer, str(temp_path), caption=caption)

    try:
        run(task())
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/invite-link")
def invite_link():
    data = request.get_json(silent=True) or {}
    peer = data.get("peer")

    async def task():
        await ensure_connected()
        result = await client(functions.messages.ExportChatInviteRequest(peer=peer))
        return getattr(result, "link", str(result))

    try:
        return jsonify({"ok": True, "link": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/admin/action")
def admin_action():
    data = request.get_json(silent=True) or {}
    peer = data.get("peer")
    user = data.get("user")
    action = str(data.get("action", "")).lower()
    if not peer or not user or action not in {"promote", "demote", "ban", "unban"}:
        return jsonify({"ok": False, "error": "peer, user and action are required"}), 400

    async def task():
        await ensure_connected()
        if action in {"promote", "demote"}:
            rights = types.ChatAdminRights(
                change_info=action == "promote", post_messages=action == "promote",
                edit_messages=action == "promote", delete_messages=action == "promote",
                ban_users=action == "promote", invite_users=action == "promote",
                pin_messages=action == "promote", add_admins=False,
            )
            return await client(functions.channels.EditAdminRequest(
                channel=peer, user_id=user, admin_rights=rights,
                rank=str(data.get("rank", "admin")) if action == "promote" else "",
            ))
        banned = action == "ban"
        rights = types.ChatBannedRights(
            until_date=None, view_messages=banned, send_messages=banned,
            send_media=banned, send_stickers=banned, send_gifs=banned,
            send_games=banned, send_inline=banned, embed_links=banned,
            send_polls=banned, change_info=False, invite_users=False,
            pin_messages=False,
        )
        return await client(functions.channels.EditBannedRequest(channel=peer, participant=user, banned_rights=rights))

    try:
        run(task())
        return jsonify({"ok": True, "action": action})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/contacts")
def contacts():
    async def task():
        await ensure_connected()
        result = await client(functions.contacts.GetContactsRequest(hash=0))
        return [{"id": u.id, "name": " ".join(filter(None, [u.first_name, u.last_name])), "username": u.username} for u in result.users]

    try:
        return jsonify({"ok": True, "contacts": run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/botfather/start")
def botfather_start():
    async def task():
        await ensure_connected()
        message = await client.send_message("@BotFather", "/newbot")
        return {"id": message.id, "note": "Continue the BotFather conversation manually; never expose the bot token in logs or GitHub."}

    try:
        return jsonify({"ok": True, **run(task())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Open http://{host}:{port} in the browser.")
    app.run(host=host, port=port, debug=False, threaded=True)
