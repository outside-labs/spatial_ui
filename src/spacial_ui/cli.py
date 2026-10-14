"""Command line entry point."""

import argparse

from spacial_ui import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="spacial-ui", description="A Python spatial UI engine."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    demo = commands.add_parser("demo", help="Run the local interactive dashboard")
    demo.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "demo":
        from spacial_ui.demo import Dashboard, demo_stylesheet
        from spacial_ui.server import App
        App(Dashboard, title="spacial_ui / Outside Labs", stylesheet=demo_stylesheet()).run(args.port)
        return
    parser.print_help()
