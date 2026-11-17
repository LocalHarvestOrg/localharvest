"""A stand-in model server for trying the app without a model (development only).

It is the FakeModelServer of tests/test_server.py: it answers every page with
"no ownership table" after DELAY seconds, so runs finish without llama-server
and stop at triage (extraction gets no pages). Run it from the repository:

    .venv_extract/bin/python app/dev/fake_model_server.py --port 8000 --delay 3

then use http://127.0.0.1:8000/v1/chat/completions as the server address.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.test_server import FakeModelServer  # noqa: E402


class FixedPortServer(FakeModelServer):
    def __init__(self, port: int):
        super(FakeModelServer, self).__init__(("127.0.0.1", port), self.Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--delay", type=float, default=2.0, help="seconds per page (default 2)")
    args = parser.parse_args()
    server = FixedPortServer(args.port)
    server.delay = args.delay
    print(f"Fake model server on http://127.0.0.1:{args.port}/v1/chat/completions "
          f"({args.delay:g} s per page). Ctrl+C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
