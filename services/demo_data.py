from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from media_engine.script_manifest import prepare_script_manifest
from models.database import execute, init_db, query, upsert_companies, upsert_prices, upsert_research_sources
from services.web_research import approve_research_bundle, research_bundle_path


SYNTHETIC_EVENT_DATE = "2026-05-29"
SYNTHETIC_TICKER = "ALTA"


def load_synthetic_demo_data() -> dict[str, Any]:
    """Load a small, fully synthetic demo event with approved source evidence."""

    init_db()
    upsert_companies(
        [
            {
                "ticker": SYNTHETIC_TICKER,
                "name": "ArcLight Analytics",
                "sector": "Technology",
                "industry": "Enterprise AI Software",
                "market_cap": 8_400_000_000,
            }
        ]
    )
    upsert_prices(_synthetic_prices())
    event_id = _upsert_demo_event()
    event = query("SELECT * FROM events WHERE id = ?", (event_id,))[0]
    upsert_research_sources(_synthetic_research_sources(event_id))
    bundle_path = _write_synthetic_research_bundle(event)
    approve_research_bundle(bundle_path, approval_mode="synthetic_demo")
    manifest_result = prepare_script_manifest(event_id)
    return {
        "event_id": event_id,
        "ticker": SYNTHETIC_TICKER,
        "event_date": SYNTHETIC_EVENT_DATE,
        "research_bundle_path": str(bundle_path),
        "manifest_path": manifest_result["manifest_path"],
    }


def _upsert_demo_event() -> int:
    analysis = {
        "summary": (
            "ArcLight Analytics moved higher after a synthetic product-launch update "
            "and stronger-than-expected pilot adoption metrics."
        ),
        "price_change_pct": 8.4,
        "volume_ratio": 2.9,
        "catalysts": [
            "Synthetic product-launch update",
            "Higher pilot-conversion metrics",
            "Raised full-year demo revenue range",
        ],
        "risks": [
            "Demo data is synthetic and should not be interpreted as market evidence.",
            "Enterprise AI adoption timelines can change quickly.",
        ],
    }
    execute(
        """
        INSERT INTO events (ticker, event_type, event_date, score, reason, analysis_json, status)
        VALUES (?, 'synthetic_demo_mover', ?, 88, ?, ?, 'candidate')
        ON CONFLICT(ticker, event_type, event_date) DO UPDATE SET
            score=excluded.score,
            reason=excluded.reason,
            analysis_json=excluded.analysis_json,
            status='candidate'
        """,
        (
            SYNTHETIC_TICKER,
            SYNTHETIC_EVENT_DATE,
            "ALTA moved up 8.4% with 2.9x average volume after synthetic AI platform news",
            json.dumps(analysis, sort_keys=True),
        ),
    )
    rows = query(
        """
        SELECT id
        FROM events
        WHERE ticker = ? AND event_type = 'synthetic_demo_mover' AND event_date = ?
        """,
        (SYNTHETIC_TICKER, SYNTHETIC_EVENT_DATE),
    )
    return int(rows[0]["id"])


def _synthetic_prices() -> list[dict[str, Any]]:
    base_rows = [
        ("2026-05-20", 39.8, 40.4, 39.1, 39.7, 1_110_000, -0.8),
        ("2026-05-21", 39.7, 41.0, 39.4, 40.8, 1_180_000, 2.8),
        ("2026-05-22", 40.9, 42.1, 40.6, 41.9, 1_290_000, 2.7),
        ("2026-05-26", 42.0, 42.5, 41.3, 41.8, 1_030_000, -0.2),
        ("2026-05-27", 41.8, 42.0, 40.9, 41.1, 1_080_000, -1.7),
        ("2026-05-28", 41.2, 41.9, 40.7, 41.5, 1_160_000, 1.0),
        ("2026-05-29", 42.3, 45.4, 42.2, 45.0, 3_360_000, 8.4),
    ]
    return [
        {
            "ticker": SYNTHETIC_TICKER,
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "average_volume": 1_160_000,
            "current_price": close,
            "change_percent": change_percent,
            "market_cap": 8_400_000_000,
        }
        for date, open_price, high, low, close, volume, change_percent in base_rows
    ]


