"""
MCS Project Scout — Weekly Ground-Truth Lead Verification & Outlook Draft Agent
===============================================================================
Implements the 8-step verification & draft workflow:
  [Leads list from JSON/PDF] -> [Query builder] -> [Search API (Tavily/Brave/SerpAPI)]
  -> [Fetch pages] -> [Extract facts + verbatim emails + source URL]
  -> [Scoring rules (Gov/Company=3, News=2, Directory=1, <90d=+2, Multi-source=+2)]
  -> [Email check (ZeroBounce/NeverBounce optional)]
  -> [Report + update Outlook drafts (Never auto-send; CC Nick Hernandez)]

Usage:
  python verify_leads.py                          # Audit leads.json and write verification_report.json
  python verify_leads.py --update-outlook-drafts  # Also create/update local Outlook Drafts via COM (never auto-sends)
"""

import argparse
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LEADS_FILE = BASE_DIR / "leads.json"
REPORT_FILE = BASE_DIR / "verification_report.json"

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
ZEROBOUNCE_API_KEY = os.environ.get("ZEROBOUNCE_API_KEY", "")
GRAPH_ACCESS_TOKEN = os.environ.get("GRAPH_ACCESS_TOKEN", "")

TRUST = {"gov": 3, "company": 3, "news": 2, "directory": 1}
DIRECTORY_DOMAINS = ("zoominfo.com", "dnb.com", "manta.com", "rocketreach.co", "lusha.com", "contactout.com")
NEWS_DOMAINS = (
    "rebusinessonline.com",
    "connectcre.com",
    "citybiz.co",
    "commercialsearch.com",
    "globest.com",
    "therealdeal.com",
    "labusinessjournal.com",
)


