"""Command line: rebuild the published study and check it.

    python -m severidad run --out FOLDER [--raw datos_crudos]
    python -m severidad verify [--out FOLDER] [--root .]
    python -m severidad download [--bands visual] [--raw datos_crudos]
    python -m severidad ajuste --out FOLDER [--raw datos_crudos] [--published datos]

``run`` downloads what is missing (scenes, municipal boundaries) into ``--raw``, computes the whole
study and writes the four published files to a new ``--out`` folder; the Copernicus references must
already be in ``--raw/referencias`` (notebook 02 explains where to get them).

``download`` fetches extra bands of the two study scenes into ``--raw`` (by default the
true-colour composite used by the web viewer); they are only drawn and never change ``datos/``.

``ajuste`` computes the additional result (local fit of the burned / unburned threshold, see
``severidad.ajuste``) into a new ``--out`` folder; it never writes to ``datos/``.

``verify`` needs no network: it checks that the figures in the README files and the web map match
``datos/``; with ``--out`` it also compares a ``run`` folder with ``datos/``.

Exit codes: 0 everything matches, 1 differences found, 2 the run or the check could not be done.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, ajuste, estudio, fuente
from .errors import SeveridadError

logger = logging.getLogger("severidad")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m severidad",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"severidad {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="rebuild the published files from scratch")
    run.add_argument("--out", type=Path, required=True, help="new folder for the results")
    run.add_argument("--raw", type=Path, default=Path("datos_crudos"),
                     help="downloads and references (default: datos_crudos)")
    download = commands.add_parser("download", help="download extra bands of the study scenes")
    download.add_argument("--bands", nargs="+", default=list(fuente.COLOUR_BANDS),
                          help="catalogue asset names (default: %(default)s)")
    download.add_argument("--raw", type=Path, default=Path("datos_crudos"),
                          help="download folder (default: datos_crudos)")
    adjust = commands.add_parser("ajuste", help="local threshold fit (additional result)")
    adjust.add_argument("--out", type=Path, required=True, help="new folder for the result")
    adjust.add_argument("--raw", type=Path, default=Path("datos_crudos"),
                        help="scenes, references and boundaries (default: datos_crudos)")
    adjust.add_argument("--published", type=Path, default=Path("datos"),
                        help="published study files (default: datos)")
    verify = commands.add_parser("verify", help="check documents and, optionally, a run folder")
    verify.add_argument("--out", type=Path, help="folder written by `run` to compare with datos/")
    verify.add_argument("--root", type=Path, default=Path(),
                        help="repository root (default: current folder)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            paths = estudio.run(args.out, args.raw)
            for path in paths.values():
                logger.info("written %s", path)
            return 0
        if args.command == "ajuste":
            path = ajuste.run(args.out, args.raw, args.published)
            data = json.loads(path.read_text(encoding="utf-8"))
            for line in ajuste.summary(data):
                logger.info("%s", line)
            return 0
        if args.command == "download":
            for scene, bands in estudio.scenes(args.raw, args.bands).items():
                for band, path in bands.items():
                    logger.info("%s %s: %s", scene, band, path)
            return 0
        problems = estudio.compare_documents(args.root)
        if args.out is not None:
            problems += estudio.compare_outputs(args.out, args.root / "datos")
    except (SeveridadError, FileExistsError, FileNotFoundError) as error:
        logger.error("could not complete: %s", error)
        return 2
    for problem in problems:
        logger.error("DIFFERENT: %s", problem)
    if problems:
        return 1
    checked = "documents and run folder" if args.out is not None else "documents"
    logger.info("IDENTICAL: %s match the published data", checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
