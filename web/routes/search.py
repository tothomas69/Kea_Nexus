"""
search.py — The header's search box (⌘K / Ctrl+K). Answers each keystroke
with a short list of links; the matching rules live in web/search_data.py.
"""

from fastapi import APIRouter, Depends, Query, Request, Response

import db
from kea import KeaClient
from web.deps import current_user, kea_client
from web.kea_reads import read_kea
from web.routes.overview import address_rows
from web.search_data import MIN_QUERY_LENGTH, search
from web.templating import render

router = APIRouter()


@router.get("/search")
def search_results(
	request: Request,
	q: str = Query(""),
	user: dict = Depends(current_user),
	kea: KeaClient = Depends(kea_client),
) -> Response:
	data = read_kea(kea)
	rows = address_rows(kea, data["leases"], data["config"])
	context = {
		"query": q.strip(),
		"results": search(rows, db.get_devices(), q),
		"min_length": MIN_QUERY_LENGTH,
	}
	return render(request, "_search_results.html", context)
