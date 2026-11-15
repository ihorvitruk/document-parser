from tests.conftest import PDF_BYTES


def pdf(name="a.pdf", content=PDF_BYTES):
    return ("files", (name, content, "application/pdf"))


def stored_files(upload_dir):
    return sorted(p.name for p in upload_dir.glob("*")) if upload_dir.exists() else []


def test_upload_single_pdf_is_accepted_and_queued(client, upload_dir):
    response = client.post("/api/documents", files=[pdf("report.pdf")], data={"parser": "pypdf"})

    assert response.status_code == 202
    [doc] = response.json()["documents"]
    assert doc["filename"] == "report.pdf"
    assert doc["parser"] == "pypdf"
    assert doc["status"] == "queued"
    assert stored_files(upload_dir) == [f"{doc['id']}.pdf"]

    detail = client.get(f"/api/documents/{doc['id']}").json()
    assert detail["status"] == "queued"
    assert detail["pages"] == []
    assert detail["summary"] is None


def test_upload_several_pdfs_creates_one_document_each(client, upload_dir):
    response = client.post(
        "/api/documents", files=[pdf("a.pdf"), pdf("b.pdf")], data={"parser": "pypdf"}
    )

    assert response.status_code == 202
    docs = response.json()["documents"]
    assert [d["filename"] for d in docs] == ["a.pdf", "b.pdf"]
    assert len({d["id"] for d in docs}) == 2
    assert len(stored_files(upload_dir)) == 2


def test_non_pdf_is_rejected_by_content_not_by_name(client, upload_dir):
    response = client.post(
        "/api/documents", files=[pdf("fake.pdf", b"just text")], data={"parser": "pypdf"}
    )

    assert response.status_code == 400
    assert "fake.pdf" in response.json()["detail"]
    assert client.get("/api/documents").json() == []
    assert stored_files(upload_dir) == []


def test_pdf_with_other_extension_is_accepted(client):
    response = client.post(
        "/api/documents", files=[pdf("scan.txt")], data={"parser": "pypdf"}
    )
    assert response.status_code == 202


def test_oversized_file_is_rejected(client, upload_dir):
    big = PDF_BYTES + b"0" * (1024 * 1024)  # limit is 1 MB in tests
    response = client.post("/api/documents", files=[pdf("big.pdf", big)], data={"parser": "pypdf"})

    assert response.status_code == 413
    assert "big.pdf" in response.json()["detail"]
    assert stored_files(upload_dir) == []


def test_one_bad_file_rejects_the_whole_request(client, upload_dir):
    response = client.post(
        "/api/documents",
        files=[pdf("good.pdf"), pdf("bad.pdf", b"nope")],
        data={"parser": "pypdf"},
    )

    assert response.status_code == 400
    assert client.get("/api/documents").json() == []
    assert stored_files(upload_dir) == []


def test_no_files_is_a_bad_request(client):
    response = client.post("/api/documents", data={"parser": "pypdf"})
    assert response.status_code == 400


def test_unknown_parser_is_unprocessable(client):
    response = client.post("/api/documents", files=[pdf()], data={"parser": "magic"})
    assert response.status_code == 422


def test_parser_without_key_is_rejected_before_any_file_is_saved(client, upload_dir):
    response = client.post("/api/documents", files=[pdf()], data={"parser": "gemini"})

    assert response.status_code == 400
    assert "GEMINI_API_KEY" in response.json()["detail"]
    assert stored_files(upload_dir) == []


def test_parser_with_key_is_accepted(make_client):
    client = make_client(gemini_api_key="test-key")
    response = client.post("/api/documents", files=[pdf()], data={"parser": "gemini"})
    assert response.status_code == 202


def test_get_unknown_document_is_404(client):
    assert client.get("/api/documents/nope").status_code == 404


def test_list_is_newest_first_and_has_no_pages(client):
    for name in ("first.pdf", "second.pdf", "third.pdf"):
        client.post("/api/documents", files=[pdf(name)], data={"parser": "pypdf"})

    listing = client.get("/api/documents").json()

    assert [d["filename"] for d in listing] == ["third.pdf", "second.pdf", "first.pdf"]
    assert all("pages" not in d for d in listing)


def test_list_is_empty_initially(client):
    assert client.get("/api/documents").json() == []


def test_filename_path_is_stripped(client):
    response = client.post(
        "/api/documents", files=[pdf("..\\..\\etc/passwd.pdf")], data={"parser": "pypdf"}
    )
    assert response.json()["documents"][0]["filename"] == "passwd.pdf"


def test_parsers_endpoint_reports_availability(client):
    parsers = {p["name"]: p for p in client.get("/api/parsers").json()}

    assert parsers["pypdf"]["available"] is True
    assert parsers["gemini"]["available"] is False
    assert "GEMINI_API_KEY" in parsers["gemini"]["reason"]
    assert parsers["gpt5mini"]["available"] is False
    assert "OpenAI key required" in parsers["gpt5mini"]["reason"]
