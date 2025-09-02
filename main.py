import os
from typing import List, Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from pydantic import BaseModel, HttpUrl

# ----- Models -----
class Listing(BaseModel):
    id: str
    source: str
    title: str
    description: Optional[str] = None
    url: Optional[HttpUrl] = None
    image: Optional[str] = None
    location_name: Optional[str] = None
    posted_at: Optional[datetime] = None
    price: float = 0.0

# ----- App & CORS -----
app = FastAPI(title="Freebie Aggregator", version="0.1.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

# ----- Craigslist connector (Free section) -----
CL_DOMAIN = "https://lasvegas.craigslist.org"

def cl_build_url(zip_code: str, radius_miles: float, query: Optional[str]) -> str:
    params = [
        ("postal", zip_code or ""),
        ("search_distance", str(int(radius_miles))),
        ("hasPic", "1"),
        ("bundleDuplicates", "1"),
    ]
    if query:
        params.append(("query", query))
    qs = "&".join(f"{k}={httpx.QueryParams({k:v})[k]}" for k, v in params)
    return f"{CL_DOMAIN}/search/zip?{qs}"

def cl_parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except Exception:
        return None

async def cl_fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FreebieAggregator/1.0; +https://example.com)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text

def cl_extract_listings(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: List[Listing] = []
    items = soup.select("li.cl-static-search-result") or soup.select("li.result-row")
    for li in items:
        a = li.select_one("a.cl-app-anchor") or li.select_one("a.result-title") or li.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = CL_DOMAIN + href
        title = (a.get_text(strip=True) or "Free item")
        hood = li.select_one("span.cl-location") or li.select_one("span.result-hood")
        location_name = (hood.get_text(strip=True).strip("()") if hood else None)
        time_el = li.select_one("time")
        posted_at = cl_parse_datetime(time_el.get("datetime") if time_el else None)
        img = li.select_one("img")
        img_url = img.get("src") if img else None
        listings.append(Listing(
            id=href,
            source="craigslist",
            title=title,
            description=None,
            url=href,
            image=img_url,
            location_name=location_name,
            posted_at=posted_at,
            price=0.0,
        ))
    return listings

@app.get("/search", response_model=List[Listing])
async def search(
    zip: Optional[str] = None,
    radius_miles: float = Query(25.0, ge=5.0, le=100.0),
    query: Optional[str] = None,
    sources: str = "craigslist"
):
    zip_code = zip or "89101"
    url = cl_build_url(zip_code, radius_miles, query)
    html = await cl_fetch_html(url)
    combined = cl_extract_listings(html)
    combined.sort(key=lambda x: (x.posted_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return combined
