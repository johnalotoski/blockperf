import logging
from pathlib import Path

import click
import psutil

from blockperf import logger_name
from blockperf.app import App
from blockperf.config import AppConfig


def already_running() -> bool:
    """Checks if blockperf is already running."""
    blockperfs = []
    for proc in psutil.process_iter():
        if "blockperf" in proc.name():
            blockperfs.append(proc)
    if len(blockperfs) > 1:
        return True
    return False


def configure_logging(debug: bool = False):
    """Configures the root logger"""
    # Configure blockperf logger
    lvl = logging.DEBUG if debug else logging.INFO
    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(lvl)
    logging.basicConfig(level=lvl, handlers=[stdout_handler])


@click.group()
def main():
    """
    This script is based on blockperf.sh which collects data from the cardano-node
    and sends it to an aggregation services for further analysis.
    """
    # dont print() but click.echo()
    pass


@click.command("run", short_help="Run blockperf")
@click.argument(
    "config_file_path", required=False, type=click.Path(resolve_path=True, exists=True)
)
@click.option(
    "-d",
    "--debug",
    is_flag=True,
    help="Enables debug mode (print even more than verbose)",
)
def cmd_run(config_file_path=None, verbose=False, debug=False):
    if already_running():
        click.echo(f"Is blockperf already running?")
        raise SystemExit

    if debug:
        click.echo("Debug enabled")

    configure_logging(debug)
    app_config = AppConfig(config_file_path)
    app_config.validate_or_die()
    app = App(app_config)
    app.run()


main.add_command(cmd_run)
