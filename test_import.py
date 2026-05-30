import sys
import importlib.util

try:
    sys.path.insert(0, ".")
    spec = importlib.util.spec_from_file_location("main", "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("Success!")
except Exception as e:
    import traceback
    traceback.print_exc()