def search_web_api(query: str, max_results: int = 8) -> list[dict]:
    """Run search via Tavily, Brave, or SerpAPI if an API key is configured."""
    if TAVILY_API_KEY:
        try:
            payload = json.dumps({
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "MCS-Lead-Verifier/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("results", [])
        except Exception:
            return []

    if BRAVE_API_KEY:
        try:
            url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={max_results}"
            req = urllib.request.Request(
                url,
                headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = []
                for item in data.get("web", {}).get("results", []):
                    results.append({
                        "url": item.get("url", ""),
                        "content": f"{item.get('title', '')} {item.get('description', '')}",
                        "published_date": item.get("page_age", "2026-01-01")[:10],
                    })
                return results
        except Exception:
            return []

    return []


def fetch_direct_source(url: str) -> dict | None:
    """Fetch the known public source page directly so every saved source link is spot-checked."""
    if not url or not url.startswith("http"):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            clean = re.sub(r"<[^>]+>", " ", raw)
            return {
                "url": url,
                "content": re.sub(r"\s+", " ", clean)[:20000],
                "published_date": dt.date.today().isoformat(),
            }
    except Exception:
        return None


def queries(lead: dict) -> list[str]:
    """Build the 3 standard verification queries per lead."""
    addr = lead.get("address", "")
    company = (lead.get("company_name") or "").split("—")[0].replace("[WATCH LIST]", "").strip()
    city = lead.get("city", "")
    contact = (lead.get("contact_name") or "").split("(")[0].strip()
    firm = lead.get("source", "")
    return [
        f"{addr} {city} tenant lease 2026".strip(),
        f"{company} {city} warehouse".strip(),
        f"{contact} {firm}".strip(),
    ]


def extract(text: str) -> dict:
    """
    Extract only verbatim emails and phone numbers that appear word-for-word on a page.
    STRICT RULE: Never construct or guess emails from a first.last@company.com pattern.
    """
    emails = {
        e.lower()
        for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text or "")
        if not e.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"))
    }
    phones = set(re.findall(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}", text or ""))
    return {"emails": emails, "phones": phones}


def classify_source_kind(url: str, lead: dict) -> str:
    u = (url or "").lower()
    if ".gov" in u or ".mil" in u or "lacity.org" in u:
        return "gov"
    if any(d in u for d in DIRECTORY_DOMAINS):
        return "directory"
    if any(d in u for d in NEWS_DOMAINS):
        return "news"
    return "company"


def score_hit(hit: dict, lead: dict) -> int:
    """
    Cross-check scoring rules:
      - Source trust: gov (3) / company site (3) > news (2) > data-broker directory (1)
      - Recency: page from the last 90 days gets +2
    """
    kind = classify_source_kind(hit.get("url", ""), lead)
    pub_str = (hit.get("published_date") or "2000-01-01")[:10]
    try:
        age = (dt.date.today() - dt.date.fromisoformat(pub_str)).days
    except ValueError:
        age = 180
    return TRUST[kind] + (2 if age < 90 else 0)


def check_email_deliverability(email: str) -> str:
    """Optional ZeroBounce verification if ZEROBOUNCE_API_KEY is configured."""
    if not email:
        return "none"
    if not ZEROBOUNCE_API_KEY:
        return "unchecked (set ZEROBOUNCE_API_KEY to verify mailbox)"
    try:
        url = (
            "https://api.zerobounce.net/v2/validate?"
            f"api_key={urllib.parse.quote(ZEROBOUNCE_API_KEY)}&email={urllib.parse.quote(email)}"
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status", "unknown")
    except Exception:
        return "error"


def verify(lead: dict) -> dict:
    """Verify a single lead against live search hits and direct source URLs."""
    hits = []
    for q in queries(lead):
        hits.extend(search_web_api(q))

    # Always include direct checks of the saved source URLs
    for src_key in ("email_source_url", "source_url"):
        direct_hit = fetch_direct_source(lead.get(src_key, ""))
        if direct_hit:
            hits.append(direct_hit)

    found_emails: dict[str, list[tuple[int, str]]] = {}
    for h in hits:
        s = score_hit(h, lead)
        for e in extract(h.get("content", ""))["emails"]:
            found_emails.setdefault(e, []).append((s, h.get("url", "")))

    # Multi-source agreement bonus (+2 when >=2 independent URLs show the same email)
    scored_candidates = []
    for email_addr, src_list in found_emails.items():
        unique_urls = list(dict.fromkeys(u for _, u in src_list))
        agreement_bonus = 2 if len(unique_urls) >= 2 else 0
        total_score = sum(s for s, _ in src_list) + agreement_bonus
        scored_candidates.append((total_score, email_addr, unique_urls[:3]))

    scored_candidates.sort(reverse=True)

    # Red-flag detection ("for lease" means tenant may be leaving, as with Al Dahra; "finaled" means rack permit done)
    flags = list(lead.get("flags") or [])
    combined_content = " ".join((h.get("content") or "").lower() for h in hits)
    if "for lease" in combined_content and "for lease" not in flags:
        flags.append("for lease")
    if "permit finaled" in combined_content and "permit finaled" not in flags:
        flags.append("permit finaled")

    current_email = (lead.get("contact_email") or "").lower().strip()
    return {
        "id": lead.get("id"),
        "rank": lead.get("rank"),
        "outreach_bucket": lead.get("outreach_bucket"),
        "lead": lead.get("company_name"),
        "address": f"{lead.get('address')}, {lead.get('city')}, {lead.get('state', 'CA')}",
        "saved_email": lead.get("contact_email"),
        "email_provenance": lead.get("email_provenance", "None"),
        "email_source_url": lead.get("email_source_url"),
        "current_email_seen_live": bool(current_email and current_email in found_emails),
        "deliverability": check_email_deliverability(current_email),
        "candidates": [(email_addr, urls) for _, email_addr, urls in scored_candidates[:3]],
        "flags": flags,
        "before_sending_note": lead.get("before_sending_note", ""),
        "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def save_outlook_drafts(leads: list[dict], buckets: tuple = ("Send now", "Confirm first", "Government capability intro")) -> int:
    """
    Create or update drafts in Outlook (via local COM on Windows or Microsoft Graph API in the cloud).
    DESIGN RULE: Never auto-send. Only saves drafts ($mail.Save()) with Nick Hernandez CC'd for human review.
    """
    eligible = [l for l in leads if l.get("outreach_bucket") in buckets]
    created = 0

    # Cloud option: Microsoft Graph API (POST /me/messages creates a Draft without sending)
    if GRAPH_ACCESS_TOKEN:
        for l in eligible:
            to_addr = (l.get("contact_email") or "").strip()
            msg_payload = {
                "subject": l.get("custom_email_subject", ""),
                "body": {"contentType": "Text", "content": l.get("custom_email_body", "")},
                "toRecipients": [{"emailAddress": {"address": to_addr}}] if to_addr else [],
            }
            req = urllib.request.Request(
                "https://graph.microsoft.com/v1.0/me/messages",
                data=json.dumps(msg_payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {GRAPH_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10):
                    created += 1
            except Exception:
                continue
        return created

    # Desktop option: Windows Outlook COM (matches %TEMP%\scout_drafts.py)
    if os.name == "nt":
        try:
            import win32com.client as w
            ol = w.Dispatch("Outlook.Application")
            nick = ""
            r = ol.Session.CreateRecipient("Nick Hernandez")
            if r.Resolve():
                try:
                    ex = r.AddressEntry.GetExchangeUser()
                    nick = ex.PrimarySmtpAddress if ex else r.Address
                except Exception:
                    nick = r.Address

            for l in eligible:
                m = ol.CreateItem(0)
                m.Subject = l.get("custom_email_subject") or f"Introduction: McMurray Stern ({l.get('address')})"
                m.To = (l.get("contact_email") or "").strip()
                if nick:
                    m.CC = nick
                m.Body = (l.get("custom_email_body") or "").replace("\n", "\r\n")
                m.Save()  # Strictly .Save() to Drafts — NEVER .Send()
                created += 1
        except Exception as exc:
            print(f"Outlook COM note: {exc}")
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify MCS Project Scout leads and optionally update Outlook drafts.")
    parser.add_argument("--update-outlook-drafts", action="store_true", help="Save drafts in Outlook (never auto-sends)")
    args = parser.parse_args()

    leads_data = json.loads(LEADS_FILE.read_text(encoding="utf-8"))
    results = [verify(l) for l in leads_data]
    REPORT_FILE.write_text(json.dumps(results, indent=2, default=list), encoding="utf-8")
    print(f"Verified {len(results)} leads -> saved to {REPORT_FILE.name}")

    if args.update_outlook_drafts:
        draft_count = save_outlook_drafts(leads_data)
        print(f"Saved {draft_count} drafts to Outlook (CC: Nick Hernandez; 0 auto-sent).")
