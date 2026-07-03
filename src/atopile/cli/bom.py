"""`ato bom` — resolve concrete parts (the BOM) for a build.

The companion to `ato build --no-pick`. Where `--no-pick` produces a routable
board from footprints alone (deferring the solver-driven MPN resolution), `ato
bom` runs the part picker to resolve concrete parts and write the BOM artifacts
(`<output_base>.bom.csv` / `.bom.json`).

This is the v1 shell: it resolves the *full* BOM by driving the normal build
pipeline up to the `generate-bom` target with picking enabled. The incremental,
sidecar-driven form (pick a subset, layer extra constraints without editing the
`.ato`, run concurrently with routing) is built on top of this entry point.
"""

from typing import Annotated

import typer

from atopile.cli import build as build_cli
from atopile.logging import logger as log


def bom(
    entry: Annotated[str | None, typer.Argument()] = None,
    build: Annotated[
        list[str], typer.Option("--build", "-b", envvar="ATO_BUILD")
    ] = [],
    pick: Annotated[
        list[str],
        typer.Option(
            "--pick",
            help="Incrementally pin a part into the parts.yaml sidecar without "
            "editing the .ato: `--pick <address>=<lcsc>` (e.g. --pick r1=C25819). "
            "Repeatable. Recorded, then the BOM is resolved.",
        ),
    ] = [],
) -> None:
    """Resolve concrete parts (the BOM) for a build.

    Runs the part picker (solver-driven MPN resolution) and writes the BOM
    artifacts. Use after `ato build --no-pick`, which leaves the BOM open so
    place & route can proceed immediately. `--pick` records pins into an
    out-of-source `parts.yaml` overlay (H3), leaving the `.ato` untouched.
    """
    from atopile.config import config

    if pick:
        # Record the pins into the sidecar first, then resolve.
        from faebryk.libs.app.parts_sidecar import (
            PartEntry,
            default_parts_path,
            merge_pick,
        )

        config.apply_options(entry=entry, selected_builds=build if build else ())
        build_name = list(config.selected_builds)[0]
        with config.select_build(build_name):
            path = config.build.paths.parts_config or default_parts_path()
            for spec in pick:
                if "=" not in spec:
                    raise typer.BadParameter(
                        f"--pick expects <address>=<lcsc>, got {spec!r}"
                    )
                address, lcsc = spec.split("=", 1)
                merge_pick(path, address.strip(), PartEntry(lcsc=lcsc.strip()))
            log.info(f"Recorded {len(pick)} pin(s) into {path}")

    log.info("Resolving BOM (running the part picker)...")
    build_cli.build(
        entry=entry,
        selected_builds=build,
        target=["generate-bom"],
        no_pick=False,
    )
