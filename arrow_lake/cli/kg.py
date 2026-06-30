"""CLI kg commands — knowledge graph build, query, stats."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, _run_async, console


@click.group()
def kg_group() -> None:
    """Knowledge graph operations."""


@kg_group.command("build")
@click.argument("dataset")
@click.pass_context
def kg_build(ctx: click.Context, dataset: str) -> None:
    """Build knowledge graph from a dataset."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Building knowledge graph from '{dataset}'...[/dim]")

    try:
        task_id = _run_async(lake.kg_build(dataset))
    except Exception as exc:
        _print_error(f"KG build failed: {exc}")
        raise SystemExit(1) from None

    console.print(f"  Task ID: {task_id}")
    _print_success("KG build started (use 'kg status <task_id>' to check progress)")


@kg_group.command("status")
@click.argument("task_id")
@click.pass_context
def kg_status(ctx: click.Context, task_id: str) -> None:
    """Check knowledge graph build status."""
    lake = _get_lake(ctx)

    try:
        status = _run_async(lake.kg_build_status(task_id))
    except Exception as exc:
        _print_error(f"Failed to get status: {exc}")
        raise SystemExit(1) from None

    if status is None:
        _print_error(f"Task '{task_id}' not found")
        raise SystemExit(1) from None

    console.print(json.dumps(status, indent=2, default=str))


@kg_group.command("stats")
@click.option("--dataset", default=None, help="Lake path — scope to the kg_{dataset} graph")
@click.pass_context
def kg_stats(ctx: click.Context, dataset: str | None) -> None:
    """Show knowledge graph statistics."""
    lake = _get_lake(ctx)

    try:
        stats = _run_async(lake.kg_stats(dataset_name=dataset))
    except Exception as exc:
        _print_error(f"Failed to get stats: {exc}")
        raise SystemExit(1) from None

    table = Table(title="Knowledge Graph Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for key, value in stats.items():
        table.add_row(key.replace("_", " ").title(), str(value))

    console.print(table)


@kg_group.command("query")
@click.argument("gremlin_query")
@click.pass_context
def kg_query(ctx: click.Context, gremlin_query: str) -> None:
    """Execute a Gremlin query against the knowledge graph."""
    lake = _get_lake(ctx)

    try:
        results = _run_async(lake.kg_query(gremlin_query))
    except Exception as exc:
        _print_error(f"KG query failed: {exc}")
        raise SystemExit(1) from None

    if not results:
        console.print("[dim]No results.[/dim]")
        return

    console.print(json.dumps(results, indent=2, default=str))


@kg_group.command("neighbors")
@click.argument("entity_id")
@click.option("--depth", default=1, help="Traversal depth")
@click.option("--dataset", default=None, help="Lake path — scope to the kg_{dataset} graph")
@click.pass_context
def kg_neighbors(ctx: click.Context, entity_id: str, depth: int, dataset: str | None) -> None:
    """Get neighbors of an entity in the knowledge graph."""
    lake = _get_lake(ctx)

    try:
        neighbors = _run_async(
            lake.kg_get_neighbors(entity_id, depth=depth, dataset_name=dataset)
        )
    except Exception as exc:
        _print_error(f"Failed to get neighbors: {exc}")
        raise SystemExit(1) from None

    if not neighbors:
        console.print("[dim]No neighbors found.[/dim]")
        return

    console.print(json.dumps(neighbors, indent=2, default=str))


@kg_group.command("delete")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_context
def kg_delete(ctx: click.Context, yes: bool) -> None:
    """Delete all data from the knowledge graph (irreversible)."""
    if not yes and not click.confirm("Delete ALL knowledge graph data? This cannot be undone."):
        return

    lake = _get_lake(ctx)

    try:
        _run_async(lake.kg_delete_graph())
    except Exception as exc:
        _print_error(f"KG deletion failed: {exc}")
        raise SystemExit(1) from None

    _print_success("Knowledge graph deleted")


# ------------------------------------------------------------------
# Traverser subgroup
# ------------------------------------------------------------------

@click.group()
def traverser_group() -> None:
    """Graph traversal algorithms."""


@traverser_group.command("all-shortest-paths")
@click.argument("source")
@click.argument("target")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--max-depth", default=10, type=int)
@click.pass_context
def traverser_all_shortest_paths(ctx: click.Context, source: str, target: str, direction: str, max_depth: int) -> None:
    """Find all shortest paths between two vertices."""
    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_all_shortest_paths(source, target, direction=direction, max_depth=max_depth))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


