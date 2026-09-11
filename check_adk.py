import google.adk as adk

# Check runners and sessions
for mod in ["google.adk.runners", "google.adk.sessions"]:
    try:
        m = __import__(mod, fromlist=[""])
        print(f"{mod}: {[x for x in dir(m) if not x.startswith('_')][:10]}")
    except ImportError as e:
        print(f"{mod}: MISSING ({e})")

# Check how to run agent synchronously
from google.adk.agents import LlmAgent
from google.adk import Runner
import inspect
print()
print("Runner init signature:", inspect.signature(Runner.__init__))
print()

# Check session services
try:
    from google.adk.sessions import InMemorySessionService
    print("InMemorySessionService: OK")
    print("  signature:", inspect.signature(InMemorySessionService.__init__))
except ImportError as e:
    print("InMemorySessionService: MISSING", e)

# Check FunctionTool
try:
    from google.adk.tools import FunctionTool
    print("FunctionTool: OK")
except ImportError as e:
    print("FunctionTool:", e)

# Check genai types needed
try:
    from google.genai import types as genai_types
    print("google.genai.types: OK")
except ImportError as e:
    print("google.genai.types:", e)
