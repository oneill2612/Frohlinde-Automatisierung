import os
import re
import time
import requests
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

URL = "https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"

def bereinige_string(text):
    if not text: return ""
    text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def hole_spieldaten():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(URL, timeout=60000, wait_until="networkidle")

        try:
            btn = page.locator("button:has-text('Zustimmen'), button:has-text('Akzeptieren')")
            if btn.count() > 0: btn.first.click(timeout=3000)
        except: pass

        # FEHLER BEHOBEN: Wir warten spezifisch auf die echten Fussball-Tabellen (.club-name)
        try:
            page.wait_for_selector(".club-name", timeout=10000)
        except: pass

        for _ in range(15):
            try:
                btn = page.locator(".load-more-button, a:has-text('Mehr laden')")
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    page.wait_for_timeout(1500)
                else: break
            except: break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    spiele = []

    sa_str = "26.09.2026"
    so_str = "27.09.2026"
    
    sa_such_datum = "26.09."
    so_such_datum = "27.09."
    
    aktuelles_datum_str = ""
    aktuelle_zeit = "--:--"
    aktueller_wettbewerb = "Senioren"
    
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        row_text = bereinige_string(row_text)
        
        if not row_text: continue

        clubs = tr.select(".club-name, .column-club")
        is_match_row = len(clubs) >= 2 or (" : " in row_text)

        if not is_match_row:
            # 1. Info-Zeile parsen
            date_match = re.search(r'(Mo|Di|Mi|Do|Fr|Sa|So),\s*(\d{2}\.\d{2}\.)', row_text)
            if date_match:
                aktuelles_datum_str = date_match.group(2)
                
            time_match = re.search(r'\b(\d{1,2}:\d{2})\b', row_text)
            if time_match:
                aktuelle_zeit = time_match.group(1)
                
            team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen)', row_text, re.IGNORECASE)
            if team_match:
                aktueller_wettbewerb = team_match.group(1).strip()
        else:
            # 2. Spiel-Zeile parsen
            if sa_such_datum in aktuelles_datum_str:
                tag = "SA"
            elif so_such_datum in aktuelles_datum_str:
                tag = "SO"
            else:
                continue 

            heim, gast = "", ""
            if len(clubs) >= 2:
                heim = bereinige_string(clubs[0].get_text(strip=True))
                gast = bereinige_string(clubs[-1].get_text(strip=True))
            else:
                parts = row_text.split(":")
                if len(parts) >= 2:
                    heim_parts = parts[0].strip().split()
                    heim = " ".join(heim_parts[-3:]) if len(heim_parts) >= 3 else parts[0].strip()
                    gast_parts = parts[1].strip().split()
                    gast = " ".join(gast_parts[:3]) if len(gast_parts) >= 3 else parts[1].strip()

            if "Frohlinde" in heim or "Frohlinde" in gast:
                ist_heim = "Frohlinde" in heim
                ist_turnier = "turnier" in aktueller_wettbewerb.lower()
                
                spiele.append({
                    "tag": tag,
                    "zeit": aktuelle_zeit,
                    "team": aktueller_wettbewerb,
                    "heim": heim,
                    "gast": gast,
                    "ist_heim": ist_heim,
                    "ist_turnier": ist_turnier
                })

    if not spiele:
        spiele.append({
            "tag": "SA",
            "zeit": "00:00",
            "team": "Fehler-Diagnose",
            "heim": f"Keine Daten für {sa_such_datum} oder {so_such_datum} gefunden",
            "gast": "Wurden auf Fussball.de schon Spiele eingetragen?",
            "ist_heim": True,
            "ist_turnier": False
        })

    return sa_str, so_str, spiele

def erstelle_und_sende():
    sa_str, so_str, spiele = hole_spieldaten()

    with open("template.html", "r", encoding="utf-8") as f:
        template = Template(f.read())

    rendered_html = template.render(
        samstag_datum=f"SAMSTAG, {sa_str}",
        sonntag_datum=f"SONNTAG, {so_str}",
        samstag_heim=[s for s in spiele if s["tag"] == "SA" and s["ist_heim"] and not s["ist_turnier"]],
        samstag_auswaerts=[s for s in spiele if s["tag"] == "SA" and not s["ist_heim"] and not s["ist_turnier"]],
        sonntag_heim=[s for s in spiele if s["tag"] == "SO" and s["ist_heim"] and not s["ist_turnier"]],
        sonntag_auswaerts=[s for s in spiele if s["tag"] == "SO" and not s["ist_heim"] and not s["ist_turnier"]],
        turniere=[s for s in spiele if s["ist_turnier"]]
    )

    with open("output.html", "w", encoding="utf-8") as f:
        f.write(rendered_html)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.goto(f"file://{os.path.abspath('output.html')}")
        page.screenshot(path="spielplan.png")
        browser.close()

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open("spielplan.png", "rb") as photo:
            requests.post(
                url,
                data={"chat_id": chat_id, "caption": f"⚽ Spielplan FC Frohlinde ({sa_str} - {so_str})"},
                files={"photo": photo}
            )

if __name__ == "__main__":
    erstelle_und_sende()
