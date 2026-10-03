"""Turning a fetched web page into investigation text (Phase 12 §12).

The objective is not to reproduce the page. It is to hand Phase 2 a clean,
normalised string that a human would recognise as the page's content, with the
structural and stylistic noise removed and nothing invented.

## Why a hand-rolled parser rather than a library

`html.parser` from the standard library is used deliberately. The alternative —
BeautifulSoup or lxml — would add two dependencies, one of which compiles native
code, to produce the same result for the narrow job done here. The subset of
HTML that matters for extraction (text-bearing elements, the handful of
attributes worth keeping, and the elements whose *content* is not page text) is
small and completely understood, and parsing it here keeps the security surface
of the project unchanged.

Two properties of the parser matter more than completeness:

**It never executes anything.** The page is data. No `<script>` is run, no
stylesheet is applied, no markup is interpreted as instruction. §33 and §34 of
Phase 12 require that, and an extractor is the first place a hostile page could
otherwise reach something.

**It cannot be made to read the filesystem.** No entity beyond the five
predefined XML entities is expanded, no external resource is referenced, and no
URL is fetched. A page full of `<img src>` and `<link>` tags produces nothing but
inert text.

## What is kept, and what is dropped

Kept: the title, headings, paragraphs, list items, table cells, and anchor
text. Dropped: `<script>`, `<style>`, `<noscript>`, `<template>`, `<svg>`,
`<iframe>`, comments, and the entire subtree of any element that is not visible
by default (`hidden` attribute, `display:none`/`visibility:hidden` in an inline
style, `aria-hidden="true"`).

Comments are dropped because they are a well-known place to hide text that a
model will read and a human will not see.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from app.core.config import Settings
from app.schemas.url import WebsiteDocument

__all__ = ["WebsiteContentExtractor"]

#: Elements whose text is never page content.
#:
#: `<head>` is deliberately absent: it contains `<title>`, which is wanted. The
#: children inside it that could carry unwanted text (`script`, `style`) are
#: dropped in their own right, and the remaining head children are void.
_DROPPED_ELEMENTS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "math",
        "iframe",
        "object",
        "embed",
        "canvas",
        "audio",
        "video",
        "select",
        "option",
        "textarea",
    }
)

#: Elements that imply a line break in the extracted text.
_BLOCK_ELEMENTS: frozenset[str] = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "dd",
        "div", "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "ul",
    }
)

#: Elements whose text is metadata rather than body copy.
_METADATA_ELEMENTS: frozenset[str] = frozenset({"title"})

#: Elements that have no end tag and no content.
#:
#: These are recorded in the document flow but must never open a frame on the
#: parser's stack: the browser has no closing tag to balance them against, and
#: an unbalanced frame would leak drop/hidden state into the rest of the page.
_VOID_ELEMENTS: frozenset[str] = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)

#: Attributes that hide an element from a reader.
_HIDDEN_STYLE = re.compile(
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE
)

#: Runs of whitespace, collapsed at the end of extraction.
_WHITESPACE = re.compile(r"[ \t\x0b\f\r]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _VisibleTextParser(HTMLParser):
    """Collects visible text from an HTML document.

    Args:
        title_sink: Receives the document title, if any.
        description_sink: Receives the ``<meta name="description">`` value, if any.
        links_sink: Receives anchor text, used for social/platform context.
    """

    def __init__(
        self,
        title_sink: list[str],
        description_sink: list[str],
        links_sink: list[str],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._title_sink = title_sink
        self._description_sink = description_sink
        self._links_sink = links_sink

        self._parts: list[str] = []
        # One frame per *open* element, so an end tag can be matched to the
        # start tag that opened it. Void elements never push one: they have no
        # end tag, so pushing would leave the stack permanently unbalanced and
        # silently corrupt the drop/hidden counts for the rest of the document.
        self._stack: list[tuple[str, str]] = []
        self._drop_depth = 0
        self._hidden_depth = 0
        self._title_depth = 0

    # -- helpers -------------------------------------------------------

    @property
    def _suppressed(self) -> bool:
        """Whether current content is inside a dropped or hidden element."""
        return bool(self._drop_depth or self._hidden_depth)

    def _is_hidden(self, attrs: dict[str, str | None]) -> bool:
        """Report whether an element's attributes hide it from a reader."""
        if "hidden" in attrs:
            return True
        if (attrs.get("aria-hidden") or "").strip().lower() == "true":
            return True
        style = attrs.get("style") or ""
        return bool(_HIDDEN_STYLE.search(style))

    def _open(self, tag: str, attrs: dict[str, str | None]) -> None:
        """Record an opening element and emit any separator it implies.

        Args:
            tag: Lowercased tag name.
            attrs: Attribute mapping, keys already lowercased.
        """
        if tag in _DROPPED_ELEMENTS:
            self._stack.append((tag, "drop"))
            self._drop_depth += 1
            return

        if tag in _METADATA_ELEMENTS:
            self._stack.append((tag, "title"))
            self._title_depth += 1
            return

        if self._is_hidden(attrs):
            self._stack.append((tag, "hidden"))
            self._hidden_depth += 1
            return

        if tag == "a":
            href = attrs.get("href")
            if href:
                self._links_sink.append(href)

        self._stack.append((tag, "normal"))

        if tag in _BLOCK_ELEMENTS and not self._suppressed:
            self._parts.append("\n")

    # -- parser callbacks ----------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open an element."""
        lowered = tag.lower()
        mapped = {key.lower(): value for key, value in attrs}

        if lowered in _VOID_ELEMENTS:
            # A void element is processed for its content and metadata but does
            # not alter the nesting stack, because it can never be closed.
            if not self._suppressed and lowered in _BLOCK_ELEMENTS:
                self._parts.append("\n")
            if lowered == "meta":
                name = (mapped.get("name") or "").strip().lower()
                if name == "description" and mapped.get("content"):
                    self._description_sink.append(mapped.get("content") or "")
            return

        self._open(lowered, mapped)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle a self-closing element such as ``<br/>``."""
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        """Close an element.

        The matching frame is found by name. HTML in the wild closes elements
        they were never opened with, so an unmatched end tag is ignored rather
        than allowed to decrement a counter it does not own.
        """
        lowered = tag.lower()
        index = self._matching_frame_index(lowered)
        if index is None:
            return

        _, kind = self._stack.pop(index)
        if kind == "drop":
            self._drop_depth -= 1
        elif kind == "hidden":
            self._hidden_depth -= 1
        elif kind == "title":
            self._title_depth -= 1

        if lowered in _BLOCK_ELEMENTS and not self._suppressed and kind == "normal":
            self._parts.append("\n")

    def _matching_frame_index(self, tag: str) -> int | None:
        """Find the innermost open frame for a tag.

        Args:
            tag: Lowercased tag name from the end tag.

        Returns:
            Index into the stack, or ``None`` when nothing is open for it.
        """
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                return index
        return None

    def handle_data(self, data: str) -> None:
        """Receive character data."""
        if self._suppressed:
            return
        if self._title_depth:
            self._title_sink.append(data)
            return
        self._parts.append(data)

    def handle_comment(self, data: str) -> None:
        """Discard comments.

        Comments are a standard place to hide text that a model will read and a
        human will never see, so they are never part of the extracted text.
        """

    # -- result --------------------------------------------------------

    def text(self) -> str:
        """The accumulated body text, whitespace-normalised."""
        joined = "".join(self._parts)
        return normalize_whitespace(joined)


def normalize_whitespace(raw: str) -> str:
    """Collapse whitespace runs and strip blank-line piles.

    Args:
        raw: Text straight from the parser.

    Returns:
        Text with single spaces within a line, single newlines between blocks,
        and no leading or trailing blank lines.
    """
    without_tabs = raw.replace("\n", "\n")
    collapsed = _WHITESPACE.sub(" ", without_tabs)
    collapsed = re.sub(r" *\n *", "\n", collapsed)
    collapsed = _BLANK_LINES.sub("\n\n", collapsed)
    return collapsed.strip()


class WebsiteContentExtractor:
    """Converts a fetched page into investigation text.

    Attributes:
        settings: Provides the character budget applied to the result.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def extract(self, page: object) -> WebsiteDocument:
        """Build an investigation document from a fetched page.

        Args:
            page: A :class:`~app.services.url_fetch.FetchedPage`.

        Returns:
            The document, with its text already normalised and bounded.
        """
        # Imported here to avoid a circular import at module load: the fetch
        # service and this one are peers, and only this one needs the type.
        from app.services.url_fetch import FetchedPage

        assert isinstance(page, FetchedPage)

        title_parts: list[str] = []
        description_parts: list[str] = []
        link_hrefs: list[str] = []

        body = self._decode(page.body, page.charset)

        parser = _VisibleTextParser(title_parts, description_parts, link_hrefs)
        try:
            parser.feed(body)
            parser.close()
        except Exception:
            # A malformed document must not lose the text recovered before the
            # error: `HTMLParser` raises on some pathological input, and the
            # partial result is still the page's content.
            pass

        raw_text = parser.text()
        title = normalize_whitespace("".join(title_parts))
        description = normalize_whitespace("".join(description_parts)) or None

        budget = self._settings.url_extraction_max_chars
        truncated = len(raw_text) > budget
        text = raw_text[:budget] if truncated else raw_text

        # Social and platform links are identity context for an investor, and a
        # Telegram or WhatsApp link on an investment page is one of the existing
        # red-flag patterns. They are appended as text rather than interpreted.
        platform_links = [href for href in link_hrefs if _is_social_href(href)]
        if platform_links:
            links_block = "Links on page: " + ", ".join(dict.fromkeys(platform_links[:10]))
            text = f"{text}\n\n{links_block}" if text else links_block

        document = WebsiteDocument(
            source=page.source(page_title=title or None, meta_description=description),
            title=title,
            text=text,
            truncated=truncated,
            truncated_at=budget if truncated else None,
        )
        return document

    @staticmethod
    def _decode(body: bytes, declared_charset: str | None) -> str:
        """Decode a response body.

        An explicitly declared charset wins, but only when Python knows it; a
        page declaring something exotic falls back to UTF-8 and then to a
        permissive latin-1 decode, because refusing to read a page over its
        encoding label would be a worse failure than decoding it approximately.

        Args:
            body: Raw response bytes.
            declared_charset: The charset from the Content-Type header, if any.

        Returns:
            The decoded document.
        """
        if declared_charset:
            try:
                return body.decode(declared_charset, errors="replace")
            except LookupError:
                pass
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return body.decode("latin-1", errors="replace")


