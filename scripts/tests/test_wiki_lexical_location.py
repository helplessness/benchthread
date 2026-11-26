from plugins.paper_research_workflow.wiki.store import PaperWikiStore


def test_literal_hit_without_vector_index(tmp_path):
    store = PaperWikiStore(library_root=tmp_path / "library", workdir=str(tmp_path))
    body = "第一段。\n\n第二段：AmberCompass。\n末段：AmberCompass。"
    store.save_page({"title": "TitleOnly", "content": body}, actor="test")
    hit = store.search("ambercompass")["hits"][0]
    assert [loc["line_start"] for loc in hit["matched_chunks"]] == [3, 4]
    for location in hit["matched_chunks"]:
        assert body.encode()[location["byte_start"]:location["byte_end"]].decode() == location["content"]
    assert "content" not in hit
    assert not store.search("TitleOnly")["hits"][0]["matched_chunks"]
