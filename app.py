import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
LEADS_FILE = BASE_DIR / "leads.json"
PIPELINE_TASKS_FILE = BASE_DIR / "lead_pipeline_tasks.json"
INDEX_FILE = BASE_DIR / "index.html"
GUIDE_FILE = BASE_DIR / "guide.html"

app = FastAPI(title="MCS Project Scout & Warehouse Lead Engine")


def load_leads_db():
    if LEADS_FILE.exists():
        return json.loads(LEADS_FILE.read_text(encoding="utf-8"))
    return []


def load_pipeline_tasks():
    if PIPELINE_TASKS_FILE.exists():
        return json.loads(PIPELINE_TASKS_FILE.read_text(encoding="utf-8"))
    return []


def save_leads_db(leads):
    LEADS_FILE.write_text(json.dumps(leads, indent=2), encoding="utf-8")


LEADS_DB = load_leads_db()


@app.get("/")
async def index():
    return FileResponse(INDEX_FILE)


@app.get("/guide")
@app.get("/guide.html")
async def team_guide():
    return FileResponse(GUIDE_FILE)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "leads_count": len(LEADS_DB)}


@app.get("/api/leads")
async def get_leads():
    return JSONResponse(LEADS_DB)


@app.get("/api/pipeline-tasks")
async def get_pipeline_tasks():
    return JSONResponse(load_pipeline_tasks())


@app.post("/api/leads/{lead_id}/update")
async def update_lead_contact_and_dates(lead_id: str, request: Request):
    payload = await request.json()
    allowed_fields = (
        "contact_name",
        "contact_title",
        "contractor_or_applicant",
        "contact_email",
        "contact_phone",
        "filing_date",
        "target_date",
    )
    for lead in LEADS_DB:
        if lead.get("id") == lead_id:
            for field in allowed_fields:
                if field in payload:
                    lead[field] = payload[field]
            save_leads_db(LEADS_DB)
            return {"ok": True, "lead": lead}
    return JSONResponse({"ok": False, "error": "Lead not found"}, status_code=404)


@app.post("/api/leads/{lead_id}/status")
async def update_lead_status(lead_id: str, request: Request):
    payload = await request.json()
    new_status = payload.get("status", "NEW")
    for lead in LEADS_DB:
        if lead.get("id") == lead_id:
            lead["lead_status"] = new_status
            save_leads_db(LEADS_DB)
            return {"ok": True, "status": new_status}
    return JSONResponse({"ok": False, "error": "Lead not found"}, status_code=404)


@app.post("/api/estimate")
async def compute_estimate(request: Request):
    payload = await request.json()
    sqft = int(payload.get("sqft") or 31500)
    height = int(payload.get("clear_height") or 28)
    beam_levels = 5 if height >= 32 else (4 if height >= 26 else 3)
    total_bays = max(12, round(sqft / 340))
    pallet_capacity = total_bays * (beam_levels + 1) * 2
    budgetary_min = round((pallet_capacity * 82) / 100) * 100
    budgetary_max = round((pallet_capacity * 108) / 100) * 100
    return {
        "pallet_capacity": pallet_capacity,
        "total_bays": total_bays,
        "budgetary_min": budgetary_min,
        "budgetary_max": budgetary_max,
    }


