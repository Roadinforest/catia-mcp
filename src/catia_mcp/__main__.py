import argparse

from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CATIA MCP server.")
    parser.add_argument(
        "transport", choices=("stdio", "streamable-http"), nargs="?", default="stdio"
    )
    parser.add_argument("--port", type=int, default=8000, help="HTTP port on 127.0.0.1")
    args = parser.parse_args()
    create_server(port=args.port).run(transport=args.transport)


if __name__ == "__main__":
    main()
