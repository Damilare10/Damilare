from flask import Flask, request, redirect, session
import os
import requests
import secrets
import base64
import hashlib
import sqlite3
from dotenv import load_dotenv
from telegram import Bot


load_dotenv()
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Twitter OAuth 2.0 Credentials
CLIENT_ID = os.getenv("TWITTER_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET")
AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
SCOPE = "tweet.read tweet.write users.read offline.access like.write"
CALLBACK_URL = "https://telegram-bot-production-d526.up.railway.app/twitter/callback"
API_KEY = os.getenv("TELEGRAM_TOKEN")


def generate_code_verifier_challenge():
    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)).rstrip(b'=').decode('utf-8')
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode('utf-8')
    return verifier, challenge


def save_tokens(telegram_id, handle, twitter_id, access_token, refresh_token):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM users WHERE telegram_id = ?",
                (str(telegram_id),))
    exists = cur.fetchone()

    if exists:
        cur.execute("""
            UPDATE users SET twitter_handle = ?, twitter_id = ?, access_token = ?, refresh_token = ?, last_updated = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
        """, (handle, twitter_id, access_token, refresh_token, str(telegram_id)))
    else:
        cur.execute("""
            INSERT INTO users (telegram_id, twitter_handle, twitter_id, access_token, refresh_token)
            VALUES (?, ?, ?, ?, ?)
        """, (str(telegram_id), handle, twitter_id, access_token, refresh_token))

    conn.commit()
    conn.close()


@app.route('/twitter/connect')
def connect():
    telegram_id = request.args.get("telegram_id")
    if not telegram_id:
        return "Missing telegram_id", 400

    code_verifier, code_challenge = generate_code_verifier_challenge()
    session['code_verifier'] = code_verifier
    session['telegram_id'] = telegram_id

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "scope": SCOPE,
        "state": secrets.token_urlsafe(16),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    auth_url = f"{AUTH_URL}?{'&'.join([f'{k}={requests.utils.quote(v)}' for k,
                                      v in params.items()])}"
    return redirect(auth_url)


@app.route('/twitter/callback')
def callback():
    code = request.args.get("code")
    if not code or "code_verifier" not in session or "telegram_id" not in session:
        return "Missing required session or code", 400

    code_verifier = session["code_verifier"]
    telegram_id = session["telegram_id"]

    # Basic Auth
    basic_auth = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URL,
        "code_verifier": code_verifier
    }

    try:
        token_res = requests.post(TOKEN_URL, headers=headers, data=data)
        token_json = token_res.json()
        access_token = token_json["access_token"]
        refresh_token = token_json.get("refresh_token")

        # ✅ Fetch user info
        user_res = requests.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_res.json().get("data", {})
        twitter_id = user_data.get("id")
        twitter_handle = user_data.get("username")

        if not twitter_handle:
            return "❌ Failed to retrieve user info", 500

        # ✅ Save to DB
        save_tokens(telegram_id, twitter_handle, twitter_id,
                    access_token, refresh_token)

        try:
            message = f"✅ Your Twitter account (@{twitter_handle}) has been connected successfully!"
            telegram_api_url = f"https://api.telegram.org/bot{API_KEY}/sendMessage"
            payload = {
                "chat_id": telegram_id,
                "text": message
            }
            requests.post(telegram_api_url, data=payload)
        except Exception as e:
            print(f"❌ Failed to send Telegram message: {e}")

        return "✅ Twitter connected successfully, you can close this page!"

    except Exception as e:
        print("❌ Token error:", e)
        return "❌ Failed to connect Twitter", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
