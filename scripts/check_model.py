"""Quick check that the course key and gpt-6-luna work. Never prints the key."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAI

from app import config

if not config.api_key():
    sys.exit("PORTKEY_API_KEY not found in ./.env or ../.env")

client = OpenAI(api_key=config.api_key(), base_url=config.PORTKEY_BASE_URL)
r = client.responses.create(model=config.MODEL, input="Reply with exactly: Luna OK")
print(r.output_text)
print("usage:", r.usage.input_tokens, "in /", r.usage.output_tokens, "out")
print("env file:", config.ENV_FILE.name if config.ENV_FILE else None, "from", config.ENV_FILE.parent.name if config.ENV_FILE else None)
