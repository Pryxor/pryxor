"""
Pryxor — Email sector.

Rules:
    - Sensitive subject (password, secret, key) → BLOCK
    - Too many recipients → HOLD
    - External domain → HOLD
"""

from __future__ import annotations

import re
from typing import Any

from ._framework.base import Decision
from ._framework.simple import SimpleSector


class EmailSector(SimpleSector):
    name = "email_code"
    version = "1.0.0"

    def decide(
        self,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> Decision:
        if not isinstance(parameters, dict):
            return Decision.block("INVALID_TOOL_CALL", "'parameters' must be a dict.")

        # --- Extract destinators (to + cc) ---------------------------
        def _as_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [str(v) for v in value]
            return [str(value)]

        recipients = _as_list(parameters.get("to")) + _as_list(parameters.get("cc"))
        recipients = [r.strip() for r in recipients if r.strip()]

        subject = str(parameters.get("subject") or "")

        # --- Rule 1: sensitive keywords in the subject -----------------
        blocked_pattern = self.sector_config.get(
            "blocked_subject_pattern",
            r"(?i)password|secret|key",
        )
        if blocked_pattern and re.search(blocked_pattern, subject):
            return Decision.block(
                "SENSITIVE_SUBJECT",
                "Subject contains a blocked keyword.",
                subject=subject,
            )

        # --- Rule 2: maximum number of recipients ----------------------
        max_recipients = int(self.sector_config.get("max_recipients", 10))
        if len(recipients) > max_recipients:
            return Decision.hold(
                "MASS_EMAIL",
                f"{len(recipients)} recipients > limit {max_recipients}.",
                recipients_count=len(recipients),
            )

        # --- Rule 3: external domains ----------------------------------
        allowed_domains = [
            d.lower().lstrip("@") for d in (self.sector_config.get("allowed_domains") or [])
        ]
        if allowed_domains:
            for r in recipients:
                if "@" not in r:
                    return Decision.block(
                        "INVALID_RECIPIENT",
                        f"Recipient '{r}' is not a valid email address.",
                    )
                domain = r.split("@", 1)[1].lower()
                if domain not in allowed_domains:
                    return Decision.hold(
                        "EXTERNAL_EMAIL",
                        f"Domain '{domain}' is not in the allowed list.",
                        recipient=r,
                        domain=domain,
                    )

        return Decision.approve(
            f"Email authorized to {len(recipients)} recipient(s).",
            recipients_count=len(recipients),
        )
