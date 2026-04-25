"""CLI embed commands — generate text and image embeddings."""

from __future__ import annotations

import json

import click
import numpy as np

from arrow_lake.cli import _print_error, console


@click.group()
def embed_group() -> None:
    """Generate embeddings (text, image)."""


@embed_group.command("text")
@click.argument("text")
@click.option("--model", default="Qwen/Qwen3-Embedding-0.6B", help="Embedding model name")
@click.option("--source", default="huggingface", type=click.Choice(["huggingface", "modelscope"]), help="Model download source")
def embed_text(text: str, model: str, source: str) -> None:
    """Generate embedding vector for text."""
    from arrow_lake.embed.encoder import LocalEmbeddingEncoder

    console.print(f"[dim]Loading model {model}...[/dim]", end=" ")
    encoder = LocalEmbeddingEncoder(model_name=model, model_source=source)
    loaded_model = encoder._load_model()
    console.print("[green]done[/green]")

    console.print("[dim]Encoding...[/dim]", end=" ")
    vec = loaded_model.encode([text], normalize_embeddings=True)[0]
    console.print("[green]done[/green]")

    console.print(f"  Dimension: {vec.shape[0]}")
    console.print(f"  Norm: {np.linalg.norm(vec):.6f}")
    console.print(f"  First 5 values: {vec[:5].tolist()}")


@embed_group.command("image")
@click.argument("path")
@click.option("--model", default=None, help="CLIP model name (default: auto)")
def embed_image(path: str, model: str | None) -> None:
    """Generate embedding vector for an image."""
    try:
        import pyarrow as pa
    except ImportError:
        _print_error("pyarrow is required for image embedding")
        raise SystemExit(1) from None

    console.print(f"[dim]Loading image encoder...[/dim]", end=" ")

    try:
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        if model:
            encoder = LocalEmbeddingEncoder(model_name=model)
        else:
            encoder = LocalEmbeddingEncoder()
        loaded_model = encoder._load_model()
        console.print("[green]done[/green]")
    except Exception as exc:
        _print_error(f"Failed to load encoder: {exc}")
        raise SystemExit(1) from None

    try:
        vec = loaded_model.encode([path], normalize_embeddings=True)[0]
    except Exception as exc:
        _print_error(f"Failed to encode image: {exc}")
        raise SystemExit(1) from None

    console.print(f"  Dimension: {vec.shape[0]}")
    console.print(f"  Norm: {np.linalg.norm(vec):.6f}")
    console.print(f"  First 5 values: {vec[:5].tolist()}")
