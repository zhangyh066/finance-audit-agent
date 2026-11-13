import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

from .config import MAX_UPLOAD_BYTES
from .db import audit, dumps, new_id, now_iso
from .ocr import windows_ocr

INVOICE_ALIASES = {
    "费用类型": "expense_type", "费用类别": "expense_type", "报销类型": "expense_type",
    "部门": "department", "成本中心": "cost_center_code", "金额": "amount", "报销金额": "amount", "价税合计": "amount",
    "开票日期": "invoice_date", "发票日期": "invoice_date", "发票代码": "invoice_code", "发票号码": "invoice_number",
    "销售方": "seller_name", "销售方名称": "seller_name", "购买方": "buyer_name", "购买方名称": "buyer_name",
    "城市": "city", "消费城市": "city", "事由": "description", "说明": "description", "备注": "description",
    "expensetype": "expense_type", "department": "department", "costcenter": "cost_center_code",
    "amount": "amount", "invoicedate": "invoice_date", "invoicecode": "invoice_code", "invoicenumber": "invoice_number",
    "seller": "seller_name", "buyer": "buyer_name", "sellername": "seller_name", "buyername": "buyer_name",
    "costcentercode": "cost_center_code", "city": "city", "description": "description",
}


def _key(value):
    normalized = re.sub(r"[\s_\-]", "", str(value).strip())
    return normalized.replace("（小写）", "").replace("(小写)", "").lower()


ALIASES = {_key(key): value for key, value in INVOICE_ALIASES.items()}


def _decode(raw):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本仅支持 UTF-8 或 GB18030 编码")


def _mineru_text(data):
    content = data.get("content_list") if isinstance(data, dict) else None
    if isinstance(content, dict):
        content = content.get("data") or content.get("content") or []
    if isinstance(content, list):
        chunks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            for field in ("text", "content", "table_body"):
                if block.get(field):
                    chunks.append(str(block[field]))
                    break
        if chunks:
            return "\n".join(chunks)
    chunks = []
    def walk(value):
        if isinstance(value, dict):
            if "spans" in value and isinstance(value["spans"], list):
                for span in value["spans"]:
                    if isinstance(span, dict):
                        text = span.get("text") or span.get("content")
                        if text:
                            chunks.append(str(text))
                return
            for key in ("text", "content"):
                if value.get(key) and not any(k in value for k in ("lines", "blocks", "spans", "para_blocks", "preproc_blocks")):
                    chunks.append(str(value[key]))
                    return
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    if isinstance(data, dict) and "content_list" not in data:
        for field in ("pdf_info", "preproc_blocks", "para_blocks"):
            if field in data:
                walk(data[field])
                break
    return "\n".join(chunks)


