from backend.app.main import app
from firebase_functions.options import HttpsOptions

# For Firebase CLI to detect the ASGI application as a Firebase Function
# We wrap the FastAPI app in a standard ASGI function so `inspect.isfunction` evaluates to True.
async def api(scope, receive, send):
    await app(scope, receive, send)

api.__firebase_endpoint__ = HttpsOptions(region="asia-east1")._endpoint(func_name="api")