@app.get("/api/leads/{lead_id}/dossier")
async def get_lead_dossier(lead_id: str):
    lead = next((l for l in LEADS_DB if l.get("id") == lead_id), None)
    if not lead:
        return JSONResponse({"error": "Not found"}, status_code=404)

    company = lead.get("company_name", "Facility Operator")
    addr = lead.get("address", "")
    city = lead.get("city", "Southern California")
    pallets = lead.get("est_pallet_positions") or 1000
    bays = lead.get("est_bay_count") or 100
    product = lead.get("primary_product", "Selective Pallet Rack")
    contact_name = lead.get("contact_name") or "Operations / Supply Chain Lead"
    filing_date = lead.get("filing_date") or "recent"

    subject = f"{addr} ({city}) — Preliminary {pallets:,}-Pallet CAD Layout & Ch. 32 Seismic Packet"
    email_body = (
        f"Hi {contact_name},\n\n"
        f"Our engineering team at McMurray Stern (Santa Fe Springs HQ) noticed your {filing_date} "
        f"activity at {addr} in {city}.\n\n"
        f"To help streamline municipal building & California Chapter 32 High-Pile Combustible Storage (HPCS) "
        f"plan check, we modeled a preliminary {product} concept yielding ~{pallets:,} pallet positions "
        f"across {bays:,} bays (including California PE-stamped ASCE 7-22 seismic calculations and 87-layer AutoCAD submittals).\n\n"
        f"Are you open to a quick 10-minute site walk or review of the preliminary CAD layout this week?\n\n"
        f"Best regards,\n"
        f"McMurray Stern — Storage & Automation Engineering\n"
        f"15020 Marquardt Ave, Santa Fe Springs, CA 90670"
    )
    phone_battlecard = (
        f"OPENER: 'Hi {contact_name}, calling from McMurray Stern in Santa Fe Springs regarding the warehouse project at {addr} in {city}.'\n\n"
        f"HOOK: 'We saw the {filing_date} filing ({lead.get('source_reference', 'N/A')}) and already ran a preliminary {pallets:,}-pallet {product} layout with California Zone D/E seismic calcs so your team doesn't get delayed in Fire Marshal High-Pile plan check.'\n\n"
        f"ASK: 'Who is overseeing the rack layout and permitting timeline for {addr}?'"
    )
    linkedin_q = quote_plus(f"{company} {lead.get('contact_title', 'Director Logistics')} Southern California")
    return {
        "subject": subject,
        "email_body": email_body,
        "phone_battlecard": phone_battlecard,
        "linkedin_url": f"https://www.linkedin.com/search/results/people/?keywords={linkedin_q}",
    }


EXCLUDED_KEYWORDS = ("costar.com", "loopnet.com", "cold storage", "cold-storage", "freezer", "amazon.com", "amazon fulfillment")
TARGET_MARKETS = {
    "santa fe springs": ("Santa Fe Springs", "Los Angeles County", "CA", 33.9472, -118.0853),
    "commerce": ("Commerce", "Los Angeles County", "CA", 34.0006, -118.1598),
    "whittier": ("Whittier", "Los Angeles County", "CA", 33.9792, -118.0328),
    "norwalk": ("Norwalk", "Los Angeles County", "CA", 33.9022, -118.0817),
    "torrance": ("Torrance", "Los Angeles County", "CA", 33.8358, -118.3406),
    "carson": ("Carson", "Los Angeles County", "CA", 33.8317, -118.2617),
    "long beach": ("Long Beach", "Los Angeles County", "CA", 33.7701, -118.1937),
    "rancho dominguez": ("Rancho Dominguez", "Los Angeles County", "CA", 33.8650, -118.2250),
    "vernon": ("Vernon", "Los Angeles County", "CA", 34.0039, -118.2301),
    "tustin": ("Tustin", "Orange County", "CA", 33.7458, -117.8262),
    "irvine": ("Irvine", "Orange County", "CA", 33.6846, -117.8265),
    "anaheim": ("Anaheim", "Orange County", "CA", 33.8366, -117.9143),
    "ontario": ("Ontario", "San Bernardino County", "CA", 34.0633, -117.6509),
    "chino": ("Chino", "San Bernardino County", "CA", 34.0122, -117.6889),
    "rialto": ("Rialto", "San Bernardino County", "CA", 34.1064, -117.3703),
    "fontana": ("Fontana", "San Bernardino County", "CA", 34.0922, -117.4350),
    "san bernardino": ("San Bernardino", "San Bernardino County", "CA", 34.1083, -117.2898),
    "perris": ("Perris", "Riverside County", "CA", 33.7825, -117.2286),
    "jurupa valley": ("Jurupa Valley", "Riverside County", "CA", 33.9972, -117.4855),
    "hesperia": ("Hesperia", "San Bernardino County", "CA", 34.4264, -117.3009),
    "apple valley": ("Apple Valley", "San Bernardino County", "CA", 34.5008, -117.1859),
    "phoenix": ("Phoenix", "Maricopa County", "AZ", 33.4484, -112.0740),
    "glendale": ("Glendale", "Maricopa County", "AZ", 33.5387, -112.1860),
    "surprise": ("Surprise", "Maricopa County", "AZ", 33.6292, -112.3679),
    "scottsdale": ("Scottsdale", "Maricopa County", "AZ", 33.4942, -111.9261),
    "las vegas": ("Las Vegas", "Clark County", "NV", 36.1699, -115.1398),
    "north las vegas": ("North Las Vegas", "Clark County", "NV", 36.1989, -115.1175),
    "reno": ("Reno", "Washoe County", "NV", 39.5296, -119.8138),
    "sparks": ("Sparks", "Washoe County", "NV", 39.5349, -119.7527),
    "fremont": ("Fremont", "Alameda County", "CA", 37.5485, -121.9886),
    "livermore": ("Livermore", "Alameda County", "CA", 37.6819, -121.7680),
    "san jose": ("San Jose", "Santa Clara County", "CA", 37.3382, -121.8863),
    "sacramento": ("Sacramento", "Sacramento County", "CA", 38.5816, -121.4944),
    "stockton": ("Stockton", "San Joaquin County", "CA", 37.9577, -121.2908),
    "tracy": ("Tracy", "San Joaquin County", "CA", 37.7397, -121.4252),
}


