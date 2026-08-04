from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any


def unique_display_name_fields() -> dict[str, str | None]:
    display_name = f"test_{uuid.uuid4().hex[:10]}"
    return {
        "display_name": display_name,
        "display_name_normalized": display_name,
        "display_name_changed_at": None,
    }


def schema_2_cost_estimate(
    *,
    completeness: str = "complete",
    include_internal_fields: bool = False,
) -> dict[str, Any]:
    categories = [
        {
            "category": category,
            "coverage": "priced",
            "range": {"min_cny": 100, "max_cny": 200},
            "price_basis": "reference",
            "basis_label": "测试参考价",
        }
        for category in (
            "intercity_transport",
            "accommodation",
            "local_transport",
            "admission",
            "meals",
        )
    ]
    if completeness == "partial":
        categories[0] = {
            "category": "intercity_transport",
            "coverage": "missing",
            "range": None,
            "price_basis": None,
            "basis_label": "未提供出发城市",
        }
        scenario_id = "without_intercity"
        intercity_mode = None
        total_scope = "estimated_subset"
        total_range = {"min_cny": 400, "max_cny": 800}
        missing_categories = ["intercity_transport"]
    elif completeness == "unavailable":
        categories = [
            {
                "category": item["category"],
                "coverage": "missing",
                "range": None,
                "price_basis": None,
                "basis_label": "暂不可估算",
            }
            for item in categories
        ]
        scenario_id = "without_intercity"
        intercity_mode = None
        total_scope = "unavailable"
        total_range = None
        missing_categories = [item["category"] for item in categories]
    else:
        scenario_id = "train_round_trip"
        intercity_mode = "train"
        total_scope = "full_trip"
        total_range = {"min_cny": 500, "max_cny": 1000}
        missing_categories = []
    payload: dict[str, Any] = {
        "snapshot_version": "1",
        "completeness": completeness,
        "currency": "CNY",
        "estimated_at": "2026-08-04T00:00:00Z",
        "scenarios": [
            {
                "scenario_id": scenario_id,
                "intercity_mode": intercity_mode,
                "label": "测试费用场景",
                "total_scope": total_scope,
                "total_range": total_range,
                "categories": categories,
                "missing_categories": missing_categories,
            }
        ],
        "assumptions": [{"code": "two_travellers_per_room", "label": "每间房两位旅客"}],
        "exclusions": [{"code": "cycling_cost_not_included", "label": "骑行费用暂未计入"}],
        "notice": "费用为规划参考，实际支付金额请以预订或现场结算为准",
    }
    if include_internal_fields:
        payload.update(
            {
                "snapshot_id": "secret-snapshot",
                "diagnostics": {"fen_arithmetic": [10000, 20000]},
                "provider_payload": {"secret": True},
            }
        )
        payload["scenarios"][0]["observations"] = [{"raw_fare": 10000}]
        payload["scenarios"][0]["categories"][0]["line_items"] = [
            {"provider_payload": {"secret": True}}
        ]
    return deepcopy(payload)
