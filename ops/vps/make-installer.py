#!/usr/bin/env python3
"""NAP-087: build the single-file server installer: bootstrap.sh with napdeploy.py embedded.
Usage: python3 ops/vps/make-installer.py [output]   (default: nap-vps-install.sh)"""
import base64
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
template = (here / "bootstrap.sh").read_text()
assert template.count("__NAPDEPLOY_B64__") == 1
payload = base64.b64encode((here / "napdeploy.py").read_bytes()).decode()
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "nap-vps-install.sh")
out.write_text(template.replace("__NAPDEPLOY_B64__", payload))
out.chmod(0o755)
print(out)