@traverser_group.command("weighted-shortest")
@click.argument("source")
@click.argument("target")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--weight-prop", default="weight", help="Edge weight property")
@click.option("--max-degree", default=10000, type=int)
@click.pass_context
def traverser_weighted_shortest(ctx: click.Context, source: str, target: str, direction: str, weight_prop: str, max_degree: int) -> None:
    """Weighted shortest path between two vertices."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_weighted_shortest_path(source, target, direction=direction, weight_prop=weight_prop, max_degree=max_degree))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@traverser_group.command("single-source-shortest")
@click.argument("source")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--weight-prop", default="weight")
@click.option("--max-degree", default=10000, type=int)
@click.pass_context
def traverser_single_source_shortest(ctx: click.Context, source: str, direction: str, weight_prop: str, max_degree: int) -> None:
    """Single source shortest path to all reachable vertices."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_single_source_shortest_path(source, direction=direction, weight_prop=weight_prop, max_degree=max_degree))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@traverser_group.command("multi-node-shortest")
@click.option("--sources", required=True, help="JSON array of source vertex IDs")
@click.option("--targets", required=True, help="JSON array of target vertex IDs")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--weight-prop", default="weight")
@click.option("--max-degree", default=10000, type=int)
@click.pass_context
def traverser_multi_node_shortest(ctx: click.Context, sources: str, targets: str, direction: str, weight_prop: str, max_degree: int) -> None:
    """Shortest paths between multiple source-target pairs."""
    try:
        src_list = json.loads(sources)
        tgt_list = json.loads(targets)
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON: {exc}")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_multi_node_shortest_path(src_list, tgt_list, direction=direction, weight_prop=weight_prop, max_degree=max_degree))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


@traverser_group.command("rays")
@click.argument("source")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--max-depth", default=5, type=int)
@click.pass_context
def traverser_rays(ctx: click.Context, source: str, direction: str, max_depth: int) -> None:
    """Rays — non-cyclic paths from a source vertex."""
    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_rays(source, direction=direction, max_depth=max_depth))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


@traverser_group.command("rings")
@click.argument("source")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--max-depth", default=5, type=int)
@click.pass_context
def traverser_rings(ctx: click.Context, source: str, direction: str, max_depth: int) -> None:
    """Rings — cyclic paths from source back to itself."""
    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_rings(source, direction=direction, max_depth=max_depth))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


@traverser_group.command("crosspoints")
@click.argument("source")
@click.argument("target")
@click.option("--direction", default="OUT", type=click.Choice(["OUT", "BOTH", "IN"]))
@click.option("--max-depth", default=5, type=int)
@click.pass_context
def traverser_crosspoints(ctx: click.Context, source: str, target: str, direction: str, max_depth: int) -> None:
    """Crosspoints — vertices on paths between source and target."""
    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_crosspoints(source, target, direction=direction, max_depth=max_depth))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


