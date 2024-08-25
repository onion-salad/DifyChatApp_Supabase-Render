import streamlit as st
import requests
import pandas as pd
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import time
from streamlit.components.v1 import html
import stripe

load_dotenv()

# Supabase設定
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# FastAPI backend URL
BACKEND_URL = os.getenv("BACKEND_URL")

# Stripe Publishable Key
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def init_session_state():
    if 'page' not in st.session_state:
        st.session_state.page = 'login'
    if 'user' not in st.session_state:
        st.session_state.user = None
    if 'access_token' not in st.session_state:
        st.session_state.access_token = None
    if 'refresh_token' not in st.session_state:
        st.session_state.refresh_token = None
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

def refresh_access_token():
    try:
        res = requests.post(f"{BACKEND_URL}/refresh_token", json={"refresh_token": st.session_state.refresh_token})
        res.raise_for_status()
        data = res.json()
        st.session_state.access_token = data['access_token']
        st.session_state.refresh_token = data['refresh_token']
    except requests.exceptions.RequestException as e:
        st.error(f"Error refreshing token: {str(e)}")
        if hasattr(e.response, 'text'):
            st.error(f"Server response: {e.response.text}")
        st.session_state.page = 'login'

def login_page():
    st.title("Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        try:
            res = requests.post(f"{BACKEND_URL}/token", json={"email": email, "password": password})
            res.raise_for_status()
            data = res.json()
            st.session_state.access_token = data['access_token']
            st.session_state.refresh_token = data['refresh_token']
            st.session_state.page = 'chat'
            st.success("Login successful!")
        except requests.exceptions.RequestException as e:
            st.error(f"Error during login: {str(e)}")
            if hasattr(e.response, 'text'):
                st.error(f"Server response: {e.response.text}")

    if st.button("Go to Register"):
        st.session_state.page = 'register'

def register_page():
    st.title("Register")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Register"):
        try:
            res = supabase.auth.sign_up({"email": email, "password": password})
            if res.user:
                st.success("Registration successful!")
                st.session_state.page = 'login'
            else:
                st.error("Registration failed. Please try again.")
        except Exception as e:
            st.error(f"Error during registration: {str(e)}")

    if st.button("Back to Login"):
        st.session_state.page = 'login'

def get_chat_history(headers: dict):
    try:
        chat_history_response = requests.get(f"{BACKEND_URL}/chat_history", headers=headers)
        chat_history_response.raise_for_status()
        return chat_history_response.json()["chat_history"]
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching chat history: {str(e)}")
        if hasattr(e.response, 'text'):
            st.error(f"Server response: {e.response.text}")
        return None
    except Exception as e:
        st.error(f"An unexpected error occurred: {str(e)}")
        return None

def chat_page():
    st.title("Chat")
    if not st.session_state.access_token:
        st.error("Please login first")
        st.session_state.page = 'login'
        return

    # トークンの有効期限をチェックし、必要に応じて更新
    if st.session_state.session.expires_at < time.time():
        refresh_access_token()

    headers = {
        "Authorization": f"Bearer {st.session_state.access_token}"
    }

    # Stripe 購入ボタン、チャット履歴エクスポートボタン、残チャット回数を表示
    st.sidebar.title("Menu")

    # 常に Stripe 購入ボタンを表示
    st.sidebar.warning("Please purchase access to start chatting.")
    show_stripe_purchase_button()

    # ユーザー情報を取得
    user_profile = supabase.table('user_profiles').select('*').eq('user_id', st.session_state.user.id).single().execute().data
    if not user_profile:
        st.error("User profile not found.")
        return

    if user_profile.get('is_paid', False):
        st.sidebar.success("Payment confirmed!")
        remaining_chats = 50 - user_profile.get('chat_count', 0)
        st.sidebar.write(f"Remaining chats: {remaining_chats}")

        if st.sidebar.button("Export Chat History"):
            chat_history = get_chat_history(headers)
            if chat_history is not None:
                df = pd.DataFrame(chat_history)
                csv = df.to_csv(index=False)
                st.sidebar.download_button(
                    label="Download Chat History",
                    data=csv,
                    file_name="chat_history.csv",
                    mime="text/csv"
                )

    # チャット履歴を表示
    if not st.session_state.chat_history:
        st.session_state.chat_history.append({
            "role": "DIVINEチャット",
            "content": "こんにちは！美容クリニック「DIVINE」にお問い合わせいただき、ありがとうございます。何かお手伝いできることがあれば、お気軽にお知らせください。"
        })
    for message in st.session_state.chat_history:
        if message['role'] == 'user':
            st.text_input("You:", message['content'], disabled=True)
        else:
            st.markdown("**Assistant:**")
            st.markdown(message['content'])

    # チャット入力と送信
    if user_profile.get('is_paid', False):
        message = st.text_area("Enter your message:", height=100)
        if st.button("Send") and message:
            try:
                response = requests.post(
                    f"{BACKEND_URL}/chat",
                    json={
                        "user_id": st.session_state.user.id,
                        "message": message
                    },
                    headers=headers
                )
                response.raise_for_status()
                response_data = response.json()
                if "response" in response_data:
                    assistant_response = response_data["response"]
                    st.session_state.chat_history.append({"role": "user", "content": message})
                    st.session_state.chat_history.append({"role": "assistant", "content": assistant_response})
                    
                    supabase.table('user_profiles').update({'chat_count': user_profile['chat_count'] + 1}).eq('user_id', st.session_state.user.id).execute()
                    st.rerun()
                else:
                    st.error("Unexpected response format from server")
            except requests.exceptions.RequestException as e:
                st.error(f"Error sending message: {str(e)}")
                if hasattr(e.response, 'text'):
                    st.error(f"Server response: {e.response.text}")
    else:
        st.write("Please purchase access to start chatting.")

    if st.sidebar.button("Logout"):
        st.session_state.user = None
        st.session_state.access_token = None
        st.session_state.refresh_token = None
        st.session_state.chat_history = []
        st.session_state.page = 'login'

def main():
    init_session_state()

    pages = {
        'login': login_page,
        'register': register_page,
        'chat': chat_page
    }

    pages[st.session_state.page]()

if __name__ == "__main__":
    main()
