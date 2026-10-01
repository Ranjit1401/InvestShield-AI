"""Tests for URL/domain utilities (Phase 3, §7).

Every answer to "what host published this?" in the product comes from
`extract_domain`. These tests fix the required cases from the phase brief and
the credential-handling cases that matter for safety.
"""

from __future__ import annotations

import pytest

from app.services.search.domain import canonicalize_url, extract_domain


class TestExtractDomain:
    """The required examples, plus the awkward real-world shapes."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.sebi.gov.in/example", "sebi.gov.in"),
            ("https://sebi.gov.in/", "sebi.gov.in"),
            ("https://nsearchives.nseindia.com/file", "nsearchives.nseindia.com"),
            ("https://example.com/path", "example.com"),
            ("http://example.com", "example.com"),
            ("https://EXAMPLE.COM/Path", "example.com"),
            ("https://www.example.com", "example.com"),
            ("sebi.gov.in", "sebi.gov.in"),
            ("example.com/path?q=1", "example.com"),
            ("https://example.com:443/path", "example.com"),
            ("https://example.com:8443/path", "example.com"),
            ("https://example.com./path", "example.com"),
        ],
    )
    def test_documented_cases(self, url: str, expected: str) -> None:
        assert extract_domain(url) == expected

    def test_subdomain_chain_is_preserved(self) -> None:
        # A subdomain can indicate a genuinely different official host, so
        # collapsing to the registrable domain would lose information.
        assert extract_domain("https://a.b.sebi.gov.in/x") == "a.b.sebi.gov.in"

    def test_credentials_are_discarded(self) -> None:
        assert extract_domain("https://user:pass@example.com/x") == "example.com"

    def test_www_is_only_stripped_at_the_front(self) -> None:
        assert extract_domain("https://mywww.example.com") == "mywww.example.com"

    @pytest.mark.parametrize("url", ["", "   ", None])
    def test_blank_input_returns_none(self, url: str | None) -> None:
        assert extract_domain(url) is None

    def test_ipv6_literal_is_returned_unchanged(self) -> None:
        assert extract_domain("https://[2001:db8::1]/x") == "[2001:db8::1]"

    def test_port_is_discarded(self) -> None:
        assert extract_domain("https://example.com:8080/x") == "example.com"


class TestCanonicalizeUrl:
    """Deduplication keys: same document collapses, different pages do not."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/page", "https://example.com/page"),
            ("https://example.com/page/", "https://example.com/page"),
            ("https://www.example.com/page", "https://example.com/page"),
            ("HTTPS://EXAMPLE.COM/page", "https://example.com/page"),
            ("https://example.com", "https://example.com"),
            ("https://example.com/", "https://example.com"),
            ("https://example.com/page#section", "https://example.com/page"),
            ("https://example.com:443/page", "https://example.com/page"),
            ("https://example.com/page?a=1", "https://example.com/page?a=1"),
        ],
    )
    def test_canonical_forms(self, url: str, expected: str) -> None:
        assert canonicalize_url(url) == expected

    def test_different_paths_stay_distinct(self) -> None:
        assert canonicalize_url("https://example.com/a") != canonicalize_url(
            "https://example.com/b"
        )

    def test_different_query_stays_distinct(self) -> None:
        assert canonicalize_url("https://example.com/a?id=1") != canonicalize_url(
            "https://example.com/a?id=2"
        )

    def test_different_hosts_stay_distinct(self) -> None:
        assert canonicalize_url("https://example.com/a") != canonicalize_url(
            "https://other.com/a"
        )

    def test_path_case_is_preserved(self) -> None:
        # Paths are case-sensitive on most servers, so only host/scheme are
        # lower-cased. Over-normalising here would merge different documents.
        assert canonicalize_url("https://example.com/Path") != canonicalize_url(
            "https://example.com/path"
        )

    def test_trailing_slash_on_nested_path_collapses(self) -> None:
        assert canonicalize_url("https://example.com/a/b/") == "https://example.com/a/b"

    @pytest.mark.parametrize("url", ["", "   ", None])
    def test_blank_input_returns_none(self, url: str | None) -> None:
        assert canonicalize_url(url) is None