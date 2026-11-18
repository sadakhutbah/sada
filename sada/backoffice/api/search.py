"""Search across the sermons of the mosque: words, Quran verses, meaning."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from .. import search as search_mod
from ..context import CtxDep, imam_filter

router = APIRouter()


@router.get("/search")
async def search(ctx: CtxDep, q: str = "", limit: int = Query(20, ge=1, le=50)):
    """Results grouped by sermon. Same scope as the sermon list: the mosque's sermons, and for an
    imam without `history_all` only his own. The query embedding runs off the event loop."""
    if len(q.strip()) < 2:
        return {"q": q.strip(), "results": [], "semantic": False, "timings": {}}
    return await run_in_threadpool(search_mod.search, ctx.conn, imam_filter(ctx), q, limit)
