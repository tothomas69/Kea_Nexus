"""
help_routes.py — The Help panel's table of contents and the articles.

/help returns just the table of contents when HTMX asks for it (the Help
button loads it into a modal) and a full page otherwise, so the same URL also
works when opened directly.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from web.deps import current_user
from web.help_content import article_html, table_of_contents
from web.templating import is_htmx, render

router = APIRouter()

_HTTP_NOT_FOUND = 404


@router.get("/help")
def help_index(request: Request, user: dict = Depends(current_user)) -> Response:
	context = {"user": user, "articles": table_of_contents()}
	if is_htmx(request):
		return render(request, "help/_toc.html", context)
	return render(request, "help/index.html", context)


@router.get("/help/{filename}")
def help_article(filename: str, request: Request, user: dict = Depends(current_user)) -> Response:
	try:
		title, body_html = article_html(filename)
	except (KeyError, FileNotFoundError):
		raise HTTPException(status_code=_HTTP_NOT_FOUND, detail="No such help article.")
	return render(
		request, "help/article.html", {"user": user, "title": title, "body_html": body_html}
	)
