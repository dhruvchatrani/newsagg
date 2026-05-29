import re
import json

def main():
    try:
        with open('bucketlist.txt', 'r') as f:
            content = f.read()
        
        # Extract everything inside single quotes, ignoring JS/TS syntax
        assets = re.findall(r"'([A-Z0-9\.\-\/]+)'", content)
        unique_assets = sorted(list(set(assets)))
        
        with open('assets.json', 'w') as f:
            json.dump(unique_assets, f, indent=2)
            
        print(f"Successfully extracted {len(unique_assets)} unique assets to assets.json")
    except Exception as e:
        print(f"Error parsing assets: {e}")

if __name__ == "__main__":
    main()
