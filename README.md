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

## Scope

এটি foundation build: OTP/2FA login, dialogs, text messages। Telegram X-এর মতো UI বানালেও Telegram-এর backend features API integration ছাড়া কাজ করবে না। Media, file upload/download, contacts, groups, search, reactions, pinned messages, notifications, drafts এবং settings পরবর্তী আলাদা modules হিসেবে যোগ করতে হবে।
