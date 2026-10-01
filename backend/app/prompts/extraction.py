"""Extraction prompt (Phase 2, §15).

The prompt is versioned and lives in code — not in a string literal inside the
service — so that extraction behaviour is auditable and a change is always a
reviewed diff. `EXTRACTION_PROMPT_VERSION` is recorded on every
:class:`~app.schemas.extraction.ExtractionResult`.

Change the version whenever the instructions change materially, so stored
results can be attributed to the prompt that produced them.
"""

from __future__ import annotations

#: Bump when the prompt instructions change materially.
EXTRACTION_PROMPT_VERSION = "extraction-v1"

#: The maximum characters of input sent to the model. Long inputs are truncated
#: with a visible marker so the model is never silently handed a partial
#: document without saying so.
MAX_INPUT_CHARS = 12000

#: The claim types the model may return. Kept in sync with
#: :class:`~app.schemas.claims.ClaimType` and enforced by Pydantic validation.
_CLAIM_TYPES = (
    "REGULATORY_STATUS",
    "RETURN_PROMISE",
    "PROFIT_PROMISE",
    "PERFORMANCE_CLAIM",
    "CREDENTIAL_CLAIM",
    "COMPANY_CLAIM",
    "PRODUCT_CLAIM",
    "INVESTMENT_OPPORTUNITY",
    "PAYMENT_INSTRUCTION",
    "WITHDRAWAL_CLAIM",
    "AFFILIATION_CLAIM",
    "OWNERSHIP_CLAIM",
    "GUARANTEE_CLAIM",
    "OTHER",
)

_ENTITY_TYPES = (
    "PERSON",
    "COMPANY",
    "ORGANIZATION",
    "REGULATOR",
    "BROKER",
    "INVESTMENT_ADVISER",
    "PLATFORM",
    "WEBSITE",
    "DOMAIN",
    "PRODUCT",
    "FINANCIAL_INSTRUMENT",
    "LOCATION",
    "SOCIAL_HANDLE",
    "REGISTRATION_NUMBER",
    "BANK_ACCOUNT",
    "UPI_ID",
    "PHONE_NUMBER",
    "EMAIL",
    "OTHER",
)

#: System instruction. Enforces the safety boundary between extraction and
#: verification, and forbids hallucination.
EXTRACTION_SYSTEM_PROMPT = f"""You are an information extraction system.

Extract claims and entities from the supplied investment-related text.

Hard rules:
- Extract ONLY information that is literally present in the input.
- Do NOT invent, complete or infer any company name, adviser name, registration
  number, website, amount, date or contact detail that is not written in the input.
- Do NOT determine whether any claim is true or false.
- Do NOT accuse any person or company of fraud, scam or misconduct.
- Do NOT output words like VERIFIED, UNVERIFIED, CONTRADICTED, SCAM or FRAUD as
  conclusions. Those belong to a later verification stage.
- Split the text into independently verifiable statements. Never return the
  whole message as one claim.
- Do NOT treat conversational filler ("Hello everyone", "Welcome to our group",
  "Contact us for details") as a claim.
- Copy claim text and entity names verbatim from the input. Do not paraphrase,
  translate or normalise them inside the `text`/`name` fields.
- Return structured data only. No prose, no explanation, no markdown.

Allowed claim_type values:
{", ".join(_CLAIM_TYPES)}

Allowed entity_type values:
{", ".join(_ENTITY_TYPES)}

confidence means: how confident you are that this text expresses a claim of this
type (or that this mention is an entity of this type). It is NOT how likely the
claim is to be true."""

#: Instruction block for one extraction request.
EXTRACTION_USER_PROMPT = """Extract the claims and entities from the text below.

Rules for this task:
- `claims[].text` must be copied verbatim from the input, including its original
  wording and punctuation.
- Split one sentence into multiple claims when it makes several separate claims.
- `claims[].entity_names` must list names of entities, as written, that the claim
  refers to.
- Prefer specific types over OTHER.
- Return an empty list for a field when nothing of that kind is present.

Text to analyse:
---
{input_text}
---"""


def build_extraction_prompt(input_text: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, str]:
    """Build the (user prompt, truncation warning) pair for an extraction call.

    Args:
        input_text: The already-normalised text to analyse.
        max_chars: Maximum characters to send.

    Returns:
        ``(prompt, warning)``. `warning` is ``None`` when nothing was truncated.
    """
    if len(input_text) <= max_chars:
        return EXTRACTION_USER_PROMPT.format(input_text=input_text), None

    truncated = input_text[:max_chars]
    warning = (
        f"Input was truncated to the first {max_chars} characters for LLM extraction; "
        "deterministic extraction still covered the full text."
    )
    return EXTRACTION_USER_PROMPT.format(input_text=truncated), warning


__all__ = [
    "EXTRACTION_PROMPT_VERSION",
    "EXTRACTION_SYSTEM_PROMPT",
    "EXTRACTION_USER_PROMPT",
    "MAX_INPUT_CHARS",
    "build_extraction_prompt",
]
