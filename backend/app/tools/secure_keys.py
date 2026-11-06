"""Prints fresh values for NAP_CONTENT_MASTER_KEY and NAP_LICENSE_SIGNING_KEY (NAP-104). Put them in the server's
.env once; changing them later makes already downloaded packages and leases unreadable."""
import base64
import os

if __name__ == "__main__":
    for name in ("NAP_CONTENT_MASTER_KEY", "NAP_LICENSE_SIGNING_KEY"):
        print(f"{name}={base64.b64encode(os.urandom(32)).decode()}")
