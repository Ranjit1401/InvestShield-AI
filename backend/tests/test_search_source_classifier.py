"""Tests for source classification (Phase 3, §8).

The most important assertions here are the negative ones. A classifier that
labels `fake-sebi-example.com` as a regulator would let a later stage present a
scammer's own website as authoritative, so authority must be recognisable only
by hostname identity.
"""

from __future__ import annotations

import pytest

from app.schemas.search import SourceType
from app.services.search.source_classifier import (
    classify_domain,
    classify_url,
)


class TestKnownOfficialDomains:
    """Explicit registry entries."""

    @pytest.mark.parametrize(
        "domain",
        ["sebi.gov.in", "rbi.org.in", "irdai.gov.in", "nfra.gov.in", "sfio.nic.in"],
    )
    def test_regulators(self, domain: str) -> None:
        assert classify_domain(domain) is SourceType.REGULATOR

    @pytest.mark.parametrize(
        "domain",
        ["nseindia.com", "bseindia.com", "www.nseindia.com", "nsearchives.nseindia.com"],
    )
    def test_exchanges(self, domain: str) -> None:
        assert classify_domain(domain) is SourceType.EXCHANGE

    @pytest.mark.parametrize(
        "domain",
        ["mca.gov.in", "www.mca.gov.in", "incometax.gov.in", "edcs.gov.in", "data.gov.in"],
    )
    def test_government_hosts(self, domain: str) -> None:
        assert classify_domain(domain) is SourceType.GOVERNMENT

    def test_regulator_specificity_beats_the_gov_suffix(self) -> None:
        # sebi.gov.in is both a regulator and a .gov.in host; the more specific
        # answer is the useful one.
        assert classify_domain("sebi.gov.in") is SourceType.REGULATOR

    def test_unknown_subdomain_of_a_regulator_inherits_the_category(self) -> None:
        assert classify_domain("investor.sebi.gov.in") is SourceType.REGULATOR


class TestUnknownSources:
    """Unrecognised hosts must not be promoted."""

    @pytest.mark.parametrize(
        "domain",
        [
            "unknown.com",
            "some-random-blog.net",
            "example.org",
            "news-site.in",
        ],
    )
    def test_ordinary_sites_are_general_web(self, domain: str) -> None:
        assert classify_domain(domain) is SourceType.GENERAL_WEB

    @pytest.mark.parametrize(
        "domain",
        ["", "   ", None],
    )
    def test_blank_is_unknown(self, domain: str | None) -> None:
        assert classify_domain(domain) is SourceType.UNKNOWN

    def test_host_without_a_public_suffix_is_unknown(self) -> None:
        # No recognisable suffix: not confidently "ordinary published content".
        assert classify_domain("intranet-host") is SourceType.UNKNOWN

    def test_ip_literal_is_unknown(self) -> None:
        assert classify_domain("192.0.2.1") is SourceType.UNKNOWN


class TestAuthorityIsNotGuessedFromWords:
    """The core anti-overclaiming guarantee."""

    @pytest.mark.parametrize(
        "domain",
        [
            "fake-sebi-example.com",
            "mysebi.gov.in.evil.com",
            "sebi-registry-fake.in",
            "sebi-approval-portal.com",
            "notsebi.gov.in.example.com",
            "fake-rbi.com",
            "rbi-fraud-help.net",
            "nseindia-fake.com",
            "bseindia.example",
            "www-sebi-gov-in.evil.net",
        ],
    )
    def test_lookalike_domains_are_not_official(self, domain: str) -> None:
        result = classify_domain(domain)

        assert result not in {
            SourceType.REGULATOR,
            SourceType.GOVERNMENT,
            SourceType.EXCHANGE,
        }
        assert result is SourceType.GENERAL_WEB

    def test_label_boundary_matching_is_enforced(self) -> None:
        # "mysebi.gov.in" is a real .gov.in host and is GOVERNMENT, but it must
        # not be treated as SEBI itself.
        assert classify_domain("mysebi.gov.in") is SourceType.GOVERNMENT

    def test_subdomain_of_a_lookalike_is_not_official(self) -> None:
        assert classify_domain("sebi-example.com.br") is SourceType.GENERAL_WEB

    def test_unlisted_public_suffix_is_still_general_web(self) -> None:
        # Any real two-label host is ordinary web content once the official
        # registries have been ruled out by exact matching.
        for domain in ("example.co.uk", "site.com.br", "portal.co.jp"):
            assert classify_domain(domain) is SourceType.GENERAL_WEB


class TestClassifyUrl:
    """Classification by full URL delegates to host extraction."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.sebi.gov.in/example", SourceType.REGULATOR),
            ("https://nsearchives.nseindia.com/file", SourceType.EXCHANGE),
            ("https://mca.gov.in/", SourceType.GOVERNMENT),
            ("https://fake-sebi-example.com/", SourceType.GENERAL_WEB),
            ("https://example.com/path", SourceType.GENERAL_WEB),
        ],
    )
    def test_urls_classify_by_host(self, url: str, expected: SourceType) -> None:
        assert classify_url(url) is expected

    @pytest.mark.parametrize("url", ["", None, "not a url at all"])
    def test_unusable_urls_are_unknown(self, url: str | None) -> None:
        assert classify_url(url) is SourceType.UNKNOWN

    def test_classification_is_deterministic(self) -> None:
        results = {classify_domain("sebi.gov.in") for _ in range(50)}

        assert results == {SourceType.REGULATOR}