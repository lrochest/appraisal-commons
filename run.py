"""Start Appraisal Commons: python run.py  (then open http://127.0.0.1:8040)."""

import uvicorn

from app import config

if __name__ == "__main__":
    print("Appraisal Commons")
    print("  API key:", "found in " + str(config.ENV_FILE) if config.api_key() else "NOT FOUND - put PORTKEY_API_KEY in .env here or one folder up")
    print("  Open http://127.0.0.1:8040 in your browser (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8040)