@traverser_group.command("customized")
@click.argument("source")
@click.option("--steps", required=True, help="JSON array of step dicts [{labels, direction, properties}]")
@click.option("--with-vertex/--no-with-vertex", default=True)
@click.option("--with-edge/--no-with-edge", default=True)
@click.pass_context
def traverser_customized(ctx: click.Context, source: str, steps: str, with_vertex: bool, with_edge: bool) -> None:
    """Customized multi-step path traversal."""
    try:
        steps_list = json.loads(steps)
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON in --steps: {exc}")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)
    try:
        results = _run_async(lake.kg_customized_paths(source, steps_list, with_vertex=with_vertex, with_edge=with_edge))
    except Exception as exc:
        _print_error(f"Traverser failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(results, indent=2, default=str))


# ------------------------------------------------------------------
# Algo subgroup (Vermeer OLAP)
# ------------------------------------------------------------------

@click.group()
def algo_group() -> None:
    """Graph OLAP algorithms (via Vermeer)."""


@algo_group.command("pagerank")
@click.option("--iterations", default=20, type=int)
@click.option("--damping", default=0.85, type=float, help="Damping factor")
@click.pass_context
def algo_pagerank(ctx: click.Context, iterations: int, damping: float) -> None:
    """PageRank — identify important vertices."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_pagerank(iterations=iterations, damping_factor=damping))
    except Exception as exc:
        _print_error(f"PageRank failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("louvain")
@click.option("--resolution", default=1.0, type=float)
@click.pass_context
def algo_louvain(ctx: click.Context, resolution: float) -> None:
    """Louvain community detection."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_louvain(resolution=resolution))
    except Exception as exc:
        _print_error(f"Louvain failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("label-propagation")
@click.pass_context
def algo_label_propagation(ctx: click.Context) -> None:
    """Label Propagation community detection."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_label_propagation())
    except Exception as exc:
        _print_error(f"Label propagation failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("wcc")
@click.pass_context
def algo_wcc(ctx: click.Context) -> None:
    """Weakly Connected Components."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_wcc())
    except Exception as exc:
        _print_error(f"WCC failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("triangle-count")
@click.pass_context
def algo_triangle_count(ctx: click.Context) -> None:
    """Triangle counting."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_triangle_count())
    except Exception as exc:
        _print_error(f"Triangle count failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("degree-centrality")
@click.pass_context
def algo_degree_centrality(ctx: click.Context) -> None:
    """Degree centrality computation."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_degree_centrality())
    except Exception as exc:
        _print_error(f"Degree centrality failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("closeness-centrality")
@click.pass_context
def algo_closeness_centrality(ctx: click.Context) -> None:
    """Closeness centrality computation."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_closeness_centrality())
    except Exception as exc:
        _print_error(f"Closeness centrality failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("k-core")
@click.option("--k", default=3, type=int)
@click.pass_context
def algo_k_core(ctx: click.Context, k: int) -> None:
    """K-core decomposition."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_k_core(k=k))
    except Exception as exc:
        _print_error(f"K-core failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


@algo_group.command("betweenness-centrality")
@click.pass_context
def algo_betweenness_centrality(ctx: click.Context) -> None:
    """Betweenness centrality computation."""
    lake = _get_lake(ctx)
    try:
        result = _run_async(lake.kg_betweenness_centrality())
    except Exception as exc:
        _print_error(f"Betweenness centrality failed: {exc}")
        raise SystemExit(1) from None
    console.print(json.dumps(result, indent=2, default=str))


# ------------------------------------------------------------------
# Graph I/O
# ------------------------------------------------------------------

@kg_group.command("export")
@click.option("--output", default=None, help="Output file path (default: stdout)")
@click.option("--with-properties/--no-with-properties", default=True)
@click.pass_context
def kg_export(ctx: click.Context, output: str | None, with_properties: bool) -> None:
    """Export the full knowledge graph as JSON."""
    lake = _get_lake(ctx)

    try:
        data = _run_async(lake.kg_export_graph(with_properties=with_properties))
    except Exception as exc:
        _print_error(f"Graph export failed: {exc}")
        raise SystemExit(1) from None

    content = json.dumps(data, indent=2, default=str)

    if output:
        from pathlib import Path
        Path(output).write_text(content)
        _print_success(f"Graph exported to '{output}'")
    else:
        click.echo(content)


@kg_group.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.pass_context
def kg_import(ctx: click.Context, file_path: str) -> None:
    """Import graph data from a JSON file."""
    lake = _get_lake(ctx)

    try:
        with open(file_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _print_error(f"Failed to read file: {exc}")
        raise SystemExit(1) from None

    try:
        result = _run_async(lake.kg_import_graph(data))
    except Exception as exc:
        _print_error(f"Graph import failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Graph imported: {result}")
