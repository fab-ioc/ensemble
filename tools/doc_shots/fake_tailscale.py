"""A stand-in for `tailscale` in the documentation's demo hub: a made-up
tailnet name and address, so no real machine name reaches a screenshot."""
import json
import sys

args = [a for a in sys.argv[1:] if a != "--json"]
if args[:1] == ["ip"]:
    print("100.64.0.10")
elif args[:1] == ["status"]:
    print(json.dumps({"BackendState": "Running", "Self": {
        "HostName": "hub", "DNSName": "hub.tailnet.example.", "TailscaleIPs": ["100.64.0.10"]}}))
elif args[:2] == ["serve", "status"]:
    print(json.dumps({}))
else:
    sys.exit(1)
