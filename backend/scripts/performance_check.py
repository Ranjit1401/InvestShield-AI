"""The Phase 16 performance check on realistic deterministic inputs.

This is the performance leg of Phase 16: it runs every input type
through the real investigation pipeline — the same offline graph the
API serves — and reports how long each run took. The inputs are
deterministic and generated locally: the regulator fixture content
for TEXT, a rendered screenshot for IMAGE and a rendered PDF for
PDF, so the numbers are comparable between runs and between machines
in the way that matters: same work, measured.

The numbers are **informational**. There is deliberately no timing
threshold: a fixed bound would be a fragile assertion that fails on
a loaded machine while saying nothing about correctness. What this
script refuses to tolerate is a failed run — a run that raises or
returns a FAILED state is a real failure and exits non-zero.

Usage::

    python scripts/performance_check.py [--iterations 5]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for candidate in (str(BACKEND_DIR), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from app.graph.context import GraphContext  # noqa: E402
from app.graph.investigation_graph import run_investigation  # noqa: E402
from app.graph.state import InvestigationInputType, InvestigationState  # noqa: E402
from app.schemas.ocr import ImageUpload  # noqa: E402
from app.schemas.pdf import PdfUpload  # noqa: E402
from app.services.ocr_service import OCRService  # noqa: E402
from app.services.pdf_service import PDFService  # noqa: E402
from tests.graph.graph_factories import offline_settings, real_dependencies  # noqa: E402
from tests.image_factories import png_bytes  # noqa: E402
from tests.pdf_factories import pdf_bytes  # noqa: E402
from tests.persistence_factories import REGULATORY_CONTENT  # noqa: E402

#: The deterministic input every leg runs on. The same content for
#: every input type, so the only variable is the modality.
SCREENSHOT_TEXT = "Acme Capital Advisors"
PDF_TEXT = "Acme Capital Advisors"


def _build_context() -> GraphContext:
    """Build the offline graph with both upload engines working.

    Returns:
        A context wired to the real Phase 1-6 services, with the
        real OCR and PDF engines where they are installed.
    """
    dependencies, _ = real_dependencies(offline_settings())
    return GraphContext(
        dependencies=replace(
            dependencies,
            ocr_service=OCRService(settings=offline_settings()),
            pdf_service=PDFService(settings=offline_settings()),
        )
    )


def _run_text(context: GraphContext) -> InvestigationState:
    """Run one text investigation."""
    return run_investigation(
        REGULATORY_CONTENT, input_type=InvestigationInputType.TEXT, context=context
    )


def _run_screenshot(context: GraphContext) -> InvestigationState:
    """Run one screenshot investigation."""
    return run_investigation(
        SCREENSHOT_TEXT,
        input_type=InvestigationInputType.IMAGE,
        context=context,
        upload=ImageUpload(
            content=png_bytes(text=SCREENSHOT_TEXT), content_type="image/png"
        ),
    )


def _run_pdf(context: GraphContext) -> InvestigationState:
    """Run one PDF investigation."""
    return run_investigation(
        PDF_TEXT,
        input_type=InvestigationInputType.PDF,
        context=context,
        pdf_upload=PdfUpload(
            content=pdf_bytes(text=PDF_TEXT), content_type="application/pdf"
        ),
    )


def main() -> int:
    """Run every input type and report the timing table.

    Returns:
        `0` when every attempted run succeeded, `1` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="How many times to run each input type (default: 5).",
    )
    args = parser.parse_args()
    iterations = max(1, args.iterations)

    context = _build_context()
    legs: list[tuple[str, Callable[[GraphContext], None], bool]] = [
        ("TEXT", _run_text, True),
        ("SCREENSHOT", _run_screenshot, OCRService(settings=offline_settings()).available),
        ("PDF", _run_pdf, PDFService(settings=offline_settings()).available),
    ]

    failures = 0
    header = f"{'input':<12} {'runs':>5} {'ok':>5} {'min':>9} {'median':>9} {'max':>9}"
    print(header)
    print("-" * len(header))
    for name, runner, available in legs:
        if not available:
            print(f"{name:<12} {'-':>5} {'-':>5} {'engine not installed':>9}")
            continue
        elapsed: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter()
            try:
                state = runner(context)
            except Exception as error:  # noqa: BLE001 - reported, never hidden
                failures += 1
                print(f"{name}: run raised: {type(error).__name__}: {error}")
                continue
            if state.get("errors"):
                failures += 1
                print(f"{name}: run failed: {state['errors'][0].code}")
                continue
            elapsed.append(time.perf_counter() - started)
        if not elapsed:
            failures += 1
            continue
        print(
            f"{name:<12} {iterations:>5} {len(elapsed):>5} "
            f"{min(elapsed):>8.3f}s {statistics.median(elapsed):>8.3f}s {max(elapsed):>8.3f}s"
        )

    if failures:
        print(f"\n{failures} run(s) failed.")
        return 1
    print("\nAll runs completed. Times are informational; no threshold is enforced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
