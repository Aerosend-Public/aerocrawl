"""Academic route — arXiv, PubMed, Crossref DOI, OpenAlex.

Covers most research-paper URLs. All free, all no-auth (optional key for NCBI
bumps rate limit 3→10 rps).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.routes.base import RouteResult

logger = structlog.get_logger()

_ARXIV_API = "https://export.arxiv.org/api/query"
_PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_CROSSREF_API = "https://api.crossref.org/works"
_OPENALEX_API = "https://api.openalex.org/works"

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,6})(v\d+)?")


class AcademicRoute:
    name = "academic"
    description = "arXiv, PubMed, Crossref, OpenAlex via their APIs"

    def matches(self, url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        markers = (
            "arxiv.org",
            "pubmed.ncbi.nlm.nih.gov",
            "ncbi.nlm.nih.gov/pubmed",
            "doi.org",
            "dx.doi.org",
            "openalex.org",
        )
        return any(m in host or m in url.lower() for m in markers)

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        lower = url.lower()
        host = urlparse(url).netloc.lower()

        if "arxiv.org" in host:
            return await self._fetch_arxiv(url)
        if "pubmed.ncbi.nlm.nih.gov" in host or "ncbi.nlm.nih.gov/pubmed" in lower:
            return await self._fetch_pubmed(url)
        if "doi.org" in host:
            return await self._fetch_doi(url)
        if "openalex.org" in host:
            return await self._fetch_openalex(url)
        return None

    async def _fetch_arxiv(self, url: str) -> Optional[RouteResult]:
        m = _ARXIV_ID_RE.search(url)
        if not m:
            return None
        arxiv_id = m.group(1)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    _ARXIV_API,
                    params={"id_list": arxiv_id, "max_results": 1},
                )
        except Exception as exc:
            logger.debug("arxiv: request failed", error=str(exc))
            return None
        if resp.status_code != 200:
            return None

        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return None

        entry = root.find("atom:entry", ns)
        if entry is None:
            return None

        def _text(tag: str) -> str:
            el = entry.find(tag, ns)
            return (el.text or "").strip() if el is not None else ""

        title = _text("atom:title")
        summary = _text("atom:summary")
        published = _text("atom:published")
        authors = [(a.findtext("atom:name", namespaces=ns) or "").strip() for a in entry.findall("atom:author", ns)]
        pdf_link = ""
        for link in entry.findall("atom:link", ns):
            if link.get("type") == "application/pdf":
                pdf_link = link.get("href") or ""

        md = f"# {title}\n\n"
        md += f"**arXiv:** {arxiv_id}"
        if authors:
            md += f" · **Authors:** {', '.join(authors)}"
        if published:
            md += f" · **Published:** {published}"
        md += "\n\n"
        if pdf_link:
            md += f"PDF: {pdf_link}\n\n"
        if summary:
            md += "## Abstract\n\n" + summary + "\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": title, "description": summary[:300], "source_url": url, "status_code": 200},
            route_name="academic:arxiv",
            raw_data={"arxiv_id": arxiv_id, "pdf_url": pdf_link},
        )

    async def _fetch_pubmed(self, url: str) -> Optional[RouteResult]:
        m = re.search(r"/(\d{6,10})", url)
        if not m:
            return None
        pmid = m.group(1)

        params = {"db": "pubmed", "id": pmid, "retmode": "xml"}
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(_PUBMED_EFETCH, params=params)
        except Exception:
            return None
        if resp.status_code != 200:
            return None

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return None

        article = root.find(".//PubmedArticle")
        if article is None:
            return None

        title_el = article.find(".//ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else ""
        abstract_el = article.find(".//Abstract")
        abstract = "".join(abstract_el.itertext()) if abstract_el is not None else ""
        journal = article.findtext(".//Journal/Title") or ""
        year = article.findtext(".//PubDate/Year") or ""

        authors = []
        for auth in article.findall(".//Author"):
            ln = auth.findtext("LastName") or ""
            fn = auth.findtext("ForeName") or ""
            if ln:
                authors.append(f"{fn} {ln}".strip())

        md = f"# {title}\n\n"
        md += f"**PMID:** {pmid}"
        if journal:
            md += f" · **Journal:** {journal}"
        if year:
            md += f" · **Year:** {year}"
        md += "\n\n"
        if authors:
            md += f"**Authors:** {', '.join(authors[:10])}{'…' if len(authors) > 10 else ''}\n\n"
        if abstract:
            md += "## Abstract\n\n" + abstract + "\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": title, "description": abstract[:300], "source_url": url, "status_code": 200},
            route_name="academic:pubmed",
            raw_data={"pmid": pmid},
        )

    async def _fetch_doi(self, url: str) -> Optional[RouteResult]:
        # doi.org/<doi> → Crossref metadata
        parsed = urlparse(url)
        doi = parsed.path.lstrip("/")
        if not doi:
            return None

        headers = {"User-Agent": "NinjaScraper/3.0 (mailto:aerocrawl@example.com)"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{_CROSSREF_API}/{doi}", headers=headers)
        except Exception:
            return None
        if resp.status_code != 200:
            return None

        try:
            data = resp.json().get("message", {})
        except Exception:
            return None

        title = (data.get("title") or [""])[0]
        abstract = data.get("abstract") or ""
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (data.get("author") or [])
        ]
        container = (data.get("container-title") or [""])[0]
        year = ""
        issued = data.get("issued", {}).get("date-parts", [[]])
        if issued and issued[0]:
            year = str(issued[0][0])
        publisher = data.get("publisher", "")

        md = f"# {title}\n\n"
        md += f"**DOI:** {doi}"
        if container:
            md += f" · **Publication:** {container}"
        if year:
            md += f" · **Year:** {year}"
        md += "\n\n"
        if authors:
            md += f"**Authors:** {', '.join(authors[:10])}{'…' if len(authors) > 10 else ''}\n\n"
        if publisher:
            md += f"**Publisher:** {publisher}\n\n"
        if abstract:
            # Crossref abstracts often include jats XML; strip tags
            clean = re.sub(r"<[^>]+>", "", abstract)
            md += "## Abstract\n\n" + clean + "\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": title, "description": re.sub(r"<[^>]+>", "", abstract)[:300], "source_url": url, "status_code": 200},
            route_name="academic:doi",
            raw_data={"doi": doi},
        )

    async def _fetch_openalex(self, url: str) -> Optional[RouteResult]:
        # openalex.org/W...
        m = re.search(r"/(W\d+)", url)
        if not m:
            return None
        work_id = m.group(1)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{_OPENALEX_API}/{work_id}")
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None

        title = data.get("title", "")
        abstract_idx = data.get("abstract_inverted_index") or {}
        # Reconstruct abstract from inverted index
        abstract = ""
        if abstract_idx:
            words = [""] * (max((i for idxs in abstract_idx.values() for i in idxs), default=0) + 1)
            for word, indices in abstract_idx.items():
                for i in indices:
                    if i < len(words):
                        words[i] = word
            abstract = " ".join(w for w in words if w)

        authors = [a.get("author", {}).get("display_name", "") for a in (data.get("authorships") or [])]
        year = data.get("publication_year")
        venue = (data.get("primary_location") or {}).get("source", {}).get("display_name", "")

        md = f"# {title}\n\n"
        md += f"**OpenAlex:** {work_id}"
        if venue:
            md += f" · **Venue:** {venue}"
        if year:
            md += f" · **Year:** {year}"
        md += "\n\n"
        if authors:
            md += f"**Authors:** {', '.join(a for a in authors if a)[:1000]}\n\n"
        if abstract:
            md += "## Abstract\n\n" + abstract + "\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": title, "description": abstract[:300], "source_url": url, "status_code": 200},
            route_name="academic:openalex",
            raw_data={"work_id": work_id},
        )
