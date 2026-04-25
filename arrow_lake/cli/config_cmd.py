"""CLI config commands — show and initialize configuration."""

from __future__ import annotations

import json

import click

from arrow_lake.cli import _print_error, _print_success, console


@click.group()
def config_group() -> None:
    """Configuration management."""


@config_group.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Show current configuration."""
    from arrow_lake import ArrowLakeConfig

    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")

    config = None
    if config_path:
        try:
            config = ArrowLakeConfig.from_yaml(config_path)
        except Exception as exc:
            _print_error(f"Failed to load config: {exc}")
            raise SystemExit(1) from None
    else:
        config = ArrowLakeConfig()

    console.print(f"[bold]Base URI:[/bold] {base_uri}")
    console.print(f"[bold]Config file:[/bold] {config_path or '(default)'}")
    console.print()

    dump = config.model_dump()
    console.print(json.dumps(dump, indent=2, default=str))


@config_group.command("init")
@click.option("--output", default="arrow-lake.yaml", help="Output file path")
@click.pass_context
def config_init(ctx: click.Context, output: str) -> None:
    """Generate a configuration template file."""
    import yaml

    from arrow_lake import ArrowLakeConfig

    config = ArrowLakeConfig()
    data = config.model_dump()

    try:
        with open(output, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    except OSError as exc:
        _print_error(f"Failed to write config: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Configuration template written to {output}")
