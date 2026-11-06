"""Bounded, defensive extraction of user-provided document attachments.

All extracted text is untrusted reference data. Archives are inspected in memory;
no archive paths are written to disk.
"""
import base64
import io
import json
import mimetypes
import re
import zipfile
from pathlib import PurePosixPath

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 24 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100
MAX_TEXT_CHARS = 12_000
MAX_IMAGE_BYTES = 1024 * 1024
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".json", ".csv", ".log", ".yaml", ".yml", ".toml", ".xml", ".sql",
    ".sh", ".ps1", ".ini", ".cfg", ".diff", ".rst", ".env", ".bat",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}


class AttachmentError(ValueError):
    pass


def _decode_text(data, name):
    for encoding in ("utf-8-sig", "utf-16", "cp1256", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttachmentError(f"فایل متنی {name} با رمزگذاری پشتیبانی‌شده قابل خواندن نیست.")


def _extract_pdf(data, name):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise AttachmentError("برای خواندن PDF، وابستگی pypdf نصب نیست.") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if reader.decrypt("") == 0:
                    return f"[PDF رمزگذاری‌شده است و بدون گذرواژه قابل خواندن نیست: {name}]"
            except Exception:
                return f"[PDF رمزگذاری‌شده است و بدون گذرواژه قابل خواندن نیست: {name}]"
        pages = []
        for number, page in enumerate(reader.pages[:100], 1):
            pages.append(f"[صفحه {number}]\n{page.extract_text() or '[متن قابل استخراجی در این صفحه پیدا نشد؛ ممکن است اسکن تصویری باشد.]'}")
        return "\n\n".join(pages)
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError(f"خواندن PDF {name} ناموفق بود: {str(exc)[:180]}") from exc


def _extract_docx(data, name):
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        chunks = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables[:30]:
            for row in table.rows[:100]:
                chunks.append(" | ".join(cell.text.replace("\n", " / ") for cell in row.cells))
        return "\n".join(chunks)
    except ImportError as exc:
        raise AttachmentError("برای خواندن DOCX، وابستگی python-docx نصب نیست.") from exc
    except Exception as exc:
        raise AttachmentError(f"خواندن DOCX {name} ناموفق بود: {str(exc)[:180]}") from exc


def _extract_xlsx(data, name):
    try:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        chunks = []
        for sheet in book.worksheets[:30]:
            chunks.append(f"[Sheet: {sheet.title}]")
            for row in sheet.iter_rows(max_row=300, max_col=40, values_only=True):
                values = [str(v) if v is not None else "" for v in row]
                if any(values):
                    chunks.append(" | ".join(values))
        book.close()
        return "\n".join(chunks)
    except ImportError as exc:
        raise AttachmentError("برای خواندن XLSX، وابستگی openpyxl نصب نیست.") from exc
    except Exception as exc:
        raise AttachmentError(f"خواندن XLSX {name} ناموفق بود: {str(exc)[:180]}") from exc


def _extract_pptx(data, name):
    try:
        from pptx import Presentation
        deck = Presentation(io.BytesIO(data))
        chunks = []
        for number, slide in enumerate(deck.slides[:100], 1):
            chunks.append(f"[اسلاید {number}]")
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text.strip():
                    chunks.append(shape.text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        chunks.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(chunks)
    except ImportError as exc:
        raise AttachmentError("برای خواندن PPTX، وابستگی python-pptx نصب نیست.") from exc
    except Exception as exc:
        raise AttachmentError(f"خواندن PPTX {name} ناموفق بود: {str(exc)[:180]}") from exc


def _image_payload(data, name):
    if len(data) > MAX_IMAGE_BYTES:
        return None, f"[تصویر {name} به‌دلیل بزرگ‌بودن بیش از ۵ مگابایت برای تحلیل تصویری ارسال نشد.]"
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        mime = mimetypes.guess_type(name)[0] or "image/jpeg"
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            mime = "image/jpeg"
    except ImportError:
        raise AttachmentError("برای اعتبارسنجی تصویر، وابستگی Pillow نصب نیست.")
    except Exception as exc:
        raise AttachmentError(f"فایل تصویر {name} معتبر نیست: {str(exc)[:120]}") from exc
    return {"name": name, "mime_type": mime, "data": base64.b64encode(data).decode("ascii")}, f"[تصویر پیوست‌شده: {name}؛ برای تحلیل بصری به مدل بینایی نیاز است.]"


def _pdf_page_images(data, name):
    try:
        import fitz
        from PIL import Image
        document = fitz.open(stream=data, filetype="pdf")
        images = []
        for index, page in enumerate(document[:4]):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
            raw = pixmap.tobytes("png")
            with Image.open(io.BytesIO(raw)) as source:
                source.thumbnail((1400, 1400))
                output = io.BytesIO()
                source.convert("RGB").save(output, format="JPEG", quality=72, optimize=True)
            if output.tell() <= MAX_IMAGE_BYTES:
                images.append({"name": f"{name} — page {index + 1}", "mime_type": "image/jpeg",
                               "data": base64.b64encode(output.getvalue()).decode("ascii")})
        document.close()
        return images
    except Exception:
        return []


def _extract_one(data, name):
    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return _decode_text(data, name), []
    if suffix == ".pdf":
        text = _extract_pdf(data, name)
        plain_text = re.sub(r"\[صفحه \d+\]\s*|\[متن قابل استخراجی در این صفحه پیدا نشد؛ ممکن است اسکن تصویری باشد\.\]", "", text).strip()
        images = _pdf_page_images(data, name) if len(plain_text) < 40 else []
        if images:
            text += "\n[صفحات PDF اسکن‌شده به‌صورت تصویر نیز برای مدل بینایی پیوست شده‌اند.]"
        return text, images
    if suffix == ".docx":
        return _extract_docx(data, name), []
    if suffix == ".xlsx":
        return _extract_xlsx(data, name), []
    if suffix == ".pptx":
        return _extract_pptx(data, name), []
    if suffix in IMAGE_EXTENSIONS:
        image, note = _image_payload(data, name)
        return note, [image] if image else []
    raise AttachmentError(f"فرمت فایل {name} پشتیبانی نمی‌شود.")


def extract_attachment(name, data):
    if not isinstance(name, str) or not name or len(name) > 240:
        raise AttachmentError("نام فایل معتبر نیست.")
    if not isinstance(data, bytes) or not data or len(data) > MAX_FILE_BYTES:
        raise AttachmentError("حداکثر اندازهٔ هر فایل ۱۲ مگابایت است.")
    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if suffix != ".zip":
        text, images = _extract_one(data, name)
        return {"name": name, "text": text[:MAX_TEXT_CHARS], "images": images, "truncated": len(text) > MAX_TEXT_CHARS}

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise AttachmentError(f"فایل ZIP معتبر نیست: {str(exc)[:120]}") from exc
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise AttachmentError("آرشیو ZIP بیش از ۱۰۰ عضو دارد؛ آن را کوچک‌تر کنید.")
    total = sum(item.file_size for item in entries if not item.is_dir())
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise AttachmentError("حجم بازشدهٔ ZIP بیش از سقف ۲۴ مگابایت است.")
    chunks, images = [], []
    consumed = 0
    for item in entries:
        if item.is_dir():
            continue
        path = PurePosixPath(item.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or item.flag_bits & 1:
            chunks.append(f"[عضو ناامن یا رمزگذاری‌شده نادیده گرفته شد: {item.filename[:160]}]")
            continue
        if item.file_size > MAX_FILE_BYTES:
            chunks.append(f"[فایل بزرگ نادیده گرفته شد: {item.filename[:160]}]")
            continue
        ext = path.suffix.lower()
        if ext not in TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS:
            continue
        try:
            raw = archive.read(item)
            consumed += len(raw)
            if consumed > MAX_TOTAL_UNCOMPRESSED:
                raise AttachmentError("حجم دادهٔ استخراج‌شده از ZIP از سقف مجاز عبور کرد.")
            extracted, found_images = _extract_one(raw, str(path))
            chunks.append(f"--- FILE: {path.as_posix()} ---\n{extracted[:12000]}")
            images.extend(found_images[:4 - len(images)])
        except (AttachmentError, RuntimeError, OSError) as exc:
            chunks.append(f"[استخراج {path.name} ناموفق بود: {str(exc)[:180]}]")
        if sum(map(len, chunks)) >= MAX_TEXT_CHARS:
            chunks.append("[ادامهٔ محتوای ZIP به‌دلیل محدودیت حجم متن حذف شد.]")
            break
    return {
        "name": name,
        "text": ("\n\n".join(chunks) or "[در ZIP فایل قابل‌خواندن پشتیبانی‌شده‌ای پیدا نشد.]")[:MAX_TEXT_CHARS],
        "images": images,
        "truncated": sum(map(len, chunks)) > MAX_TEXT_CHARS,
    }
