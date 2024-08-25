from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from supabase import create_client, Client
from dotenv import load_dotenv
import os
import stripe
import requests
from pydantic import BaseModel
import logging
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException
import json
from datetime import datetime, timedelta
from jose import JWTError, jwt

# ログ設定
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

if not all([SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY, FRONTEND_URL]):
    logger.error("Environment variables are not set properly")
    raise ValueError("Environment variables are not set properly")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)  # サービスロールキーを使用

# Stripe設定
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

# Dify設定
DIFY_API_URL = os.getenv("DIFY_API_URL")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")

security = HTTPBearer()

class ChatMessage(BaseModel):
    user_id: str
    message: str

class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: str

class TokenData(BaseModel):
    user_id: str

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        user = supabase.auth.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return user
    except JWTError as e:
        logger.error(f"Error decoding JWT: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

# リクエストボディのモデルを定義
class TokenRequest(BaseModel):
    email: str
    password: str

@app.post("/token")
async def login_for_access_token(request: TokenRequest):
    try:
        email = request.email
        password = request.password
        # Supabaseの認証APIを使用してユーザーを認証
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": res.user.id}, expires_delta=access_token_expires
            )
            refresh_token = res.session.refresh_token
            return {"access_token": access_token, "token_type": "bearer", "refresh_token": refresh_token}
        else:
            raise HTTPException(status_code=400, detail="Login failed")
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during login: {str(e)}")

@app.post("/refresh_token")
async def refresh_access_token(refresh_token: str):
    try:
        new_session = supabase.auth.refresh_session(refresh_token)
        if new_session.user:
            access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": new_session.user.id}, expires_delta=access_token_expires
            )
            return JSONResponse(content={"access_token": access_token, "token_type": "bearer", "refresh_token": new_session.refresh_token})
        else:
            raise HTTPException(status_code=400, detail="Failed to refresh token")
    except Exception as e:
        logger.error(f"Error during token refresh: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during token refresh: {str(e)}")

@app.post("/register")
async def register(email: str, password: str):
    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        if res.user:
            return JSONResponse(content={"message": "Registration successful"})
        else:
            raise HTTPException(status_code=400, detail="Registration failed")
    except Exception as e:
        logger.error(f"Error during registration: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during registration: {str(e)}")

@app.get("/auth/callback")
async def auth_callback(request: Request, response: Response):
    try:
        # Supabase からセッション情報を取得
        session = await supabase.auth.get_session_from_url(str(request.url))

        # セッション情報をクッキーに保存
        response.set_cookie(key="access_token", value=session.access_token, httponly=True)
        response.set_cookie(key="refresh_token", value=session.refresh_token, httponly=True)

        # Streamlit アプリにリダイレクト
        return RedirectResponse(url=FRONTEND_URL)
    except Exception as e:
        logger.error(f"Error in auth_callback: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error handling OAuth callback: {str(e)}")

# その他のエンドポイントはそのまま

def chat_with_dify(message: str, user_id: str) -> dict:
    url = f"{DIFY_API_URL}/chat-messages"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": {},  # もし特定の入力が必要ならここに追加
        "query": message,
        "user": user_id,
        "response_mode": "streaming",  # または "blocking"
        "conversation_id": "",  # ここをNoneではなく空文字列に変更
        "files": []  # 必要ならファイルも追加
    }
    
    logger.debug(f"Sending request to Dify API. URL: {url}")
    logger.debug(f"Headers: {headers}")
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True)
        logger.debug(f"Dify API Response Status Code: {response.status_code}")
        logger.debug(f"Dify API Response Body: {response.text}")
        response.raise_for_status()
        return process_streaming_response(response)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Dify API: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Dify API response: {e.response.text}")
        raise HTTPException(status_code=500, detail=f"Error calling Dify API: {e.response.text if hasattr(e, 'response') else str(e)}")

