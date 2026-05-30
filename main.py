import json
import traceback
from backend.app.main import app
from firebase_functions import https_fn
from flask import Response as FlaskResponse
from werkzeug.wrappers import Response as WerkzeugResponse
from a2wsgi import ASGIMiddleware

# 將 FastAPI (ASGI) 轉換為 WSGI 供 Firebase Functions 使用
wsgi_app = ASGIMiddleware(app)

def handle_request(req: https_fn.Request) -> https_fn.Response:
    # 建立基礎 CORS 標頭
    cors_headers = {
        "Access-Control-Allow-Origin": req.headers.get("Origin", "*"),
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
        "Access-Control-Allow-Credentials": "true",
    }

    # 1. 快速處理 CORS 預檢請求 (OPTIONS)，不進入 ASGI，避免冷啟動或內部錯誤導致預檢失敗
    if req.method == "OPTIONS":
        return FlaskResponse(
            status=204,
            headers=cors_headers
        )

    try:
        # 2. 執行 WSGI 應用程式
        w_res = WerkzeugResponse.from_app(wsgi_app, req.environ)
        
        # 確保成功的回應也有 CORS 標頭
        headers = dict(w_res.headers)
        for key, val in cors_headers.items():
            if key not in headers:
                headers[key] = val
                
        return FlaskResponse(
            response=w_res.get_data(),
            status=w_res.status_code,
            headers=headers,
            mimetype=w_res.mimetype
        )
    except Exception as e:
        # 3. 捕獲所有未處理的例外，輸出完整 traceback，並回傳 500 JSON 與 CORS 標頭
        tb = traceback.format_exc()
        print(f"Exception during request handling:\n{tb}")
        
        return FlaskResponse(
            response=json.dumps({
                "detail": f"Internal Server Error: {str(e)}",
                "trace": tb.splitlines()
            }, ensure_ascii=False),
            status=500,
            mimetype="application/json",
            headers=cors_headers
        )

from firebase_functions import options

# 註冊為 Firebase HTTP 函數，支援公開呼叫 (invoker="public")
# 增加記憶體至 512MB 以獲得更多 CPU 資源，並將超時時間延長至 180 秒
@https_fn.on_request(
    region="asia-east1", 
    invoker="public", 
    timeout_sec=180, 
    memory=options.MemoryOption.MB_512
)
def api(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)

@https_fn.on_request(
    region="asia-east1", 
    invoker="public", 
    timeout_sec=180, 
    memory=options.MemoryOption.MB_512
)
def api_preview(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)
