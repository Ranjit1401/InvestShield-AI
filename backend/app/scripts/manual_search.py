"""Manual smoke test for the Phase 3 search infrastructure.

Not part of the pytest suite. Run it directly to see what the search layer does
in this environment:

    cd backend
    python -m scripts.manual_search

It uses the synthetic query from the phase brief. With no `SERPAPI_KEY` it must
degrade gracefully and print an explicit unavailable response; with a key it
prints normalized results and their source classification.

It never interprets the results. It prints what was retrieved and who published
it. Whether any of it bears on a claim's truth is Phase 4's question, not this
script's.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.services.search import SearchService, build_search_service

QUERY = "SEBI approved trading platform guaranteed 35% monthly returns"


def main() -> int:
    """Run one search and print the outcome. Returns a process exit code."""
    settings = get_settings()
    service: SearchService = build_search_service(settings)

    print(f"provider        : {service.provider_name}")
    print(f"configured      : {service.available}")
    print(f"query           : {QUERY}")
    print(f"max results     : {settings.search_max_results}")
    print("-" * 72)

    response = service.search(QUERY, max_results=5)

    print(f"status          : {response.status.value}")
    print(f"success         : {response.success}")
    print(f"degraded        : {response.degraded}")
    print(f"error code      : {response.error_code or '-'}")
    print(f"request sent    : {response.provider_query_sent}")
    print(f"normalized query: {response.query!r}")

    for warning in response.warnings:
        print(f"warning         : {warning}")

    if not response.success:
        print("-" * 72)
        print(
            "Search did not complete. This is an explicit degraded state: no "
            "conclusion of any kind may be drawn from it."
        )
        return 0 if response.status.value == "UNAVAILABLE" else 1

    print(f"results         : {len(response.results)}")
    for item in response.results:
        print("-" * 72)
        print(f"  position      : {item.position}")
        print(f"  title         : {item.title}")
        print(f"  url           : {item.url}")
        print(f"  snippet       : {(item.snippet or '-')[:160]}")
        print(f"  source domain : {item.source_domain}")
        print(f"  source type   : {item.source_type.value}")
        print(f"  priority      : {item.priority} (customary authority of the category)")

    print("-" * 72)
    print(
        "Retrieval only. Source type indicates which kind of organisation "
        "published a page; it is NOT a verdict on any claim."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())