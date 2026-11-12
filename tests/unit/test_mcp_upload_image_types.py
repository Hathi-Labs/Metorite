"""CIV-14b — the type of an image comes from its first bytes, and no SVG lands.

Spec: ``project-docs/specs/external_agent_access.md`` §12.9 (scope items 1
and 4) and §15 row CIV-14b.

Two halves, one rule. The edge reads the type of ``upload_task_image`` from
the bytes (``tools.upload_type``), and the file name never decides it. The
gateway upload route checks the bytes against the declared type for EVERY
caller, a person in the app too (``attachments.stored_type``). Both call the
one sniff, ``acb_common.image_probe.sniff_image_mime``, and the one SVG test,
``looks_like_svg``.

Database-free. ``test_mcp_upload_image_tenant_r8.py`` holds the route on a
real database.

Run::

    uv run pytest tests/unit/test_mcp_upload_image_types.py -v -rs

**The mutations (R7).**

* T1. ``agent_edge/tools.py`` ``upload_type``: return ``"image/png"`` with no
  sniff. ``test_t1_an_exe_with_a_png_name_is_refused_with_no_request`` and
  ``test_t1_a_jpeg_body_named_png_goes_out_as_jpeg``.
* T2. ``gateway/routes/projects/attachments.py`` ``stored_type``: return the
  declared type. ``test_t2_the_route_refuses_bytes_that_are_not_the_declared_image``.
* T3. ``acb_common/image_probe.py`` ``looks_like_svg``: always ``False``.
  ``test_t3_an_svg_by_its_bytes_is_refused_at_both_halves``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.image_probe import RASTER_MIMES, looks_like_svg, sniff_image_mime
from agent_edge import tools
from fastapi import HTTPException

from tests.unit.test_mcp_edge_attachment import GIF, JPEG, TINY_PNG, WEBP
from tests.unit.test_mcp_edge_task_writes import edge, redis_fake  # noqa: F401 — fixtures
from tests.unit.test_mcp_edge_writes import _text, _tool
from tests.unit.test_mcp_upload_image_limits import _args, gw, parts  # noqa: F401 — fixture

SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>steal()</script></svg>"
SVG_XML = (b"\xef\xbb\xbf  <?xml version='1.0'?>\n<!DOCTYPE svg>\n"
           b"<svg xmlns='http://www.w3.org/2000/svg'/>")
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64
HTML = b"<html><body><svg width='1'/><script>steal()</script></body></html>"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


async def _upload(data: bytes, name: str = "capture.png") -> dict:
    return await _tool("upload_task_image", _args(data, name=name))


class TestTheSniff:
    """The one sniff and the one SVG test."""

    @pytest.mark.parametrize("data,mime", [
        (TINY_PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
    ])
    def test_each_raster_type_is_named(self, data, mime):
        assert sniff_image_mime(data) == mime
        assert mime in RASTER_MIMES

    @pytest.mark.parametrize("data", [SVG, SVG_XML, EXE, HTML, WAV, PDF, b""])
    def test_no_other_bytes_are_an_image(self, data):
        assert sniff_image_mime(data) is None

    @pytest.mark.parametrize("data,svg", [
        (SVG, True), (SVG_XML, True), (b"\n\t  <SVG viewBox='0 0 1 1'/>", True),
        (HTML, False), (TINY_PNG, False), (b"<?xml version='1.0'?><note/>", False),
    ], ids=["svg", "xml_prolog_bom", "upper_case", "html_with_inline_svg", "png", "other_xml"])
    def test_looks_like_svg(self, data, svg):
        assert looks_like_svg(data) is svg


class TestTheEdge:
    """Scope item 1: the edge reads the type from the bytes only."""

    @pytest.mark.parametrize("data,mime", [
        (TINY_PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
    ])
    async def test_each_image_goes_out_with_the_type_of_its_bytes(self, gw, data, mime):  # noqa: F811 — a fixture
        result = await _upload(data, name="image.bin")
        assert result["isError"] is False, result
        (sent,) = gw.uploads()
        (part,) = parts(sent.body, sent.headers["content-type"])
        assert part["type"] == mime and part["data"] == data

    async def test_t1_an_exe_with_a_png_name_is_refused_with_no_request(self, gw):  # noqa: F811 — a fixture
        assert _text(await _upload(EXE, name="setup.png")) == tools.TEXT_IMAGE_TYPE
        assert gw.requests == []

    async def test_t1_a_jpeg_body_named_png_goes_out_as_jpeg(self, gw):  # noqa: F811 — a fixture
        result = await _upload(JPEG, name="shot.png")
        assert result["isError"] is False, result
        (sent,) = gw.uploads()
        (part,) = parts(sent.body, sent.headers["content-type"])
        assert part["type"] == "image/jpeg"

    @pytest.mark.parametrize("data,name", [
        (SVG, "logo.png"), (SVG_XML, "logo.png"), (TINY_PNG, "logo.svg"), (TINY_PNG, "logo.SVGZ"),
    ], ids=["svg_bytes", "svg_xml_bytes", "svg_name", "svgz_name"])
    async def test_t3_an_svg_by_its_bytes_is_refused_at_both_halves(self, gw, data, name):  # noqa: F811 — a fixture
        assert _text(await _upload(data, name=name)) == tools.TEXT_IMAGE_SVG
        assert gw.requests == []

    @pytest.mark.parametrize("data", [HTML, WAV, PDF], ids=["html", "wav", "pdf"])
    async def test_any_other_file_is_refused_with_no_request(self, gw, data):  # noqa: F811 — a fixture
        assert _text(await _upload(data)) == tools.TEXT_IMAGE_TYPE
        assert gw.requests == []


class TestTheRouteRule:
    """Scope item 4: the route checks the bytes against the declared type, and
    refuses an SVG, for every caller."""

    def _stored(self, name: str, declared: str | None, data: bytes) -> str:
        from gateway.routes.projects.attachments import stored_type

        return stored_type(name, declared, data)

    def _refused(self, name: str, declared: str | None, data: bytes) -> HTTPException:
        with pytest.raises(HTTPException) as caught:
            self._stored(name, declared, data)
        assert caught.value.status_code == 415
        return caught.value

    @pytest.mark.parametrize("declared,data", [
        ("image/png", HTML), ("image/png", EXE), ("image/jpeg", WAV), ("image/webp", PDF),
        ("image/gif; charset=binary", b"GIF9" + b"\x00" * 8),
    ], ids=["png_html", "png_exe", "jpeg_wav", "webp_pdf", "gif_with_params"])
    def test_t2_the_route_refuses_bytes_that_are_not_the_declared_image(self, declared, data):
        error = self._refused("shot.png", declared, data)
        assert "is not a PNG, JPEG, GIF or WebP image" in str(error.detail)

    def test_t2_a_jpeg_labelled_png_is_stored_as_jpeg(self):
        assert self._stored("shot.png", "image/png", JPEG) == "image/jpeg"

    @pytest.mark.parametrize("name,declared,data", [
        ("logo.png", "image/svg+xml", TINY_PNG), ("logo.svg", "image/png", TINY_PNG),
        ("logo.svgz", "application/octet-stream", b"\x1f\x8b"), ("logo.txt", "text/plain", SVG),
        ("logo.png", "image/png", SVG_XML), ("logo", None, SVG),
    ], ids=["declared", "name", "svgz", "text_bytes", "png_bytes", "no_type"])
    def test_t3_the_route_refuses_any_svg(self, name, declared, data):
        error = self._refused(name, declared, data)
        assert "SVG files are not accepted" in str(error.detail)

    @pytest.mark.parametrize("declared,data", [
        ("application/pdf", PDF), ("text/markdown", b"# notes"), ("application/zip", b"PK\x03\x04"),
    ])
    def test_another_declared_type_stays_as_it_came(self, declared, data):
        assert self._stored("file.bin", declared, data) == declared

    def test_no_declared_type_is_a_download(self):
        assert self._stored("file.bin", None, b"\x00\x01") == "application/octet-stream"

    def test_the_rule_runs_for_every_caller_before_the_write(self):
        """Source half: the route calls the rule after the read of the bytes
        and before any file or row is written, with no caller test around it."""
        import inspect

        from gateway.routes.projects import attachments

        source = inspect.getsource(attachments.attach_file)
        rule = source.index("stored_type(name, file.content_type, content)")
        assert source.index("await file.read()") < rule < source.index("dest.write_bytes(")
        assert rule < source.index("INSERT INTO attachments")
        line = next(ln for ln in source.splitlines() if "stored_type(" in ln)
        assert line.startswith("        mime = "), "the rule is not behind a branch"

    def test_the_agent_rules_cover_a_signed_agent_only(self):
        from types import SimpleNamespace

        from gateway.routes.projects import attachments

        person = SimpleNamespace(agent=None, organization_id=None)
        attachments._agent_upload_refusal(person)
        attachments._agent_image_refusal(person, "application/pdf", 9_000_000)
        agent = SimpleNamespace(agent=object(), organization_id=None)
        with pytest.raises(HTTPException) as caught:
            attachments._agent_image_refusal(agent, "application/pdf", 10)
        assert caught.value.status_code == 415
        with pytest.raises(HTTPException) as caught:
            attachments._agent_image_refusal(agent, "image/png", tools.UPLOAD_IMAGE_MAX_BYTES + 1)
        assert caught.value.status_code == 413
        with pytest.raises(HTTPException) as caught:
            attachments._agent_upload_refusal(agent)
        assert caught.value.status_code == 404
