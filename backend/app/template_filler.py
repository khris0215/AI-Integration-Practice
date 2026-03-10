import re
import io
import logging
import pdfrw
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


def _iter_all_paragraphs(doc: Document):
    for paragraph in doc.paragraphs:
        yield paragraph

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield paragraph

    for section in doc.sections:
        for paragraph in section.header.paragraphs:
            yield paragraph
        for table in section.header.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        yield paragraph

        for paragraph in section.footer.paragraphs:
            yield paragraph
        for table in section.footer.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        yield paragraph


def _normalize_label_to_field(label: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', label.strip().lower())
    clean = re.sub(r'\s+', ' ', clean).strip()

    alias_map = {
        "incident id": "incident_id",
        "incident number": "incident_id",
        "date": "date",
        "type": "type",
        "incident type": "type",
        "description": "description",
        "impact": "impact",
        "actions taken": "actions_taken",
        "action taken": "actions_taken",
        "reporter name": "reporter_name",
        "reported by": "reporter_name",
        "amount lost": "amount_lost",
        "loss amount": "amount_lost",
        "currency": "currency",
        "evidence list": "evidence_list",
    }
    if clean in alias_map:
        return alias_map[clean]
    return re.sub(r'\s+', '_', clean)


def _non_empty_data_values(data: dict):
    preferred_order = [
        "incident_id",
        "date",
        "type",
        "description",
        "impact",
        "actions_taken",
        "reporter_name",
        "amount_lost",
        "currency",
        "evidence_list",
    ]
    values = []
    seen = set()
    for key in preferred_order + list(data.keys()):
        if key in seen:
            continue
        seen.add(key)
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and len(value) == 0:
            continue
        values.append(str(value))
    return values


def detect_checkbox(paragraph):
    """
    Detect if a paragraph contains a checkbox symbol.
    Returns True if it contains common checkbox characters.
    """
    checkbox_pattern = r'[☐☑☒□]|\[\s*\]|\(\s*\)'
    return re.search(checkbox_pattern, paragraph.text) is not None


def get_checkbox_state(paragraph, value):
    """
    Given a paragraph and a boolean value (True if should be checked),
    replace the first checkbox symbol with a checked symbol.
    For simplicity, we replace □ with ☑ or ☒, and [ ] with [X].
    """
    text = paragraph.text
    # Replace first occurrence of □ with ☑ if value True, else leave as □
    if '□' in text:
        new_text = text.replace('□', '☑', 1) if value else text
    elif '[ ]' in text:
        new_text = text.replace('[ ]', '[X]', 1) if value else text
    elif '☐' in text:
        new_text = text.replace('☐', '☑', 1) if value else text
    else:
        new_text = text
    paragraph.text = new_text


def fill_docx_heuristic(template_bytes: bytes, data: dict) -> bytes:
    """
    Replace blanks (______) and checkboxes in a DOCX with values from data.
    Returns filled document as bytes.
    """
    try:
        doc = Document(io.BytesIO(template_bytes))

        # Blank pattern: at least 3 underscores
        blank_pattern = re.compile(r'_{3,}')

        replacements = 0

        def apply_to_paragraph(paragraph, sequential_values=None, sequential_index=None):
            nonlocal replacements
            text = paragraph.text

            # Handle blanks
            if blank_pattern.search(text):
                # Split on the blank to get the part before it
                parts = blank_pattern.split(text)
                if parts:
                    label = parts[0].strip().rstrip(':').strip()
                    field_name = _normalize_label_to_field(label)
                    value = data.get(field_name, '')
                    if value is None:
                        value = ''
                    if str(value).strip():
                        # Replace the first blank with the value
                        new_text = blank_pattern.sub(str(value), text, count=1)
                        paragraph.text = new_text
                        replacements += 1
                        text = paragraph.text
                    elif sequential_values is not None and sequential_index is not None:
                        if sequential_index[0] < len(sequential_values):
                            fallback_value = sequential_values[sequential_index[0]]
                            sequential_index[0] += 1
                            new_text = blank_pattern.sub(str(fallback_value), text, count=1)
                            paragraph.text = new_text
                            replacements += 1
                            text = paragraph.text

            # Handle checkboxes (simplified: checkbox line matches incident type text)
            if detect_checkbox(paragraph):
                if 'type' in data and data['type'] and str(data['type']).lower() in text.lower():
                    get_checkbox_state(paragraph, True)

        all_paragraphs = list(_iter_all_paragraphs(doc))
        for paragraph in all_paragraphs:
            apply_to_paragraph(paragraph)

        # If no labeled replacements happened, try a sequential fallback so blanks are not left empty.
        if replacements == 0:
            seq_values = _non_empty_data_values(data)
            seq_index = [0]
            for paragraph in all_paragraphs:
                apply_to_paragraph(paragraph, seq_values, seq_index)

        if replacements == 0:
            doc.add_paragraph(
                "Warning: No fillable blanks or checkbox matches were detected in this template. "
                "Template may use unsupported structures (for example text boxes or content controls)."
            )

        logger.info("DOCX heuristic fill replacements: %s", replacements)

        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        logger.exception("Failed heuristic DOCX fill: %s", e)
        raise


def fill_pdf_form(template_bytes: bytes, data: dict) -> bytes:
    """
    Fill a PDF form with data.
    Expects field names in the PDF to match keys in data (case-insensitive).
    """
    try:
        pdf = pdfrw.PdfReader(fdata=template_bytes)
        for page in pdf.pages:
            annotations = page.get('/Annots')
            if annotations:
                for annotation in annotations:
                    field = annotation.get('/T')
                    if field:
                        field_name = field[1:-1]  # remove parentheses
                        # Try to find matching data (case-insensitive)
                        for key, value in data.items():
                            if key.lower() == field_name.lower():
                                annotation.update(
                                    pdfrw.PdfDict(V=pdfrw.PdfString(str(value)))
                                )
        output = io.BytesIO()
        pdfrw.PdfWriter().write(output, pdf)
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        logger.exception("Failed PDF form fill: %s", e)
        raise


def fill_docx_with_mapping(template_bytes: bytes, data: dict, mapping: dict) -> bytes:
    try:
        doc = Document(io.BytesIO(template_bytes))
        blank_pattern = re.compile(r'_{3,}')
        normalized_mapping = {str(k).strip().lower(): v for k, v in (mapping or {}).items()}

        def apply_to_paragraph(paragraph):
            if not blank_pattern.search(paragraph.text):
                return
            parts = blank_pattern.split(paragraph.text)
            if not parts:
                return
            label = parts[0].strip().rstrip(':').strip()
            field_name = normalized_mapping.get(label.lower())
            if field_name and field_name in data and field_name != "unknown":
                value = data[field_name] or ''
                new_text = blank_pattern.sub(str(value), paragraph.text, count=1)
                paragraph.text = new_text

        for paragraph in _iter_all_paragraphs(doc):
            apply_to_paragraph(paragraph)

        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        logger.exception("Failed mapping-based DOCX fill: %s", e)
        raise
