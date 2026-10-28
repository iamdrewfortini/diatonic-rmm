"""Synthetic local-only observer demo."""
import hashlib, json, secrets, tempfile, time
from pathlib import Path
from api.auth import Identities
from api.core import Store
from api.projections import wall_projection
from api.server import Server

class MemoryProjection:
    def __init__(self): self.items = {}
    def get(self, tenant, slot): return self.items.get((tenant, slot))
    def put(self, tenant, slot, value, ttl=90): self.items[tenant, slot] = value

def make_demo_server(address=("127.0.0.1", 0)):
    token = secrets.token_urlsafe(36)
    registry = {hashlib.sha256(token.encode()).hexdigest(): {
        "tenant":"demo","actor":"observer","role":"observer","device":"synthetic-demo","expires":time.time()+3600}}
    temp = tempfile.TemporaryDirectory(prefix="diatonic-demo-")
    identities = Path(temp.name)/"identities.json"
    identities.write_text(json.dumps(registry))
    projections = MemoryProjection()
    projections.put("demo","wall",wall_projection({
        "utc":time.time(),
        "resources":[{"type":"node","node":"sample-node-a","status":"online","cpu":3,"maxcpu":8},
                     {"type":"storage","node":"sample-node-a","storage":"sample-pool","disk":61_000_000_000,"maxdisk":100_000_000_000}],
        "guests":[{"id":"vm-101","name":"sample-web","status":"running","cpu":1,"maxcpu":2,
                   "networks":["sample-net"],"storages":["sample-pool"],"controllable":True,"password":"filtered-demo-field"}],
        "metals":[{"id":"host-01","name":"sample-metal","role":"compute","status":"online","address":"127.0.0.1"}]}))
    server=Server(address,Identities(identities),projections,Store())
    server.demo_temp=temp
    return server,token

def main():
    server,token=make_demo_server(("127.0.0.1",8765))
    print("Synthetic demo at http://127.0.0.1:8765",flush=True)
    print("Temporary bearer token: "+token,flush=True)
    print("curl -H 'Authorization: Bearer "+token+"' http://127.0.0.1:8765/api/wall/snapshot",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.shutdown(); server.server_close(); server.store.db.close(); server.demo_temp.cleanup()

if __name__=="__main__": main()
