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
    if 'session' not in st.session_state:
        st.session_state.session = None
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

def show_stripe_purchase_button():
    customer_email = st.session_state.user.email
    customer_data = supabase.table('user_profiles').select('stripe_customer_id').eq('user_id', st.session_state.user.id).single().execute()
    
    if customer_data.data and customer_data.data.get('stripe_customer_id'):
        customer_id = customer_data.data['stripe_customer_id']
    else:
        try:
            customer = stripe.Customer.create(email=customer_email)
            customer_id = customer.id
            supabase.table('user_profiles').update({'stripe_customer_id': customer_id}).eq('user_id', st.session_state.user.id).execute()
        except Exception as e:
            st.error(f"Error creating Stripe customer: {str(e)}")
            return

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price': os.getenv("STRIPE_PRICE_ID"),
                    'quantity': 1,
                },
            ],
            mode='payment',
            success_url=BACKEND_URL + '/payment_success',
            cancel_url=BACKEND_URL + '/payment_cancel',
            customer=customer_id,  # 顧客IDを設定
        )
        
        # セッションIDを使って、Stripe.jsでチェックアウトセッションを開始
        checkout_button = st.sidebar.button("Buy")
        if checkout_button:
            # リンク付きの案内文を表示
            st.write(f'<a href="{checkout_session.url}" target="_blank">Please click here to purchase</a>', unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error creating checkout session: {e}")

def login_page():
    st.title("Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        try:
            res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            if res.user:
                st.session_state.user = res.user
                st.session_state.session = res.session
                st.session_state.page = 'chat'
                st.success("Login successful!")
            else:
                st.error("Login failed. Please check your credentials.")
        except Exception as e:
            st.error(f"Error during login: {str(e)}")

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
                # user_profiles テーブルへのレコード挿入はトリガー関数が行うので不要
                st.success("Registration successful!")
                st.session_state.page = 'login'
            else:
                st.error("Registration failed. Please try again.")
        except Exception as e:
            st.error(f"Error during registration: {str(e)}")

    if st.button("Back to Login"):
        st.session_state.page = 'login'

def get_chat_history(headers: dict):  # headers を引数に追加
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
    if not st.session_state.user or not st.session_state.session:
        st.error("Please login first")
        st.session_state.page = 'login'
        return

    # セッションの有効性をチェックし、必要に応じて更新
    if st.session_state.session.expires_at < time.time():
        try:
            res = supabase.auth.refresh_session(st.session_state.session)
            st.session_state.session = res.session
        except Exception as e:
            st.error(f"Error refreshing session: {str(e)}")
            st.session_state.page = 'login'
            return

    user = st.session_state.user
    headers = {
        "Authorization": f"Bearer {st.session_state.session.access_token}"
    }

    # Stripe 購入ボタン、チャット履歴エクスポートボタン、残チャット回数を表示
    st.sidebar.title("Menu")

    # 常に Stripe 購入ボタンを表示
    st.sidebar.warning("Please purchase access to start chatting.")
    show_stripe_purchase_button()  # メニューバーに表示

    # ユーザー情報を取得
    user_profile = supabase.table('user_profiles').select('*').eq('user_id', user.id).single().execute().data
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
            "role": "assistant",
            "content": "Hello! I can help you validate your hypotheses by gathering evidence from the web. Feel free to enter your hypothesis in any language."
        })
    for message in st.session_state.chat_history:
        if message['role'] == 'user':
            st.text_input("You:", message['content'], disabled=True)
        else:
            st.markdown("**Assistant:**")
            st.markdown(message['content'])

    # チャット入力と送信
    if user_profile.get('is_paid', False):  # 課金済みの場合のみチャット入力欄を表示
        message = st.text_area("Enter your message:", height=100)  # height パラメータを追加
        if st.button("Send") and message:
            try:
                response = requests.post(
                    f"{BACKEND_URL}/chat",
                    json={
                        "user_id": user.id,
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
                    
                    supabase.table('user_profiles').update({'chat_count': user_profile['chat_count'] + 1}).eq('user_id', user.id).execute()
                    st.rerun()  # ここを変更
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
        st.session_state.session = None
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