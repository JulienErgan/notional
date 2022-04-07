"""Utilities for parsing other document types in Notion.

These parsers provide an alternative to the "Import" cabilities of the Notion client
and Notion Web Clipper.  Perhaps that capability will be exposed to the API in the
future, which would effectively render these parsers unnecessary.
"""

# TODO add more options for callers to customize output
# TODO look for options to handle text styled using CSS
# TODO consider how to handle <form> content

import csv
import io
import logging
import re
from abc import ABC, abstractmethod
from os.path import basename

import html5lib

from . import blocks, schema, types
from .text import Annotations, TextObject, lstrip, rstrip, truncate

log = logging.getLogger(__name__)

# parse embedded image data
img_data_re = re.compile("^data:image/([^;]+);([^,]+),(.+)$")


def condense_text(text):
    if text is None:
        return None

    text = re.sub(r"\s+", " ", text, flags=re.MULTILINE)

    return text


def normalize_text(text):
    if text is None:
        return None

    text = text.strip()

    return condense_text(text)


def gather_text(elem):
    text = "".join(elem.itertext())
    return normalize_text(text)


def strip_text_block(block):
    """Remove leading and trailing whitespace from text in the given block."""

    if not isinstance(block, blocks.TextBlock):
        return

    if isinstance(block, blocks.Code):
        return

    block_text = block.__text__

    if block_text is not None and len(block_text) > 0:
        lstrip(block_text[0])
        rstrip(block_text[-1])


def elem_has_text(elem, with_children=True):

    # first, check the direct text of the element...
    if elem.text is not None and not elem.text.isspace():
        return True

    # now, we need to check the tail of each child...
    for child in elem:
        if with_children and elem_has_text(child):
            return True

        if child.tail is None:
            continue

        if not child.tail.isspace():
            return True

    return False


class DocumentParser(ABC):

    title: str
    content: list

    def __init__(self):
        self.title = None
        self.content = []

    @abstractmethod
    def parse(self, data):
        if hasattr(data, "name"):
            self.title = basename(data.name)


class CsvParser(DocumentParser):
    """A standard CSV parser."""

    schema: dict

    def __init__(self, header_row=True, title_column=0):
        super().__init__()

        self._has_header = header_row
        self._title_index = title_column

        self.schema = {}

        self._field_names = []

    def parse(self, data):
        super().parse(data)

        if isinstance(data, str):
            data = io.StringIO(data, newline="")

        reader = csv.reader(data)

        self._process(reader)

    def _process(self, csv):

        # build the schema based on the first row

        try:
            header = next(csv)
        except StopIteration:
            raise ValueError("Invalid CSV: empty data")

        if self._has_header:
            self._build_schema(*header)

        else:
            cols = [str(num) for num in range(len(header))]
            self._build_schema(*cols)
            self._build_record(*header)

        # process remaining entries

        for entry in csv:
            self._build_record(*entry)

    def _build_schema(self, *fields):
        if fields is None or len(fields) < 1:
            raise ValueError("Invalid CSV: empty header")

        column = 0

        for field in fields:
            field = field.strip()

            while field in self._field_names:
                field = f"{field}_{column}"

            if column == self._title_index:
                self.schema[field] = schema.Title()
            else:
                self.schema[field] = schema.RichText()

            self._field_names.append(field)

            column += 1

    def _build_record(self, *fields):
        if len(fields) != len(self._field_names):
            raise ValueError("Invalid CSV: incorrect number of fields in data")

        record = {}

        column = 0

        for col in self._field_names:
            value = fields[column]

            if column == self._title_index:
                record[col] = types.Title.from_value(value)
            else:
                record[col] = types.RichText.from_value(value)

            column += 1

        self.content.append(record)


