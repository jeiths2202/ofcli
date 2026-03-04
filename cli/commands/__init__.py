"""OFKMS CLI Commands — Click group registration"""
import click

from cli import __version__
from cli.commands.ask import ask
from cli.commands.auth import keys, login
from cli.commands.config_cmd import config
from cli.commands.health import health
from cli.commands.products import products


@click.group()
@click.version_option(version=__version__, prog_name="ofkms")
def cli():
    """OFKMS v2 -- OpenFrame Knowledge Management CLI"""


cli.add_command(ask)
cli.add_command(health)
cli.add_command(products)
cli.add_command(login)
cli.add_command(keys)
cli.add_command(config)
