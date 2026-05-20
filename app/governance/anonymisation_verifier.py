"""
Anonymisation verifier (§13.3).

Deny-by-default classifier that blocks any global-tier write containing
patterns resembling known sensitive-data shapes.

Contract (§13.3 verbatim):
  - All tenant/user/project/sensitive-business identifiers and free-text content
    are removed or hashed.
  - The remaining content must be a pattern, not a record.
  - An automated anonymisation verifier runs on every candidate global-tier write.
  - The verifier is deny-by-default: blocks any write that contains a pattern
    resembling a known sensitive-data shape.
  - Enterprise CoE approves the anonymised version, not the raw version.
  - Adya platform team gives final approval and signs the global-tier commit.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Sensitive-data patterns (deny-by-default) ─────────────────────────────────

_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    # Pattern name, regex
    ("email",           r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    ("uuid",            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    ("ip_address",      r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("phone_number",    r"\b(?:\+?\d[\d\s\-]{7,14}\d)\b"),
    ("credit_card",     r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"),
    ("aws_key",         r"AKIA[0-9A-Z]{16}"),
    ("jwt_token",       r"eyJ[a-zA-Z0-9_\-]{20,}\.eyJ[a-zA-Z0-9_\-]{20,}"),
    ("org_name",        r"\b[A-Z][a-z]+ (?:Inc|Ltd|Corp|LLC|GmbH|Plc|Co)\.?\b"),
    ("tenant_id",       r"\btenant[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
    ("user_id",         r"\buser[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
    ("project_id",      r"\bproject[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
    ("workspace_id",    r"\bworkspace[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
    ("personal_name",   r"\b(?:Mr|Ms|Mrs|Dr|Prof)\.\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"),
]


@dataclass
class VerificationResult:
    passed: bool
    violations: list[str]           # list of pattern names that triggered
    sanitised_text: Optional[str]   # cleaned version if passed after sanitisation
    message: str


class AnonymisationVerifier:
    """
    Deny-by-default verifier for global-tier insight candidates.
    Run before any global-tier write is promoted.
    """

    def verify(self, text: str) -> VerificationResult:
        """
        Check text for sensitive-data shapes.
        Returns VerificationResult with passed=True only if clean.
        """
        violations = []
        for name, pattern in _SENSITIVE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(name)

        if violations:
            logger.warning(
                "[AnonVerifier] BLOCKED — sensitive patterns found: %s",
                ", ".join(violations),
            )
            return VerificationResult(
                passed=False,
                violations=violations,
                sanitised_text=None,
                message=f"Blocked: contains sensitive patterns: {', '.join(violations)}",
            )

        logger.info("[AnonVerifier] PASSED — no sensitive patterns detected")
        return VerificationResult(
            passed=True,
            violations=[],
            sanitised_text=text,
            message="Verification passed: no sensitive-data patterns detected",
        )

    def sanitise_and_verify(self, text: str) -> VerificationResult:
        """
        Attempt to sanitise the text by removing all sensitive patterns,
        then verify the sanitised version. Only passes if the result is
        a pure behavioural pattern (no identifiers).
        """
        sanitised = text
        applied: list[str] = []

        replacements = {
            "email":        ("[EMAIL]",      r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
            "uuid":         ("[UUID]",        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
            "ip_address":   ("[IP]",          r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            "phone_number": ("[PHONE]",       r"\b(?:\+?\d[\d\s\-]{7,14}\d)\b"),
            "credit_card":  ("[CARD]",        r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"),
            "aws_key":      ("[AWS_KEY]",     r"AKIA[0-9A-Z]{16}"),
            "jwt_token":    ("[TOKEN]",       r"eyJ[a-zA-Z0-9_\-]{20,}\.eyJ[a-zA-Z0-9_\-]{20,}"),
            "org_name":     ("[ORG]",         r"\b[A-Z][a-z]+ (?:Inc|Ltd|Corp|LLC|GmbH|Plc|Co)\.?\b"),
            "tenant_id":    ("[TENANT_ID]",   r"\btenant[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
            "user_id":      ("[USER_ID]",     r"\buser[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
            "project_id":   ("[PROJECT_ID]",  r"\bproject[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
            "workspace_id": ("[WORKSPACE]",   r"\bworkspace[_\-]?id\s*[:=]\s*['\"]?[\w\-]{6,}['\"]?"),
            "personal_name": ("[PERSON]",      r"\b(?:Mr|Ms|Mrs|Dr|Prof)\.\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"),
        }

        for name, (replacement, pattern) in replacements.items():
            new_text, n = re.subn(pattern, replacement, sanitised, flags=re.IGNORECASE)
            if n > 0:
                applied.append(name)
                sanitised = new_text

        # Re-verify the sanitised version
        result = self.verify(sanitised)
        if result.passed:
            result.sanitised_text = sanitised
            if applied:
                result.message = (
                    f"Passed after sanitisation. Replaced: {', '.join(applied)}"
                )
        return result

    def verify_candidate(self, candidate_dict: dict) -> VerificationResult:
        """
        Verify an entire CandidateInsight dict (checks all text fields).
        """
        text_fields = ["pattern_description", "proposed_action", "evidence_summary"]
        combined = " ".join(str(candidate_dict.get(f, "")) for f in text_fields)
        return self.verify(combined)
