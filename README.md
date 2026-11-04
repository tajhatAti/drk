# Local Telegram Client for Termux

এটি Telegram-style local client starter। লগইন flow:

1. Phone number
2. Telegram OTP
3. 2FA password, যদি enabled থাকে
4. Chat list, message history এবং text message send

এটি official Telegram API ব্যবহার করে এবং কোনো Hex/Session String import করে না। Telethon authenticated state locally session file-এ রাখে, যাতে প্রতি বার OTP দিতে না হয়। নিজের ফোন/account ছাড়া ব্যবহার করবেন না।

## একবারে চালানোর পদ্ধতি

1. ZIP extract করুন:

```bash
unzip telegram_local_client.zip
cd telegram_local_client
```

2. Script executable করুন এবং চালান:

```bash
chmod +x run.sh
./run.sh
```

Script নিজে `telethon` ও `flask` install করবে এবং API ID/API Hash চাইবে। API credentials নিতে হবে নিজের account দিয়ে:

```text
https://my.telegram.org
```

সেটআপের পর browser-এ খুলুন:

```text
http://127.0.0.1:8080
```

তারপর phone number, OTP এবং 2FA password দিন।

## Manual install

```bash
pkg update -y
pkg install python unzip -y
pip install -r requirements.txt
export TG_API_ID="123456"
export TG_API_HASH="your_api_hash_here"
python app.py
```

## Security

- Server শুধু `127.0.0.1`-এ bind করা আছে; public internet-এ expose করবেন না।
- `telegram.session` file, API hash, OTP এবং 2FA password কাউকে পাঠাবেন না।
- `app.py`-তে `0.0.0.0` ব্যবহার করবেন না।
- Session file delete করলে পরের বার আবার OTP login লাগবে।

## Render deployment

Render Dashboard → **New → Web Service** → `tajhatAti/drk` repository নির্বাচন করুন। Branch হিসেবে `master` নির্বাচন করুন। এই settings দিন:

```text
Environment: Python 3
Build Command: pip install -r requirements.txt
Start Command: gunicorn --bind 0.0.0.0:$PORT app:app
```

Environment Variables-এ নিজের credentials দিন:

```text
TG_API_ID=আপনার_api_id
TG_API_HASH=আপনার_api_hash
```

Render নিজে `PORT` সেট করবে। Code সেটি ব্যবহার করে public host-এ bind করবে। Free service restart/sleep হলে local session file হারিয়ে যেতে পারে, তাই আবার OTP লাগতে পারে; raw session string browser বা GitHub-এ রাখবেন না। Telegram API/IP restriction bypass করার code এখানে নেই।

## Scope

বর্তমান build-এ OTP/2FA login, dialogs, chat open, message history ও text send-এর সঙ্গে এই API actions যোগ হয়েছে:

- `POST /api/groups/create` — group create
- `POST /api/channels/create` — channel বা megagroup create
- `GET /api/search?q=...` — global message search
- `POST /api/messages/action` — reply, forward, edit, delete, pin, reaction
- `POST /api/media/send` — multipart file/media send
- `POST /api/admin/action` — promote, demote, ban, unban
- `POST /api/invite-link` — invite link export
- `GET /api/contacts` — contacts list
- `POST /api/botfather/start` — BotFather conversation শুরু করে; token manually handle করতে হবে

Telegram X-এর মতো UI বানালেও Telegram backend features API integration ছাড়া কাজ করে না। Destructive actions-এর জন্য নিজের account-এর permission লাগবে। Mass spam, bulk invite, raw session-string import এবং IP/security bypass নেই।
