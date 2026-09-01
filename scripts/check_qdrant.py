import json
import urllib.request

URLS = [
    "http://localhost:6333/collections/official_docs",
    "http://localhost:6333/collections/security_docs",
]

for url in URLS:
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read())
    r = data["result"]
    name = url.rsplit("/", 1)[-1]
    print(f"[{name}] status={r['status']} points={r['points_count']} vectors={r.get('vectors_count')}")
    schema = r.get("payload_schema", {})
    if schema:
        for field, info in schema.items():
            if isinstance(info, dict):
                print(f"  field: {field} -> {info.get('data_type')}")
            else:
                print(f"  field: {field} -> {info}")
