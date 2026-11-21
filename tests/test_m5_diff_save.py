"""
Milestone 5 Test Suite — Granular Diff, Zero Auto-Save, and Dynamic Edit Summary.
Tests:
1. Reassembly with partial selection (unselected mods left unchanged).
2. Dynamic Hindi edit summary generation mentioning only selected categories.
3. Zero Auto-Save confirmation enforcement (unconfirmed requests strictly rejected with HTTP 400).
4. Dry-run mode verification (HTTP 200 without external mutation).
5. Diff patch generation for human download / local draft.
Authoritative Source: ORIGINAL_REQUEST.md (§5, R2), PROJECT.md (§M5).
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.diff import generate_hindi_edit_summary


class TestGranularDiffAndEditSummary:
    def test_hindi_edit_summary_generation_combinations(self):
        # 1. Bare URL only
        s1 = generate_hindi_edit_summary({"bare_url"})
        assert "नग्न URL को मानक साँचों में बदला" in s1
        assert "खराब साँचों की मरम्मत" not in s1
        assert "सन्दर्भ सुधार उपकरण" in s1

        # 2. Bare URL + Syntax Repair
        s2 = generate_hindi_edit_summary({"bare_url", "syntax_repair"})
        assert "नग्न URL को मानक साँचों में बदला" in s2
        assert "खराब साँचों की मरम्मत" in s2

        # 3. Duplicate merger + dead links
        s3 = generate_hindi_edit_summary({"duplicate_merger", "dead_link"})
        assert "समान सन्दर्भों का विलय" in s3
        assert "मृत कड़ियों में आर्काइव जोड़ा" in s3

    @pytest.mark.asyncio
    async def test_diff_partial_selection_applies_only_selected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            orig = "पहला <ref>https://a.com</ref> दूसरा <ref>https://b.com</ref>"
            mods = [
                {
                    "id": "mod-1",
                    "type": "bare_url",
                    "span_start": 6,
                    "span_end": 30,
                    "proposed_snippet": "<ref>{{Cite web |url=https://a.com |title=A}}</ref>",
                },
                {
                    "id": "mod-2",
                    "type": "bare_url",
                    "span_start": 38,
                    "span_end": 62,
                    "proposed_snippet": "<ref>{{Cite web |url=https://b.com |title=B}}</ref>",
                },
            ]
            resp = await client.post(
                "/api/diff",
                json={"original_wikitext": orig, "selected_ids": ["mod-1"], "modifications": mods},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "https://a.com |title=A" in data["reconstructed_wikitext"]
            assert "<ref>https://b.com</ref>" in data["reconstructed_wikitext"]
            assert data["selected_count"] == 1


class TestZeroAutoSave:
    @pytest.mark.asyncio
    async def test_unconfirmed_save_request_strictly_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/api/save",
                json={"title": "परीक्षण पृष्ठ", "wikitext": "पाठ", "confirmed": False},
            )
            assert resp.status_code == 400
            assert "पुष्टि अनिवार्य" in resp.text

    @pytest.mark.asyncio
    async def test_dry_run_save_returns_success_without_mutation(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/api/save",
                json={
                    "title": "परीक्षण पृष्ठ",
                    "wikitext": "सुधारे गए सन्दर्भ",
                    "confirmed": True,
                    "dry_run": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "dry_run_success"
            assert data["title"] == "परीक्षण पृष्ठ"

    @pytest.mark.asyncio
    async def test_scan_api_returns_structured_modifications(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            wikitext = "तथ्य।<ref>https://example.com</ref>\n{{सन्दर्भ सूची}}"
            resp = await client.post("/api/scan", json={"wikitext": wikitext})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["total_refs"] == 1
            assert data["bare_urls"] == 1
            assert len(data["modifications"]) >= 1
            assert "Cite web" in data["modifications"][0]["proposed_snippet"]

    @pytest.mark.asyncio
    async def test_frontend_index_page_serves_hindi_interface(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "विकिपीडिया सन्दर्भ सुधार उपकरण" in resp.text
            assert "ज़ीरो ऑटो-सेव" in resp.text
