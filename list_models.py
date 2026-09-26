#!/usr/bin/env python3
"""Lists available Gemini models for the configured API key."""
import os, requests

key = os.environ.get("GEMINI_API_KEY", "")
resp = requests.get(
    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
    timeout=15
)
if resp.status_code == 200:
    models = resp.json().get("models", [])
    flash = [m["name"] for m in models if "flash" in m["name"].lower()]
    print("Flash models available:")
    for m in flash:
        print(f"  {m}")
else:
    print(f"Error {resp.status_code}: {resp.text[:300]}")
