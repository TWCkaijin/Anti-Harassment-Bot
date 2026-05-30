from backend.app.main import app
from firebase_functions import https_fn
from a2wsgi import ASGIMiddleware

# 將 FastAPI (ASGI) 轉換為 WSGI 供 Firebase Functions 使用
wsgi_app = ASGIMiddleware(app)

# 註冊為 Firebase HTTP 函數，支援公開呼叫 (invoker="public")
api = https_fn.on_request(
    wsgi_app,
    region="asia-east1",
    invoker="public"
)

api_preview = https_fn.on_request(
    wsgi_app,
    region="asia-east1",
    invoker="public"
)