def _synthetic_research_sources(event_id: int) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event_id,
            "ticker": SYNTHETIC_TICKER,
            "provider": "synthetic_filings",
            "title": "ArcLight Analytics synthetic 8-K product update",
            "url": "https://www.sec.gov/Archives/synthetic/arclight-8k-product-update",
            "source": "Synthetic SEC EDGAR filing",
            "published_at": "2026-05-29T13:15:00Z",
            "highlights_json": json.dumps(
                [
                    "ArcLight announced a synthetic AI workflow platform update.",
                    "Pilot customers converted at a higher rate than the prior synthetic baseline.",
                ]
            ),
            "metadata_json": json.dumps(
                {
                    "source_quality": {
                        "quality": "official",
                        "tier": 1,
                        "label": "primary_official",
                        "claim_use_policy": "hard_facts_and_official_claims",
                    },
                    "source_tier": 1,
                    "source_tier_label": "primary_official",
                    "claim_use_policy": "hard_facts_and_official_claims",
                    "is_official_company_release": True,
                    "requires_confirmation": False,
                },
                sort_keys=True,
            ),
        },
        {
            "event_id": event_id,
            "ticker": SYNTHETIC_TICKER,
            "provider": "synthetic_news",
            "title": "ArcLight shares rise on synthetic AI adoption readout",
            "url": "https://www.reuters.com/synthetic/arclight-market-brief",
            "source": "Reuters Synthetic Market Wire",
            "published_at": "2026-05-29T15:40:00Z",
            "highlights_json": json.dumps(
                [
                    "The synthetic stock move came with nearly three times average volume.",
                    "Analysts in the synthetic scenario focused on adoption durability.",
                ]
            ),
            "metadata_json": json.dumps(
                {
                    "source_quality": {
                        "quality": "reputable_financial",
                        "tier": 2,
                        "label": "reputable_financial",
                        "claim_use_policy": "hard_facts_with_tier1_preferred_for_exact_claims",
                    },
                    "source_tier": 2,
                    "source_tier_label": "reputable_financial",
                    "claim_use_policy": "hard_facts_with_tier1_preferred_for_exact_claims",
                    "is_official_company_release": False,
                    "requires_confirmation": False,
                },
                sort_keys=True,
            ),
        },
    ]


def _write_synthetic_research_bundle(event: dict[str, Any]) -> Path:
    bundle_path = research_bundle_path(event)
    bundle_path.mkdir(parents=True, exist_ok=True)
    accepted = [
        {
            "provider": "synthetic_filings",
            "title": "ArcLight Analytics synthetic 8-K product update",
            "url": "https://www.sec.gov/Archives/synthetic/arclight-8k-product-update",
            "source": "Synthetic SEC EDGAR filing",
            "published_at": "2026-05-29T13:15:00Z",
            "highlights": [
                "ArcLight announced a synthetic AI workflow platform update.",
                "Pilot customers converted at a higher rate than the prior synthetic baseline.",
            ],
            "source_quality": {"quality": "official", "tier": 1},
            "review_status": "accepted",
            "review_reason": "synthetic primary source for public demo",
        },
        {
            "provider": "synthetic_news",
            "title": "ArcLight shares rise on synthetic AI adoption readout",
            "url": "https://www.reuters.com/synthetic/arclight-market-brief",
            "source": "Reuters Synthetic Market Wire",
            "published_at": "2026-05-29T15:40:00Z",
            "highlights": [
                "The synthetic stock move came with nearly three times average volume.",
                "Analysts in the synthetic scenario focused on adoption durability.",
            ],
            "source_quality": {"quality": "reputable_financial", "tier": 2},
            "review_status": "accepted",
            "review_reason": "synthetic secondary source for public demo",
        },
    ]
    rejected = [
        {
            "provider": "synthetic_social",
            "title": "Unverified social speculation about ALTA",
            "url": "https://example.com/synthetic/unverified-alta-thread",
            "source": "Synthetic Social Feed",
            "published_at": "2026-05-29T14:00:00Z",
            "highlights": ["Rumor-like signal intentionally rejected."],
            "source_quality": {"quality": "discovery", "tier": 4},
            "review_status": "rejected",
            "review_reason": "discovery-only and not suitable for factual support",
        }
    ]
    _write_json(bundle_path / "synthetic_request.json", {"query": "ALTA synthetic demo event"})
    _write_json(bundle_path / "synthetic_raw_response.json", {"results": accepted + rejected})
    _write_json(bundle_path / "synthetic_review_results.json", {"accepted": accepted, "rejected": rejected})
    _write_json(bundle_path / "review_results.json", {"accepted": accepted, "rejected": rejected})
    _write_json(
        bundle_path / "manifest.json",
        {
            "event_id": int(event["id"]),
            "ticker": event["ticker"],
            "date": SYNTHETIC_EVENT_DATE,
            "automation_stage": "synthetic_research_ready_for_review",
            "ready_for_script_generation": False,
            "provider_queries": {"synthetic": "ALTA synthetic demo event"},
            "provider_counts": {
                "synthetic": {
                    "provider": "synthetic",
                    "accepted_count": len(accepted),
                    "rejected_count": len(rejected),
                    "stored_count": len(accepted),
                }
            },
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "stored_count": len(accepted),
        },
    )
    (bundle_path / "review.md").write_text(
        "# Synthetic Research Review\n\n"
        "This bundle is generated from synthetic public-demo fixtures. It contains no "
        "redistributed market data or provider responses.\n",
        encoding="utf-8",
    )
    return bundle_path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
