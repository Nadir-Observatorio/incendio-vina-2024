"""The results block of both README files is written from datos/ by
herramientas/escribir_readme.py."""

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "herramientas" / "escribir_readme.py"


def _load():
    spec = importlib.util.spec_from_file_location("escribir_readme", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "datos", root / "datos")
    for name in ("README.md", "README.en.md"):
        shutil.copy(ROOT / name, root / name)
    return root


def test_published_block_is_up_to_date():
    """The README files must carry exactly what the script writes from datos/."""
    assert _load().main(["--comprobar", "--raiz", str(ROOT)]) == 0


def test_edited_figure_is_rewritten(tmp_path):
    """A figure edited by hand inside the block is detected and then restored from datos/."""
    script = _load()
    root = _copy(tmp_path)
    readme = root / "README.md"
    original = readme.read_bytes()
    readme.write_bytes(original.replace(b"96,8 %", b"97,8 %"))
    assert script.main(["--comprobar", "--raiz", str(root)]) == 1
    assert script.main(["--raiz", str(root)]) == 0
    assert readme.read_bytes() == original


def test_text_outside_the_block_is_kept(tmp_path):
    """Rewriting the block must not touch the rest of the file."""
    script = _load()
    root = _copy(tmp_path)
    readme = root / "README.en.md"
    readme.write_bytes(readme.read_bytes() + b"\nExtra line.\n")
    assert script.main(["--raiz", str(root)]) == 0
    assert readme.read_bytes().endswith(b"\nExtra line.\n")
