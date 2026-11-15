import asyncio
from pathlib import Path

from fastapi import UploadFile

from app.errors import UploadError

PDF_SIGNATURE = b"%PDF-"
CHUNK_SIZE = 1024 * 1024


def display_name(file: UploadFile) -> str:
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:255] or "document.pdf"


async def save_pdf(file: UploadFile, dest: Path, max_bytes: int) -> None:
    """Copy an upload to `dest`, rejecting non-PDFs and oversized files.

    The type is checked by content (the `%PDF-` signature), not by file name.
    The size limit is enforced while copying. Nothing is left behind on failure.
    """
    name = display_name(file)
    head = await file.read(len(PDF_SIGNATURE))
    if head != PDF_SIGNATURE:
        raise UploadError(400, f"{name}: not a PDF file")

    dest.parent.mkdir(parents=True, exist_ok=True)
    written = len(head)
    try:
        with dest.open("wb") as out:
            out.write(head)
            while chunk := await file.read(CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    limit_mb = max_bytes // (1024 * 1024)
                    raise UploadError(413, f"{name}: file is larger than {limit_mb} MB")
                await asyncio.to_thread(out.write, chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