def extract_text(filename, raw):
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json"}:
        text = _decode(raw)
        parser = "plain-text"
        if suffix == ".json":
            data = json.loads(text)
            is_mineru = isinstance(data, dict) and any(key in data for key in ("pdf_info", "content_list", "preproc_blocks", "para_blocks"))
            if is_mineru:
                text = _mineru_text(data)
            parser = "MinerU JSON" if is_mineru else "JSON"
        return text, parser
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise ValueError("DOCX 解析需要 python-docx") from exc
        doc = Document(io.BytesIO(raw))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            lines.extend("\t".join(cell.text for cell in row.cells) for row in table.rows)
        return "\n".join(lines), "python-docx"
    if suffix == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise ValueError("PDF 解析需要 PyMuPDF") from exc
        with fitz.open(stream=raw, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
            if len(text.strip()) >= 30:
                return text, "PyMuPDF"
            if not len(doc):
                raise ValueError("PDF 没有页面")
            image = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invoice.png"
            path.write_bytes(image)
            result = windows_ocr(path)
        return result["text"], "PyMuPDF + Windows.Media.Ocr（首页）"
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("upload" + suffix)
            path.write_bytes(raw)
            result = windows_ocr(path)
        return result["text"], "Windows.Media.Ocr"
    raise ValueError("仅支持 TXT/MD/CSV/JSON/DOCX/PDF 或常见图片格式")


def invoice_fields(text, filename, raw):
    data = {}
    if Path(filename).suffix.lower() == ".json" and not any(key in json.loads(_decode(raw)) for key in ("pdf_info", "content_list", "preproc_blocks", "para_blocks")):
        source = json.loads(_decode(raw))
        if isinstance(source, dict) and "invoices" in source:
            if len(source["invoices"]) != 1:
                raise ValueError("单次只导入一张单据")
            source = source["invoices"][0]
        if isinstance(source, dict) and "invoice" in source:
            source = source["invoice"]
        if not isinstance(source, dict):
            raise ValueError("JSON 单据应为字段对象")
        data = source
    elif Path(filename).suffix.lower() == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        if len(rows) != 1:
            raise ValueError("CSV 单据文件应只有一行数据")
        data = rows[0]
    else:
        data = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            match = re.match(r"^\s*([^:：]{2,40})\s*[:：]\s*(.*?)\s*$", line)
            if match:
                value = match.group(2)
                if not value and index + 1 < len(lines) and not re.search(r"[:：]", lines[index + 1]):
                    value = lines[index + 1]
                data[match.group(1)] = value
    fields = {}
    for key, value in data.items():
        field = ALIASES.get(_key(key))
        if field and value is not None:
            cleaned = str(value).strip()
            if field == "description":
                cleaned = re.sub(r"\s+", " ", cleaned)
            else:
                cleaned = re.sub(r"\s+", "", cleaned)
            if field == "invoice_date":
                cleaned = cleaned.replace("．", ".").replace("。", ".")
                cleaned = re.sub(r"(?<=\d)[一—–](?=\d)", "-", cleaned)
            if field == "amount":
                cleaned = cleaned.replace("．", ".")
            fields[field] = cleaned
    if not fields:
        raise ValueError("没有识别出已支持的字段，请使用带字段标签的文本或示例格式")
    return fields


def parse_document(filename, raw, kind):
    if not filename or Path(filename).name != filename:
        raise ValueError("文件名不合法")
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("文件不能为空，且不能超过 2MB")
    text, parser = extract_text(filename, raw)
    fields = invoice_fields(text, filename, raw) if kind == "invoice" else {}
    if not text.strip():
        raise ValueError("文件没有可识别的文本")
    return {"filename": filename, "text": text, "fields": fields, "parser": parser,
            "sha256": hashlib.sha256(raw).hexdigest()}


def store_document(conn, parsed, kind, uploader_id):
    document_id = new_id()
    conn.execute("""
        INSERT INTO documents(id, uploader_id, kind, filename, sha256, extracted_text, fields_json, parser, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (document_id, uploader_id, kind, parsed["filename"], parsed["sha256"], parsed["text"],
          dumps(parsed["fields"]), parsed["parser"], now_iso()))
    audit(conn, uploader_id, "document.imported", detail={"document_id": document_id, "kind": kind,
          "filename": parsed["filename"], "sha256": parsed["sha256"], "parser": parsed["parser"], "original_file_stored": False})
    return document_id


def confirm_document(conn, document_id, values, user):
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise LookupError("单据文件不存在")
    if row["uploader_id"] != user["id"]:
        raise PermissionError("只能确认本人导入的文件")
    from datetime import date
    from .money import to_cents
    before = json.loads(row["fields_json"])
    after = {}
    for key, value in values.items():
        field = ALIASES.get(_key(key))
        if field:
            after[field] = str(value if value is not None else "").strip()
    if "amount" in after and after["amount"]:
        to_cents(after["amount"])
    if after.get("invoice_date"):
        date.fromisoformat(after["invoice_date"])
    changed = {key: {"before": before.get(key), "after": value} for key, value in after.items() if before.get(key) != value}
    conn.execute("UPDATE documents SET fields_json = ? WHERE id = ?", (dumps(after), document_id))
    audit(conn, user["id"], "document.confirmed", detail={"document_id": document_id,
          "changed_fields": changed, "original_text_retained": True, "confirmation": "用户确认后的字段，不代表 OCR 自动校验通过"})
    return after
