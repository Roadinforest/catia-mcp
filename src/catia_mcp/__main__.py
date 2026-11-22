import argparse

from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CATIA MCP server.")
    parser.add_argument(
        "transport", choices=("stdio", "streamable-http"), nargs="?", default="stdio"
    )
    parser.add_argument("--port", type=int, default=8000, help="HTTP port on 127.0.0.1")
    parser.add_argument("--with-script", action="store_true", help="also expose run_catia_script")
    parser.add_argument("--protocol", choices=("legacy", "whucad-stateful", "whucad-migration"), default="legacy",
                        help="isolated WHUCAD stateful prototype or existing tools")
    args = parser.parse_args()
    create_server(port=args.port, with_script=args.with_script, protocol=args.protocol).run(transport=args.transport)


if __name__ == "__main__":
    main()