def is_excluded_lead(lead_dict: dict) -> bool:
    blob = " ".join(
        str(lead_dict.get(k) or "")
        for k in ("company_name", "raw_work_desc", "summary_info", "source_url", "notes")
    ).lower()
    return any(term in blob for term in EXCLUDED_KEYWORDS)


def compute_recency_and_score(lead: dict, now_dt: datetime) -> bool:
    """Recompute dynamic Recency (1-5) and Weighted Total Score based on filing_date."""
    date_str = str(lead.get("filing_date") or lead.get("date_discovered") or "")[:10]
    days_old = 45
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_old = max(0, (now_dt - dt).days)
        except ValueError:
            days_old = 45

    recency_score = 5 if days_old <= 30 else (4 if days_old <= 90 else (3 if days_old <= 180 else (2 if days_old <= 365 else 1)))
    sb = lead.get("score_breakdown")
    if isinstance(sb, dict):
        sb["recency"] = recency_score
        has_direct = bool(lead.get("contact_phone") or lead.get("contact_email"))
        if has_direct and sb.get("reachability", 3) < 4:
            sb["reachability"] = 4
        total = round(
            sb.get("recency", 4) * 0.20
            + sb.get("size_fit", 4) * 0.15
            + sb.get("new_space", 4) * 0.20
            + sb.get("growth", 4) * 0.20
            + sb.get("reachability", 4) * 0.25,
            2,
        )
        lead["weighted_total_score"] = total
        lead["priority_level"] = "Urgent" if total >= 4.15 else ("High" if total >= 3.50 else "Normal")
        lead["last_verified_at"] = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return True
    lead["last_verified_at"] = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return False


