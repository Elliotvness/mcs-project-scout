import csv
import io
import json
import os
from pathlib import Path
from urllib.parse import quote_plus
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
LEADS_FILE = BASE_DIR / "leads.json"
PIPELINE_TASKS_FILE = BASE_DIR / "lead_pipeline_tasks.json"
INDEX_FILE = BASE_DIR / "index.html"

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


@app.post("/api/rescan")
async def rescan_feeds():
    global LEADS_DB
    LEADS_DB = load_leads_db()
    return {"ok": True, "count": len(LEADS_DB)}


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

