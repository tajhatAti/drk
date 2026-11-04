#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")"

command -v python >/dev/null 2>&1 || {
  echo "Python পাওয়া যায়নি। আগে চালান: pkg update -y && pkg install python -y"
  exit 1
}

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ -z "${TG_API_ID:-}" ]; then
  read -r -p "Telegram API ID (my.telegram.org থেকে): " TG_API_ID
fi
if [ -z "${TG_API_HASH:-}" ]; then
  read -r -p "Telegram API Hash: " TG_API_HASH
fi

export TG_API_ID TG_API_HASH

echo
python app.py
