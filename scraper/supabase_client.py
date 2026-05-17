"""Thin Supabase client wrapper.

Provides:
  - get_client(): lazy-init singleton, reads SUPABASE_URL + SUPABASE_SECRET_KEY from env.
  - smoke_test(): round-trip that hits the recipes table and reports up/down.
  - Optional SupabaseRecipeStore: drop-in replacement for recipe_builder.RecipeStore
    when you're ready to switch storage backends. Not wired up by default.

Uses the secret/service_role key so server-side writes bypass Row Level Security.
Never expose this key to the frontend.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Lazy singleton. Returns supabase.Client."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set in environment "
            "(check .env is loaded — main.py calls load_dotenv on startup)."
        )

    from supabase import create_client
    _client = create_client(url, key)
    return _client


def smoke_test() -> dict:
    """One-shot connectivity check. Returns a dict describing the result.

    Tries to SELECT from the recipes table. If the table doesn't exist yet,
    that's surfaced as a distinct error so you know to run schema.sql.
    """
    try:
        client = get_client()
    except Exception as e:
        return {"ok": False, "stage": "init", "error": str(e)}

    try:
        resp = client.table("recipes").select("domain", count="exact").limit(1).execute()
        return {
            "ok": True,
            "stage": "select",
            "table": "recipes",
            "row_count": getattr(resp, "count", None),
        }
    except Exception as e:
        msg = str(e)
        hint = None
        if "does not exist" in msg or "schema cache" in msg or "PGRST205" in msg:
            hint = "Run supabase/schema.sql in the Supabase SQL editor first."
        return {"ok": False, "stage": "select", "error": msg, "hint": hint}


# ---------------------------------------------------------------------------
# Optional drop-in for recipe_builder.RecipeStore — wire up when ready.
# ---------------------------------------------------------------------------

class SupabaseRecipeStore:
    """Same interface as recipe_builder.RecipeStore but backed by Supabase."""

    def __init__(self):
        self.client = get_client()

    def get(self, domain: str):
        from .recipe_builder import Recipe
        resp = (
            self.client.table("recipes")
            .select("*")
            .eq("domain", domain)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        return Recipe(
            domain=row["domain"],
            field_selectors=row.get("field_selectors") or {},
            negative_cache=row.get("negative_cache") or [],
            status=row.get("status", "active"),
            recipe_version=row.get("recipe_version", 1),
            last_hash=row.get("last_hash"),
        )

    def upsert(self, recipe) -> None:
        self.client.table("recipes").upsert(
            {
                "domain": recipe.domain,
                "field_selectors": recipe.field_selectors,
                "negative_cache": recipe.negative_cache,
                "status": recipe.status,
                "recipe_version": recipe.recipe_version,
                "last_hash": recipe.last_hash,
            },
            on_conflict="domain",
        ).execute()
