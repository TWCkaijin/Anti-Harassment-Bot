from backend.app.main import app
from firebase_functions.options import HttpsOptions

# For Firebase CLI to detect the ASGI application as a Firebase Function
app.__firebase_endpoint__ = HttpsOptions(region="asia-east1")._endpoint(func_name="api")

api = app