def process_streaming_response(response):
    full_response = ""
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8')
            logger.debug(f"Received line from Dify API: {decoded_line}")
            if decoded_line.startswith('data:'):
                try:
                    data = json.loads(decoded_line[5:])
                    if 'answer' in data:
                        full_response += data['answer']
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON: {str(e)}")
    if full_response:
        logger.debug(f"Full response from Dify API: {full_response}")
        return {"answer": full_response}
    else:
        logger.error("Empty or invalid response from Dify API")
        raise HTTPException(status_code=500, detail="Empty or invalid response from Dify API")

@app.post("/chat")
async def chat(chat_message: ChatMessage, current_user: dict = Depends(get_current_user)):
    try:
        # 課金状況とチャット回数をチェック
        user_profile = supabase.table('user_profiles').select('*').eq('user_id', current_user.user.id).single().execute().data
        chat_count = user_profile.get('chat_count', 0)

        if not user_profile:
            raise HTTPException(status_code=404, detail="User not found")

        if not user_profile.get('is_paid', False):
            raise HTTPException(status_code=402, detail="Please purchase access to start chatting.")
        
        # チャット回数を確認
        if user_profile['chat_count'] >= 50:
            return JSONResponse(status_code=402, content={"detail": "Chat limit reached. Please purchase more credits."})

        logger.debug(f"Processing chat message: {chat_message.message}")

        # チャットメッセージを保存
        supabase.table('chat_messages').insert({
            'user_id': chat_message.user_id,
            'role': 'user',
            'content': chat_message.message
        }).execute()

        # Dify API を呼び出して回答を取得
        logger.debug(f"Calling Dify API with message: {chat_message.message}")
        dify_response = chat_with_dify(chat_message.message, current_user.user.id)
        logger.debug(f"Dify API response: {dify_response}")

        # 回答を取得
        answer = dify_response['answer']

        logger.debug(f"Received answer from Dify API: {answer}")

        # アシスタントの回答を保存
        supabase.table('chat_messages').insert({
            'user_id': chat_message.user_id,
            'role': 'assistant',
            'content': answer
        }).execute()

        # チャット回数を増やす
        new_chat_count = chat_count + 1
        update_result = supabase.table('user_profiles').update({'chat_count': new_chat_count}).eq('user_id', current_user.user.id).execute()
        logger.debug(f"Updated chat count. Result: {update_result}")

        return JSONResponse(content={"response": answer})

    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/chat_history")
async def get_chat_history(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user.user.id
        chat_history = supabase.table('chat_messages').select('*').eq('user_id', user_id).order('created_at').execute()
        return JSONResponse(content={"chat_history": chat_history.data}, media_type="application/json; charset=utf-8")
    except Exception as e:
        logger.error(f"Error in get_chat_history: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching chat history: {str(e)}")

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error(f"Error in stripe_webhook: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_id = session.get('customer')
        logger.debug(f"Received webhook for customer_id: {customer_id}")
        
        try:
            user = supabase.table('user_profiles').select('*').eq('stripe_customer_id', customer_id).single().execute()
            logger.debug(f"Supabase query result: {user}")
            
            if user.data:
                user_id = user.data['user_id']
                result = supabase.table('user_profiles').update({
                    'is_paid': True,
                    'chat_count': 0  # チャット回数をリセット
                }).eq('user_id', user_id).execute()

                if result.data:
                    logger.info(f"Updated payment status for customer: {customer_id}")
                    return JSONResponse(content={"status": "success", "message": "Payment status updated"})
                else:
                    logger.error(f"Failed to update user profile for customer: {customer_id}")
                    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to update user profile"})
            else:
                logger.error(f"User not found for customer: {customer_id}")
                return JSONResponse(status_code=404, content={"status": "error", "message": "User not found"})
        except Exception as e:
            logger.error(f"Error updating user stats: {str(e)}")
            return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

    return JSONResponse(content={"status": "success", "message": "Event processed"})

@app.get("/payment_success")
async def payment_success():
    # Streamlit アプリにリダイレクト
    return RedirectResponse(url=FRONTEND_URL + "?success=true")

@app.get("/payment_cancel")
async def payment_cancel():
    return JSONResponse(content={"message": "Payment was cancelled. You can try again later."})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)