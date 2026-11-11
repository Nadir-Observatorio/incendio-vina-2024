"""The factsheet is built from datos/ only, in both languages; same inputs give the same file."""
import hashlib
import re
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ficha" / "make_factsheet.py"
spec = importlib.util.spec_from_file_location("make_factsheet", SCRIPT)
ficha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ficha)

CONTEXT_FILES = ("relieve.tif", "agua.tif", "construido.tif", "lugares.geojson",
                 "chile.geojson")
needs_context = pytest.mark.skipif(
    not all((ROOT / "datos" / "contexto" / n).exists() for n in CONTEXT_FILES),
    reason="run 'python -m severidad.contexto derivar' first")


class TestNumbers:
    def test_english_and_spanish_separators(self):
        assert ficha.num(10663.04, "en", 1) == "10,663.0"
        assert ficha.num(10663.04, "es", 1) == "10.663,0"
        assert ficha.pct(96.8, "es") == "96,8 %"

    def test_both_languages_have_the_same_keys(self):
        """A text missing in one language would fail only when that PDF is built."""
        assert set(ficha.TEXT["en"]) == set(ficha.TEXT["es"])
        assert len(ficha.TEXT["en"]["steps"]) == len(ficha.TEXT["es"]["steps"])
        assert len(ficha.TEXT["en"]["lims"]) == len(ficha.TEXT["es"]["lims"])


@needs_context
class TestBuild:
    @pytest.mark.parametrize("lang", ["en", "es"])
    def test_two_pages_and_deterministic(self, tmp_path, lang):
        a, b = ficha.build(lang, tmp_path / "a.pdf"), ficha.build(lang, tmp_path / "b.pdf")
        data = a.read_bytes()
        assert len(re.findall(rb"/Type\s*/Page[^s]", data)) == 2
        assert hashlib.sha256(data).digest() == hashlib.sha256(b.read_bytes()).digest()
        assert b"CreationDate" not in data

    def test_refuses_to_overwrite(self, tmp_path):
        out = tmp_path / "f.pdf"
        out.write_bytes(b"x")
        with pytest.raises(FileExistsError):
            ficha.build("es", out)

    def test_context_must_be_on_the_class_grid(self, tmp_path, monkeypatch):
        """A relief on another grid would draw shading off the burned areas without warning."""
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        ctx = tmp_path / "contexto"
        ctx.mkdir()
        for name in ("agua", "relieve", "construido"):
            profile = {"driver": "GTiff", "width": 3, "height": 3, "count": 1, "dtype": "uint8",
                       "crs": "EPSG:32719", "transform": from_origin(0, 0, 20, 20)}
            with rasterio.open(ctx / f"{name}.tif", "w", **profile) as r:
                r.write(np.zeros((3, 3), dtype=np.uint8), 1)
        monkeypatch.setattr(ficha, "CONTEXT", ctx)
        with pytest.raises(ValueError, match="grid"):
            ficha.load()

    def test_missing_built_up_layer_says_how_to_make_it(self, tmp_path, monkeypatch):
        """A context folder derived before the built-up layer existed must not build silently."""
        import shutil

        ctx = tmp_path / "contexto"
        shutil.copytree(ROOT / "datos" / "contexto", ctx)
        (ctx / "construido.tif").unlink()
        monkeypatch.setattr(ficha, "CONTEXT", ctx)
        with pytest.raises(FileNotFoundError, match="derivar"):
            ficha.load()

    def test_built_up_outline_is_drawn(self, tmp_path, monkeypatch):
        """The outline must reach the PDF: without built-up pixels the content is different."""
        import numpy as np

        with_outline = ficha.pdf_content_hash(ficha.build("en", tmp_path / "a.pdf"))
        real_load = ficha.load

        def without_built() -> dict:
            d = real_load()
            d["built"] = np.zeros_like(d["built"])
            return d

        monkeypatch.setattr(ficha, "load", without_built)
        assert ficha.pdf_content_hash(ficha.build("en", tmp_path / "b.pdf")) != with_outline

    @pytest.mark.parametrize("lang", ["en", "es"])
    def test_worldcover_attribution_is_printed(self, lang):
        """CC BY 4.0 of WorldCover asks for its notice on every map that uses it."""
        notice = ("\u00a9 ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data "
                  "(2021) processed by ESA WorldCover consortium")
        assert notice in ficha.TEXT[lang]["sources"]


def _tiny_pdf(text: bytes, level: int, indirect_length: bool = True) -> bytes:
    """Minimal one-page PDF whose content stream is compressed at zlib ``level``."""
    import zlib

    stream = zlib.compress(b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET", level)
    length = b"5 0 R" if indirect_length else str(len(stream)).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length " + length + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
    ]
    if indirect_length:                  # the length lives in its own object, as matplotlib does
        objs.append(str(len(stream)).encode())
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


class TestContentHash:
    def test_same_content_other_compression_same_hash(self, tmp_path):
        """What Windows and Linux do to the same factsheet: other bytes, same content hash."""
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        a.write_bytes(_tiny_pdf(b"6.760 ha", level=1))
        b.write_bytes(_tiny_pdf(b"6.760 ha", level=9))
        assert a.read_bytes() != b.read_bytes()
        assert ficha.pdf_content_hash(a) == ficha.pdf_content_hash(b)

    def test_direct_length_is_read_too(self, tmp_path):
        """Streams with ``/Length <n>`` (no reference) are decompressed the same way."""
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        a.write_bytes(_tiny_pdf(b"x", level=1, indirect_length=False))
        b.write_bytes(_tiny_pdf(b"x", level=9, indirect_length=False))
        assert a.read_bytes() != b.read_bytes()
        assert ficha.pdf_content_hash(a) == ficha.pdf_content_hash(b)

    def test_other_content_other_hash(self, tmp_path):
        """A changed figure must change the hash, even with the same compression."""
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        a.write_bytes(_tiny_pdf(b"6.760 ha", level=6))
        b.write_bytes(_tiny_pdf(b"6.761 ha", level=6))
        assert ficha.pdf_content_hash(a) != ficha.pdf_content_hash(b)
