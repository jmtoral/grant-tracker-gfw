import copy

import pymupdf
import pytest
from conftest import SAMPLES

from src import config
from src.ingestion import OCR_REQUIRED, IngestionError, parse_document

S = config.settings()


@pytest.mark.parametrize("name,expected", list(zip(SAMPLES, [("pdf", 3), ("docx", 4), ("txt", 3)])))
def test_three_formats_produce_pages(name, expected):
    doc = parse_document(name, (config.SAMPLES / name).read_bytes(), S)
    assert (doc.file_type, len(doc.pages)) == expected
    assert all(p.strip() for p in doc.pages) and len(doc.document_id) == 64


def test_pdf_without_text_requires_ocr():
    pdf = pymupdf.open()
    pdf.new_page()
    with pytest.raises(IngestionError, match=OCR_REQUIRED):
        parse_document("scan.pdf", pdf.tobytes(), S)


@pytest.mark.parametrize("name,data", [
    ("tool.exe", b"MZ\x90\x00"),              # extension not allowed
    ("fake.pdf", b"PK\x03\x04not a pdf"),     # magic bytes do not match
    ("fake.docx", b"%PDF-1.7 not a docx"),
    ("bad.txt", b"\xff\xfe\x00 not utf-8"),
])
def test_invalid_upload_rejected(name, data):
    with pytest.raises(IngestionError):
        parse_document(name, data, S)


def test_oversized_upload_rejected():
    small = copy.deepcopy(S)
    small["ingestion"]["max_upload_mb"] = 0.001
    with pytest.raises(IngestionError, match="larger"):
        parse_document("big.txt", b"x" * 2000, small)
