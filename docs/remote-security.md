# Remote use and security

## What it is

The hub listens on the hub computer. A private tailnet lets a Mac or phone reach it without making the dashboard public. Remote browsers must sign in with the hub's access token; the token is stored on the hub machine in `~/.ensemble/access-token.txt`.

![The demo dashboard, shown without an access token](images/overview.png)

## How to connect remotely

1. Install Tailscale on the hub and the device you will use. Join both to the same tailnet.
2. Open the hub's tailnet address and port in the remote browser. If it asks for a token, copy the token from `~/.ensemble/access-token.txt` on the hub computer and paste it into the sign-in page.
3. Keep the token private. Use the dashboard's device and notification settings for each browser or app you use.

For an HTTPS address, configure Tailscale Serve to proxy to the hub's loopback port. The normal tailnet address remains available. Keep the hub itself on a private interface or behind the tailnet.
