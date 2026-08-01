from __future__ import annotations

import uuid


def unique_display_name_fields() -> dict[str, str | None]:
    display_name = f"test_{uuid.uuid4().hex[:10]}"
    return {
        "display_name": display_name,
        "display_name_normalized": display_name,
        "display_name_changed_at": None,
    }
