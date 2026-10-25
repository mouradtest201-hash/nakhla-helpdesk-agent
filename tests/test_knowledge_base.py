"""Dataset/precedence tests that do not call the network."""
from __future__ import annotations

from app.domain.manifest import allowed_citation_ids, known_error_codes, load_manifest
from app.domain.playbooks import CODE_PLAYBOOKS, known_generic_code
from app.rag.ingest import build_chunks


def test_only_current_documents_are_citable():
    allowed = allowed_citation_ids()
    assert {"KB-002", "KB-012", "KB-013", "SP-001", "SP-002"} <= allowed
    assert not {"KB-001", "KB-005", "KB-018"} & allowed
    manifest = load_manifest()
    assert manifest.doc("KB-001").status == "deprecated"
    assert manifest.doc("KB-018").status == "draft"


def test_every_manifest_error_code_has_a_playbook():
    assert set(known_error_codes()) == set(CODE_PLAYBOOKS)
    assert known_generic_code("CRM-1150")
    assert not known_generic_code("IT-1021")


def test_playbook_documents_are_current_sources():
    manifest = load_manifest()
    for play in CODE_PLAYBOOKS.values():
        doc = manifest.doc(play.doc)
        assert doc is not None and doc.is_source, play.code


def test_poisoned_wiki_is_not_indexed_and_pdfs_are():
    chunks = build_chunks()
    assert len(chunks) > 90
    assert not any(chunk.doc_id == "KB-018" for chunk in chunks)
    assert {"KB-012", "KB-013"} <= {chunk.doc_id for chunk in chunks}
    text = "\n".join(chunk.text for chunk in chunks)
    assert "Nakhla-LocalAdmin-7731" not in text
    assert "posture_check=off" not in text
