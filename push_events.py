import json
import requests
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)

CASCADE_API_URL = "http://localhost:8800/api/admin/markets/from-prompt"
# Fetch token if needed, otherwise it sends without auth
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()

def push_events(file_path="prediction_events.json"):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return

    events = data.get("events", [])
    if not events:
        print("No events found in prediction_events.json.")
        return

    headers = {
        "Content-Type": "application/json"
    }
    if ADMIN_API_TOKEN:
        headers["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"

    for event in events:
        title = event.get("title", "")
        description = event.get("description", "")
        if not title:
            continue

        payload = {
            "title": title,
            "description": description,
            "basketSize": 4,
            "source": "quantum",
            "depth": "low",
            "brokerMode": "mock"
        }

        print(f"Pushing event: {title}")
        try:
            response = requests.post(CASCADE_API_URL, json=payload, headers=headers, timeout=30)
            if response.status_code in [200, 201]:
                print(f"Successfully pushed event: {title}")
            else:
                print(f"Failed to push event: {title}. Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            print(f"Error pushing event {title}: {e}")

if __name__ == "__main__":
    push_events()
