"""查看 Qdrant official_docs 中每个点的 payload（特别是 metadata.version）。"""
import json
import urllib.request

URL = "http://localhost:6333/collections/official_docs/points/scroll"
body = json.dumps({"limit": 10, "with_payload": True, "with_vector": False}).encode()
req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read())

for pt in data["result"]["points"]:
    meta = pt["payload"].get("metadata", {})
    print(f"id={pt['id']}")
    print(f"  metadata.technology={meta.get('technology')!r}")
    print(f"  metadata.version   ={meta.get('version')!r}")
    print(f"  metadata.topic     ={meta.get('topic')!r}")
    print(f"  content[:80]       ={(pt['payload'].get('page_content') or pt['payload'].get('text') or str(pt['payload']))[:80]!r}")
    print()
