"""
Mock Risk Scoring Function Tool
================================
This simulates Contoso Corp's internal vendor risk scoring API.
In production this would be a real Azure Function or HTTP endpoint
registered as a Function Tool in Foundry.

To run locally for testing:
    python function-tools/risk_scoring_api.py

To deploy as an Azure Function:
    See the /infra directory (future step).
"""

import json
import math


def calculate_composite_risk_score(
    vendor_name: str,
    market_red_flags: int,
    blacklist_status: str,                 # "CLEAR" | "BLOCKED" | "RESTRICTED"
    financial_risk_score: int,             # 0–100 from financial risk agent
    financial_risk_tier: str,             # "Low" | "Medium" | "High" | "Critical"
    country_risk: str,                     # "Low" | "Medium" | "High" | "Restricted"
    market_confidence: str = "Medium",    # "High" | "Medium" | "Low"
    compliance_hitl_required: bool = False,
    iso_certified: bool = True,
) -> dict:
    """
    Composite risk scoring algorithm.

    Weights:
        - Financial health:     30%
        - Compliance / ethics:  25%
        - Market intelligence:  25%
        - Geographic risk:      10%
        - Certification:        10%

    Returns a score 0–100 (higher = safer).
    """

    # ── Auto-reject on blacklist ──────────────────────────────────────────────
    if blacklist_status == "BLOCKED":
        return {
            "vendor_name": vendor_name,
            "composite_score": 0,
            "composite_tier": "Critical",
            "routing_decision": "AUTO_REJECT",
            "score_breakdown": {
                "market_intelligence_score": 0,
                "compliance_score": 0,
                "financial_score": 0,
                "weighted_total": 0,
            },
            "auto_reject_reason": "Vendor is on the Contoso blacklist — BLOCKED status.",
        }

    # ── Financial component (30 pts max) ─────────────────────────────────────
    financial_component = _scale(financial_risk_score, 0, 100, 0, 30)

    # ── Compliance component (25 pts max) ────────────────────────────────────
    compliance_score = 25  # start full
    if blacklist_status == "RESTRICTED":
        compliance_score -= 8
    if compliance_hitl_required:
        compliance_score -= 6
    if not iso_certified:
        compliance_score -= 4
    compliance_score = max(compliance_score, 0)

    # ── Market intelligence component (25 pts max) ───────────────────────────
    # red flags reduce score; confidence multiplier
    confidence_mult = {"High": 1.0, "Medium": 0.85, "Low": 0.70}.get(market_confidence, 0.85)
    red_flag_penalty = min(market_red_flags * 4, 20)  # max -20 pts
    market_raw = max(25 - red_flag_penalty, 0)
    market_component = round(market_raw * confidence_mult)

    # ── Geographic risk component (10 pts max) ───────────────────────────────
    geo_map = {"Low": 10, "Medium": 6, "High": 3, "Restricted": 0}
    geo_component = geo_map.get(country_risk, 5)

    # ── Composite ────────────────────────────────────────────────────────────
    composite = round(financial_component + compliance_score + market_component + geo_component)
    composite = max(0, min(composite, 100))  # clamp 0–100

    # ── Tier & routing ───────────────────────────────────────────────────────
    tier, routing = _tier_and_routing(composite, compliance_hitl_required)

    return {
        "vendor_name": vendor_name,
        "composite_score": composite,
        "composite_tier": tier,
        "routing_decision": routing,
        "score_breakdown": {
            "market_intelligence_score": market_component,
            "compliance_score": compliance_score,
            "financial_score": round(financial_component),
            "weighted_total": composite,
        },
    }


def _scale(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    """Linear scale value from input range to output range."""
    if in_max == in_min:
        return out_min
    return round((value - in_min) / (in_max - in_min) * (out_max - out_min) + out_min, 2)


def _tier_and_routing(score: int, hitl_override: bool) -> tuple[str, str]:
    if score >= 75:
        return ("Low", "AUTO_APPROVE")
    elif score >= 50:
        return ("Medium", "HITL_REQUIRED" if hitl_override else "STANDARD_APPROVAL")
    elif score >= 25:
        return ("High", "HITL_REQUIRED")
    else:
        return ("Critical", "AUTO_REJECT")


# ── OpenAI Function Tool schema (register this in Foundry) ───────────────────
FUNCTION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculate_composite_risk_score",
        "description": (
            "Calls Contoso's internal risk scoring API to compute a composite vendor risk score "
            "by combining market, compliance, and financial signals with weighted coefficients. "
            "Returns a score 0–100 and a routing decision."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_name": {
                    "type": "string",
                    "description": "Legal name of the vendor being evaluated.",
                },
                "market_red_flags": {
                    "type": "integer",
                    "description": "Number of red flags found by Market Intelligence agent (0–10).",
                    "minimum": 0,
                    "maximum": 10,
                },
                "market_confidence": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": "Confidence level of the market intelligence search.",
                },
                "blacklist_status": {
                    "type": "string",
                    "enum": ["CLEAR", "BLOCKED", "RESTRICTED"],
                    "description": "Vendor blacklist status from compliance agent.",
                },
                "compliance_hitl_required": {
                    "type": "boolean",
                    "description": "Whether the Policy Compliance agent flagged HITL requirement.",
                },
                "financial_risk_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Raw financial risk score (0–100) from the Financial Risk agent.",
                },
                "financial_risk_tier": {
                    "type": "string",
                    "enum": ["Low", "Medium", "High", "Critical"],
                    "description": "Financial risk classification from the Financial Risk agent.",
                },
                "iso_certified": {
                    "type": "boolean",
                    "description": "Whether the vendor has ISO 9001 or equivalent certification.",
                },
                "country_risk": {
                    "type": "string",
                    "enum": ["Low", "Medium", "High", "Restricted"],
                    "description": "Geographic risk tier for the vendor's country of operation.",
                },
            },
            "required": [
                "vendor_name",
                "market_red_flags",
                "blacklist_status",
                "financial_risk_score",
                "financial_risk_tier",
                "country_risk",
            ],
        },
    },
}


# ── Quick local test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_cases = [
        {
            "label": "Nexus Supply Co. (Low Risk)",
            "args": {
                "vendor_name": "Nexus Supply Co.",
                "market_red_flags": 0,
                "market_confidence": "High",
                "blacklist_status": "CLEAR",
                "compliance_hitl_required": False,
                "financial_risk_score": 85,
                "financial_risk_tier": "Low",
                "iso_certified": True,
                "country_risk": "Low",
            },
        },
        {
            "label": "Redstone Materials (High Risk)",
            "args": {
                "vendor_name": "Redstone Materials",
                "market_red_flags": 3,
                "market_confidence": "Medium",
                "blacklist_status": "CLEAR",
                "compliance_hitl_required": True,
                "financial_risk_score": 30,
                "financial_risk_tier": "High",
                "iso_certified": False,
                "country_risk": "High",
            },
        },
        {
            "label": "ShadeCraft Industries (Blocked — Auto-Reject)",
            "args": {
                "vendor_name": "ShadeCraft Industries",
                "market_red_flags": 5,
                "market_confidence": "High",
                "blacklist_status": "BLOCKED",
                "compliance_hitl_required": True,
                "financial_risk_score": 20,
                "financial_risk_tier": "Critical",
                "iso_certified": False,
                "country_risk": "High",
            },
        },
    ]

    for case in test_cases:
        print(f"\n{'='*60}")
        print(f"TEST CASE: {case['label']}")
        print("="*60)
        result = calculate_composite_risk_score(**case["args"])
        print(json.dumps(result, indent=2))
