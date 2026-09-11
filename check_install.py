import sys

results = []

try:
    import google.adk
    results.append(f"google-adk:          {google.adk.__version__} OK")
except ImportError as e:
    results.append(f"google-adk:          MISSING ({e})")

try:
    import google.genai
    results.append(f"google-genai:        OK")
except ImportError as e:
    results.append(f"google-genai:        MISSING ({e})")

try:
    import botbuilder.core
    results.append(f"botbuilder-core:     OK")
except ImportError as e:
    results.append(f"botbuilder-core:     MISSING ({e})")

try:
    import aiohttp
    results.append(f"aiohttp:             {aiohttp.__version__} OK")
except ImportError as e:
    results.append(f"aiohttp:             MISSING ({e})")

print(f"Python: {sys.version}")
print()
for r in results:
    print(r)
