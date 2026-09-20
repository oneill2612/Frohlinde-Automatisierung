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

        # Öfter klicken (15x), damit der 26.09. wirklich zu 100% in die Liste geladen wird!
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
    
    # Wir suchen nach der verkürzten Schreibweise, falls das Jahr fehlt!
    sa_such_datum = "26.09."
    so_such_datum = "27.09."
    
    aktuelles_datum = None
    aktuelle_zeit = "--:--"
    aktueller_wettbewerb = "Senioren"
    
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        row_text = bereinige_string(row_text)
        
        # 1. Ist das eine Datum-Überschrift? (Enthält "Samstag"/"Sonntag" und "Uhr")
        if ("Samstag" in row_text or "Sonntag" in row_text) and "Uhr" in row_text:
            if sa_such_datum in row_text:
                aktuelles_datum = "SA"
            elif so_such_datum in row_text:
                aktuelles_datum = "SO"
            else:
                aktuelles_datum = None
            
            t_match = re.search(r'(\d{1,2}:\d{2})\s*Uhr', row_text)
            if t_match: aktuelle_zeit = t_match.group(1)
            
            if "|" in row_text:
                parts = row_text.split("|")
                if len(parts) >= 2:
                    raw_team = parts[1]
                    clean_team = re.sub(r'(AME|ME|FS|TU|Kreisliga.*?|Bezirksliga.*?)', '', raw_team, flags=re.IGNORECASE)
                    aktueller_wettbewerb = bereinige_string(clean_team)
            continue
            
        # 2. Spielpaarung auslesen, wenn wir am richtigen Tag sind
        if aktuelles_datum:
            clubs = tr.select(".club-name, .column-club")
            heim, gast = "", ""
            
            if len(clubs) >= 2:
                heim = bereinige_string(clubs[0].get_text(strip=True))
                gast = bereinige_string(clubs[-1].get_text(strip=True))
            elif ":" in row_text or "-" in row_text:
                trenner = ":" if ":" in row_text else "-"
                parts = row_text.split(trenner)
                if len(parts) >= 2:
                    heim_parts = parts[0].strip().split()
                    heim = " ".join(heim_parts[-3:]) if len(heim_parts) >= 3 else parts[0].strip()
                    gast_parts = parts[1].strip().split()
                    gast = " ".join(gast_parts[:3]) if len(gast_parts) >= 3 else parts[1].strip()

            if "Frohlinde" in heim or "Frohlinde" in gast:
                ist_heim = "Frohlinde" in heim
                ist_turnier = "turnier" in aktueller_wettbewerb.lower() or "TU |" in row_text
                
                spiele.append({
                    "tag": aktuelles_datum,
                    "zeit": aktuelle_zeit,
                    "team": aktueller_wettbewerb,
                    "heim": heim,
                    "gast": gast,
                    "ist_heim": ist_heim,
                    "ist_turnier": ist_turnier
                })
                
    # Sicherheits-Netz: Wenn absolut NICHTS gefunden wurde, erzeugen wir ein Debug-Spiel, 
    # damit das Bild nicht einfach nur leer ist!
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
