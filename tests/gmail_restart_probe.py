"""Fresh-interpreter Gmail probe; only an explicit isolated sandbox is readable."""

import json
from pathlib import Path
import socket
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    from core import sandbox
    paths = sandbox.DataPaths(mode="test", test_session_id="oauth_restart")
    paths._base = root
    paths._project_root = root
    sandbox._instance = paths
    from core.mail import gmail

    def forbidden(*args, **kwargs):
        raise AssertionError("restart probe cannot access the network")

    socket.socket = forbidden
    gmail.state_path = lambda: root / "private" / "mailbox.json"
    gmail.owner = lambda: "owner"
    assert gmail._access is None and not gmail._last_error
    config = gmail.oauth_config()
    # Check private data in process; emit only public status, never the secret.
    assert config.client_secret == "fixture-secret"
    assert config.client_id == "123-fixture.apps.googleusercontent.com"
    print(json.dumps({"config": gmail.config_status(),
                      "owner": gmail.status("owner", "character"),
                      "other_role": gmail.status("owner", "other"),
                      "other_user": gmail.status("other", "character")}))


if __name__ == "__main__":
    main()