class HtmlParser(DocumentParser):
    """An HTML parser that leverages the WHATWG HTML spec."""

    meta: dict

    def __init__(self, base=None):
        super().__init__()

        self._base_url = base

        self.meta = {}

        self._current_href = None
        self._current_text_style = Annotations()

    def parse(self, data):
        super().parse(data)

        doc = html5lib.parse(data, namespaceHTMLElements=False)

        self._render(doc)

    def _render(self, elem, parent=None):
        log.debug("rendering element - %s :: %s", elem.tag, type(parent))

        if parent is None:
            parent = self.content

        if hasattr(self, f"_render_{elem.tag}"):
            log.debug("handler func -- _render_%s", elem.tag)
            pfunc = getattr(self, f"_render_{elem.tag}")
            pfunc(elem, parent)

        log.debug("block complete; %d total block(s)", len(self.content))

    def _render_a(self, elem, parent):
        self._current_href = elem.get("href")
        self._process_contents(elem, parent=parent)
        self._current_href = None

    def _render_b(self, elem, parent):
        self._current_text_style.bold = True
        self._process_contents(elem, parent=parent)
        self._current_text_style.bold = False

    def _render_base(self, elem, parent):
        base = elem.get("href")
        if base is not None:
            self._base_url = base

    def _render_blockquote(self, elem, parent):
        block = blocks.Quote()
        self._process_contents(elem, parent=block)
        parent.append(block)

    def _render_body(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_br(self, elem, parent):
        if isinstance(parent, blocks.TextBlock):
            parent.concat("\n")

    def _render_code(self, elem, parent):
        self._current_text_style.code = True
        self._process_contents(elem, parent=parent)
        self._current_text_style.code = False

    def _render_dd(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_del(self, elem, parent):
        self._current_text_style.strikethrough = True
        self._process_contents(elem, parent=parent)
        self._current_text_style.strikethrough = False

    def _render_div(self, elem, parent):
        self._process_contents(elem, parent)

    def _render_dl(self, elem, parent):
        dl = blocks.Paragraph()
        self._process_contents(elem, parent=dl)
        parent.append(dl)

    def _render_dt(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_em(self, elem, parent):
        self._render_i(elem, parent)

    def _render_h1(self, elem, parent):
        h1 = blocks.Heading1()
        self._process_contents(elem, parent=h1)
        parent.append(h1)

    def _render_h2(self, elem, parent):
        h2 = blocks.Heading2()
        self._process_contents(elem, parent=h2)
        parent.append(h2)

    def _render_h3(self, elem, parent):
        h3 = blocks.Heading3()
        self._process_contents(elem, parent=h3)
        parent.append(h3)

    def _render_h4(self, elem, parent):
        self._render_h3(elem, parent)

    def _render_h5(self, elem, parent):
        self._render_h3(elem, parent)

    def _render_h6(self, elem, parent):
        self._render_h3(elem, parent)

    def _render_head(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_hgroup(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_hr(self, elem, parent):
        parent.append(blocks.Divider())

    def _render_html(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_i(self, elem, parent):
        self._current_text_style.italic = True
        self._process_contents(elem, parent=parent)
        self._current_text_style.italic = False

    def _render_iframe(self, elem, parent):
        src = elem.get("src")
        if src is not None:
            block = blocks.Embed.from_url(src)
            parent.append(block)

    def _render_img(self, elem, parent):
        src = elem.get("src")

        # TODO use self._base_url for relative paths
        # TODO support embedded images (data:image) as HostedFile...

        if src is not None:
            file = types.ExternalFile.from_url(src)
            img = blocks.Image(image=file)

            parent.append(img)

    def _render_ins(self, elem, parent):
        self._render_u(elem, parent)

    def _render_kbd(self, elem, parent):
        self._render_code(elem, parent)

    def _render_li(self, elem, parent):
        self._process_contents(elem, parent)

    def _render_menu(self, elem, parent):
        self._render_ul(elem, parent)

    def _render_meta(self, elem, parent):
        name = elem.get("name")
        value = elem.get("content")
        if name and value:
            self.meta[name] = value

    def _render_object(self, elem, parent):
        # XXX support 'data' attribute as an embed or upload?
        self._process_contents(elem, parent=parent)

    def _render_ol(self, elem, parent):
        self._process_list(elem, parent, blocks.NumberedListItem)

    def _render_p(self, elem, parent):
        para = blocks.Paragraph()
        self._process_contents(elem, parent=para)
        parent.append(para)

    def _render_pre(self, elem, parent):
        block = blocks.Code()
        self._process_contents(elem, parent=block)
        parent.append(block)

    def _render_s(self, elem, parent):
        self._render_del(self, elem, parent)

    def _render_samp(self, elem, parent):
        self._render_code(elem, parent)

    def _render_span(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_strike(self, elem, parent):
        self._render_del(elem, parent)

    def _render_strong(self, elem, parent):
        self._render_b(elem, parent)

    def _render_table(self, elem, parent):
        table = blocks.Table()
        self._process_contents(elem, parent=table)

        if table.Width > 0:
            parent.append(table)

    def _render_tbody(self, elem, parent):
        self._process_contents(elem, parent)

    def _render_td(self, elem, parent):
        if not isinstance(parent, blocks.TableRow):
            raise TypeError("Invalid parent for <td>")

        if elem_has_text(elem):
            self._process_contents(elem, parent=parent)
        else:
            self._append_text("", parent)

    def _render_tfoot(self, elem, parent):
        self._process_contents(elem, parent=parent)

    def _render_th(self, elem, parent):
        self._render_td(elem, parent=parent)

    def _render_thead(self, elem, parent):
        if not isinstance(parent, blocks.Table):
            raise TypeError("Invalid parent for <thead>")

        parent.table.has_column_header = True
        self._process_contents(elem, parent=parent)

    def _render_title(self, elem, parent):
        self.title = gather_text(elem)

    def _render_tr(self, elem, parent):
        if not isinstance(parent, blocks.Table):
            raise TypeError("Invalid parent for <tr>")

        row = blocks.TableRow()
        for td in elem.findall("td"):
            self._render(td, parent=row)

        parent.append(row)

    def _render_tt(self, elem, parent):
        self._render_pre(elem, parent=parent)

    def _render_u(self, elem, parent):
        self._current_text_style.underline = True
        self._process_contents(elem, parent=parent)
        self._current_text_style.underline = False

    def _render_ul(self, elem, parent):
        self._process_list(elem, parent, blocks.BulletedListItem)

    def _render_var(self, elem, parent):
        self._render_code(elem, parent=parent)

    def _append_text(self, text, parent):
        log.debug("appending text :: %s => '%s'", parent.type, truncate(text, 10))

        if not isinstance(parent, blocks.Code):
            text = condense_text(text)

        style = self._current_text_style.dict()
        href = self._current_href
        obj = TextObject.from_value(text, href=href, **style)

        if isinstance(parent, blocks.TextBlock):
            if obj is not None:
                parent.concat(obj)

        elif isinstance(parent, blocks.TableRow):
            parent.append(obj)

    def _process_contents(self, elem, parent):
        log.debug("processing contents :: %s %s", elem.tag, type(parent))

        # empty elements don't need text processing...
        if not elem_has_text(elem, with_children=False):
            has_text = False

        # TextBlock's can hold text directly...
        elif isinstance(parent, blocks.TextBlock):
            has_text = True

        # so can TableRow's...
        elif isinstance(parent, blocks.TableRow):
            has_text = True

        # otherwise, we need a new parent to hold text...
        else:
            has_text = True
            new_parent = blocks.Paragraph()
            parent.append(new_parent)
            parent = new_parent

        if has_text:
            self._append_text(elem.text, parent)

        for child in elem:
            self._render(child, parent)

            if has_text:
                self._append_text(child.tail, parent)

        if isinstance(parent, blocks.TextBlock):
            strip_text_block(parent)

    def _process_list(self, elem, parent, kind):
        list_parent = parent

        for child in elem:
            if child.tag == "li":
                list_parent = kind()
                self._render(child, parent=list_parent)
                parent.append(list_parent)
            else:
                self._render(child, list_parent)

    def _process_img_data(self, elem):
        import base64
        import tempfile

        log.debug("processing image")

        # TODO this probably needs more error handling and better flow

        img_src = elem["src"]
        m = img_data_re.match(img_src)

        if m is None:
            raise ValueError("Image data missing")

        img_type = m.groups()[0]
        img_data_enc = m.groups()[1]
        img_data_str = m.groups()[2]

        log.debug("decoding embedded image: %s [%s]", img_type, img_data_enc)

        if img_data_enc == "base64":
            log.debug("decoding base64 image: %d bytes", len(img_data_str))
            img_data_b64 = img_data_str.encode("ascii")
            img_data = base64.b64decode(img_data_b64)
        else:
            raise ValueError(f"Unsupported img encoding: {img_data_enc}")

        log.debug("preparing %d bytes for image upload", len(img_data))

        with tempfile.NamedTemporaryFile(suffix=f".{img_type}") as fp:
            log.debug("using temporary file: %s", fp.name)
            fp.write(img_data)

            # TODO upload the image to Notion
