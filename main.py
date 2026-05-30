from backend.app.main import app
from firebase_functions import https_fn
from werkzeug.wrappers import Response
from a2wsgi import ASGIMiddleware

# 將 FastAPI (ASGI) 轉換為 WSGI 供 Firebase Functions 使用
wsgi_app = ASGIMiddleware(app)

# 註冊為 Firebase HTTP 函數，支援公開呼叫 (invoker="public")
@https_fn.on_request(region="asia-east1", invoker="public")
def api(req: https_fn.Request) -> https_fn.Response:
    return Response.from_app(wsgi_app, req.environ)

@https_fn.on_request(region="asia-east1", invoker="public")
def api_preview(req: https_fn.Request) -> https_fn.Response:
    return Response.from_app(wsgi_app, req.environ)