_SOCIAL_HOSTS: tuple[str, ...] = (
    "t.me",
    "telegram.me",
    "wa.me",
    "api.whatsapp.com",
    "whatsapp.com",
    "chat.whatsapp.com",
    "discord.gg",
    "discord.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
)


def _is_social_href(href: str) -> bool:
    """Report whether a link points at a messaging or social platform.

    Args:
        href: The link target as written.

    Returns:
        ``True`` when the link is a platform handle worth surfacing.
    """
    if not href:
        return False
    lowered = href.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    return any(host in lowered for host in _SOCIAL_HOSTS)


def build_analysis_text(document: WebsiteDocument) -> str:
    """Compose the string the existing pipeline analyses for a URL submission.

    The page's own text is the subject, but the URL and title are included ahead
    of it for two reasons. The ``SUSPICIOUS_URL`` rule is a pattern rule over
    text, so the submitted URL has to be present for it to be able to fire —
    that is reuse of the existing rule rather than a URL-specific duplicate. And
    a title that says "SEBI Registered Investment Advisory" is a claim a reader
    of the report needs to see.

    Args:
        document: The extracted document.

    Returns:
        Text for Phase 1 and Phase 2 to work on.
    """
    header = [f"Website: {document.source.final_url}"]
    if document.source.hostname:
        header.append(f"Domain: {document.source.hostname}")
    if document.title:
        header.append(f"Page title: {document.title}")
    if document.source.meta_description:
        header.append(f"Page description: {document.source.meta_description}")

    preamble = "\n".join(header)
    if not document.text.strip():
        return preamble
    return f"{preamble}\n\n{document.text}"