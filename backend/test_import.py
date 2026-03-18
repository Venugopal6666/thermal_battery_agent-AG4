import traceback
try:
    from agents.agent import root_agent
    print("OK: root_agent loaded successfully")
except Exception as e:
    traceback.print_exc()
