"""Prints the password of the demo accounts (*@nap.demo) on this server.

On the store server:  ssh root@SERVER 'docker exec $(docker ps -q -f label=com.docker.compose.service=api | head -1) python -m app.tools.demo_password'
"""
from ..security import demo_account_password

if __name__ == "__main__":
    print(demo_account_password())