def fetch_live_ladbs_permits(existing_ids: set, now_dt: datetime) -> list:
    """Pull recent commercial warehouse/storage rack permits from City of LA Open Data API."""
    new_items = []
    query = (
        "$where=(valuation >= 50000) AND ("
        "upper(work_description) like '%WAREHOUSE%' OR "
        "upper(work_description) like '%STORAGE RACK%' OR "
        "upper(work_description) like '%PALLET RACK%' OR "
        "upper(work_description) like '%HIGH PILE%')"
        "&$order=issue_date DESC&$limit=15"
    )
    url = f"https://data.lacity.org/resource/pi9x-tg5x.json?{urllib.parse.quote(query, safe='=$&()><%_')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MCS-Project-Scout/2.4"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        for r in rows:
            pcis = r.get("pcis_permit") or r.get("permit_nbr")
            if not pcis:
                continue
            lid = f"permit-{pcis}"
            if lid in existing_ids:
                continue
            desc = (r.get("work_description") or "").strip()
            if any(k in desc.lower() for k in EXCLUDED_KEYWORDS):
                continue
            addr = f"{r.get('address_start', '')} {r.get('street_direction', '')} {r.get('street_name', '')} {r.get('street_suffix', '')}".strip()
            addr = re.sub(r"\s+", " ", addr) or "Los Angeles Industrial Corridor"
            val = float(r.get("valuation") or 120000)
            sqft = max(25000, min(250000, round(val / 6.5)))
            pallets = max(450, round((sqft / 340) * 10))
            bays = max(45, round(sqft / 340))
            issue_dt = (r.get("issue_date") or now_dt.strftime("%Y-%m-%d"))[:10]
            lead = {
                "id": lid,
                "company_name": f"Warehouse Permit Filing ({addr})",
                "lifecycle": "BUILDING_PERMIT",
                "source": "LADBS Building Permit Feed (Live API)",
                "source_reference": f"LADBS #{pcis}",
                "source_url": "https://data.lacity.org/Housing-and-Real-Estate/Building-and-Safety-Permit-Information-Old/pi9x-tg5x",
                "address": addr,
                "city": "Los Angeles",
                "county": "Los Angeles County",
                "state": "CA",
                "lat": float(r.get("latitude") or 34.0200),
                "lon": float(r.get("longitude") or -118.2400),
                "lead_status": "NEW",
                "filing_date": issue_dt,
                "target_date": "Q1/Q2 Plan Check",
                "sq_ft": sqft,
                "clear_height_ft": 30,
                "raw_work_desc": desc,
                "summary_info": f"Live LADBS warehouse/racking permit ({pcis}) at {addr}. Valuation: ${val:,.0f}. Est. {pallets:,} pallet positions.",
                "valuation": val,
                "primary_product": "Selective Pallet Rack",
                "score": "TIER_1_HOT" if val >= 150000 else "TIER_2_WARM",
                "score_rate": 89 if val >= 150000 else 78,
                "score_grade": "A" if val >= 150000 else "B+",
                "score_reasons": [
                    f"Active LADBS warehouse permit (${val:,.0f} valuation)",
                    "California Ch. 32 High-Pile & ASCE 7-22 seismic engineering required",
                ],
                "est_pallet_positions": pallets,
                "est_bay_count": bays,
                "est_budget_min": round(pallets * 82, -2),
                "est_budget_max": round(pallets * 108, -2),
                "contact_name": r.get("applicant_first_name", "Permit Applicant / GC"),
                "contact_title": f"Permit Applicant (LADBS #{pcis})",
                "contact_phone": "(213) 482-0000",
                "contact_email": None,
                "contractor_or_applicant": r.get("contractors_business_name") or f"LADBS Permit #{pcis}",
                "contact_linkedin_query": f"{addr} Los Angeles Warehouse Operations",
                "date_discovered": now_dt.isoformat(),
                "last_verified_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            new_items.append(lead)
            existing_ids.add(lid)
    except Exception:
        pass
    return new_items


def fetch_live_cre_rss_leads(existing_urls: set, now_dt: datetime) -> list:
    """Scan approved CRE RSS feeds for new 50K+ SF warehouse/distribution center leases."""
    feeds = [
        ("REBusiness Online", "https://rebusinessonline.com/feed/"),
        ("CommercialSearch Industrial", "https://www.commercialsearch.com/news/feed/"),
    ]
    new_leads = []
    for pub_name, feed_url in feeds:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "MCS-Project-Scout/2.4"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            for item in root.findall(".//item")[:25]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()
                blob = f"{title} {desc}".lower()
                if not link or link in existing_urls:
                    continue
                if any(ex in blob for ex in EXCLUDED_KEYWORDS):
                    continue
                if any(weak in blob for weak in ("renewal", "renews", "refinanc", "multifamily", "apartment", "retail center", "hotel", "medical office")):
                    continue
                if not any(sig in blob for sig in ("lease", "distribution center", "fulfillment", "warehouse", "industrial")):
                    continue
                matched_mkt = None
                for mkt_key, mkt_info in TARGET_MARKETS.items():
                    if mkt_key in blob:
                        matched_mkt = mkt_info
                        break
                if not matched_mkt:
                    continue
                sf_match = re.search(r"(\d{2,3},\d{3})\s*(?:square[- ]foot|sq\.?\s*ft\.?|sf)", blob, re.I)
                sqft = int(sf_match.group(1).replace(",", "")) if sf_match else 100000
                if sqft < 40000:
                    continue
                city, county, state, lat, lon = matched_mkt
                pallets = round((sqft / 340) * 10)
                bays = round(sqft / 340)
                lid = f"CRE-LIVE-{ abs(hash(link)) % 100000 }"
                sb = {"recency": 5, "size_fit": 5 if 100000 <= sqft <= 500000 else 4, "new_space": 5, "growth": 4, "reachability": 4}
                total_score = round(sb["recency"]*0.2 + sb["size_fit"]*0.15 + sb["new_space"]*0.2 + sb["growth"]*0.2 + sb["reachability"]*0.25, 2)
                lead = {
                    "id": lid,
                    "company_name": title[:68],
                    "lifecycle": "NEW_CONSTRUCTION",
                    "source": f"{pub_name} (Live RSS)",
                    "source_reference": now_dt.strftime("%Y-%m-%d"),
                    "source_url": link,
                    "address": f"{city} Industrial Corridor, {city}, {state}",
                    "city": city,
                    "county": county,
                    "state": state,
                    "lat": lat,
                    "lon": lon,
                    "lead_status": "Not contacted",
                    "filing_date": now_dt.strftime("%Y-%m-%d"),
                    "target_date": "Immediate / Q1 Move-In",
                    "sq_ft": sqft,
                    "clear_height_ft": 32,
                    "raw_work_desc": title,
                    "summary_info": f"{title} ({sqft:,} SF in {city}, {state}). Est. {pallets:,} pallet positions across {bays:,} bays.",
                    "valuation": round(sqft * 3.2),
                    "primary_product": "Selective Pallet Rack & Wire Decking",
                    "score": "TIER_1_HOT",
                    "score_rate": int(round(total_score * 20)),
                    "score_grade": "A+",
                    "weighted_total_score": total_score,
                    "priority_level": "Urgent" if total_score >= 4.15 else "High",
                    "score_breakdown": sb,
                    "score_reasons": [
                        f"Weighted Score: {total_score:.2f}/5.00 — Newly published {sqft:,} SF industrial signal",
                        f"Source: {pub_name}",
                    ],
                    "est_pallet_positions": pallets,
                    "est_bay_count": bays,
                    "est_budget_min": round(pallets * 82, -2),
                    "est_budget_max": round(pallets * 108, -2),
                    "contact_name": f"Leasing Brokerage / Tenant Ops ({city})",
                    "contact_title": f"Industrial Transaction Team (CCPA Source: {pub_name})",
                    "contact_phone": None,
                    "contact_email": None,
                    "contractor_or_applicant": f"Source: {pub_name} ({city}, {state})",
                    "contact_linkedin_query": f"{title[:40]} {city} Logistics Operations",
                    "date_discovered": now_dt.isoformat(),
                    "last_verified_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                new_leads.append(lead)
                existing_urls.add(link)
        except Exception:
            continue
    return new_leads


@app.post("/api/rescan")
async def rescan_feeds():
    global LEADS_DB
    now_dt = datetime.now(timezone.utc)
    leads = load_leads_db()

    # 1. Filter out any prohibited sources/categories (CoStar, LoopNet, Cold Storage, Amazon)
    clean_leads = [l for l in leads if not is_excluded_lead(l)]

    # 2. Recompute dynamic Recency (1-5) and Weighted Total Score across all leads
    rescored_count = 0
    for l in clean_leads:
        if compute_recency_and_score(l, now_dt):
            rescored_count += 1

    # 3. Query live LADBS Open Data API & CRE RSS feeds for net-new activity
    existing_ids = {l.get("id") for l in clean_leads if l.get("id")}
    existing_urls = {l.get("source_url") for l in clean_leads if l.get("source_url")}

    new_permits = fetch_live_ladbs_permits(existing_ids, now_dt)
    new_leases = fetch_live_cre_rss_leads(existing_urls, now_dt)

    if new_permits or new_leases:
        clean_leads.extend(new_permits)
        clean_leads.extend(new_leases)

    # 4. Sort by weighted_total_score (1-5) then score_rate descending
    clean_leads.sort(
        key=lambda x: (x.get("weighted_total_score") or 0, x.get("score_rate") or 0),
        reverse=True,
    )

    LEADS_DB = clean_leads
    save_leads_db(LEADS_DB)

    return {
        "ok": True,
        "count": len(LEADS_DB),
        "rescored": rescored_count,
        "new_permits": len(new_permits),
        "new_leases": len(new_leases),
        "last_verified_at": now_dt.strftime("%Y-%m-%d %H:%M UTC"),
    }


@app.get("/api/export-csv")
async def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Score", "Grade", "Weighted_Score_1_to_5", "Priority", "Company", "Lifecycle",
        "Address", "City", "County", "Filing_Date", "Target_Date", "Contact_Name",
        "Contact_Title", "Contact_Email", "Contact_Phone", "Applicant_GC", "Sq_Ft",
        "Clear_Height_Ft", "Est_Pallets", "Est_Bays", "Budget_Min", "Budget_Max",
        "Primary_Product", "Status", "Source", "Reference", "Source_URL"
    ])
    for l in LEADS_DB:
        writer.writerow([
            l.get("id"), l.get("score_rate"), l.get("score_grade"),
            l.get("weighted_total_score"), l.get("priority_level"), l.get("company_name"),
            l.get("lifecycle"), l.get("address"), l.get("city"), l.get("county"),
            l.get("filing_date"), l.get("target_date"), l.get("contact_name"),
            l.get("contact_title"), l.get("contact_email"), l.get("contact_phone"),
            l.get("contractor_or_applicant"), l.get("sq_ft"), l.get("clear_height_ft"),
            l.get("est_pallet_positions"), l.get("est_bay_count"), l.get("est_budget_min"),
            l.get("est_budget_max"), l.get("primary_product"), l.get("lead_status"),
            l.get("source"), l.get("source_reference"), l.get("source_url")
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mcs_project_scout_leads.csv"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

