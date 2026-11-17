"""Command line: run the whole pipeline from a config file.

    python -m table_extract defaults > run.json   # a config with every default, to edit
    python -m table_extract run run.json          # triage and extraction
    python -m table_extract run <folder>/run.json # resume the run in that folder
    python -m table_extract serve                 # the desktop app's local API (table_extract/server)

Set TABLE_EXTRACT_API_KEY if the servers were started with --api-key.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import RunConfig
from .runner import run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="table_extract", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Run the pipeline from a JSON config file")
    run_parser.add_argument("config", type=Path, help="RunConfig JSON file, or the run.json of an earlier run")
    run_parser.add_argument("--overwrite", action="store_true",
                            help="Replace the results of a run with different settings in the output folder")
    # Set by the desktop app's sidecar: stop when the sidecar goes away (see lifeline.py)
    run_parser.add_argument("--stop-when-stdin-closes", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("defaults", help="Print a config with every default setting")
    serve_parser = commands.add_parser("serve", help="Start the desktop app's local API on 127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=0, help="Port to listen on (default: a free one)")
    serve_parser.add_argument("--llama-server", help="llama-server executable to use instead of the installed "
                              "runtime (default: TABLE_EXTRACT_LLAMA_SERVER, then the app's llama.cpp, then the PATH)")
    serve_parser.add_argument("--data-dir", type=Path, help="Folder for models, llama.cpp and settings "
                              "(default: TABLE_EXTRACT_DATA_DIR, then the platform's app data folder)")
    serve_parser.add_argument("--stop-when-stdin-closes", action="store_true",
                              help="Exit when stdin closes, i.e. when the app that started the sidecar exits")
    args = parser.parse_args(argv)

    if args.command == "defaults":
        config = RunConfig(inputs=[Path("path/to/pdfs")], output_dir=Path("path/to/results"))
        print(config.model_dump_json(indent=2))
        return 0

    if args.command == "serve":
        from .server.serve import serve  # FastAPI is only needed here
        return serve(args.port, args.llama_server, args.stop_when_stdin_closes, args.data_dir)

    sys.stdout.reconfigure(line_buffering=True)
    try:
        data = json.loads(args.config.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            data = data["config"]  # a run.json written by a run or by the desktop app
        config = RunConfig.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        print(f"Invalid config {args.config}:\n{e}", file=sys.stderr)
        return 2
    if args.stop_when_stdin_closes:
        from .lifeline import stop_when_stdin_closes
        stop_when_stdin_closes()
    return run(config, overwrite=args.overwrite)


if __name__ == "__main__":
    sys.exit(main())
