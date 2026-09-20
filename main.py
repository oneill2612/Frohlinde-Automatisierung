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
            btn = page.locator("button:has-text('Zustimmen')")
            if btn.count() > 0: btn.first.click(timeout=3000)
        except: pass

        for _ in range(12):
            try:
                btn = page.locator(".load-more-button")
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    page.wait_for_timeout(1500)
                else: break
            except: break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    spiele = []

    # HART CODIERT AUF NÄCHSTES WOCHENENDE FÜR DEN TEST (26.09. und 27.09.)
    sa_str = "26.09.2026"
    so_str = "27.09.2026"
    
    # Suche alle Spiele in den Match-Zeilen
    match_rows = soup.select(".match-row")
    
    # Wir iterieren durch die Tabelle. Da Datum und Uhrzeit oft in separaten Headern (row-headline) 
    # VOR den Spielen stehen, müssen wir uns diese merken.
    aktuelles_datum = None
    aktuelle_zeit = "--:--"
    aktueller_wettbewerb = "Senioren"
    
    for tr in soup.select("table tbody tr"):
        row_class = tr.get("class", [])
        
        # 1. Wenn es eine Header-Zeile ist, merke dir die Metadaten
        if "row-headline" in row_class:
            header_text = tr.get_text(" ", strip=True)
            
            # Datum extrahieren (z.B. "Samstag, 26.09.2026")
            if "26.09.2026" in header_text:
                aktuelles_datum = "SA"
            elif "27.09.2026" in header_text:
                aktuelles_datum = "SO"
            else:
                aktuelles_datum = None # Falsches Wochenende, ignorieren
                
            # Zeit extrahieren
            t_match = re.search(r'(\d{1,2}:\d{2})', header_text)
            if t_match: aktuelle_zeit = t_match.group(1)
            
            # Team / Wettbewerb extrahieren (nach dem Pipe-Symbol)
            if "|" in header_text:
                parts = header_text.split("|")
                if len(parts) > 1:
                    raw_team = parts[1]
                    # Kürzel entfernen
                    clean_team = re.sub(r'(AME|ME|FS|TU|Kreisliga.*?|Bezirksliga.*?)', '', raw_team, flags=re.IGNORECASE)
                    aktueller_wettbewerb = bereinige_string(clean_team)
            
            continue # Springe zur nächsten Zeile (dem eigentlichen Spiel)
            
        # 2. Wenn es eine Spiel-Zeile ist und wir am richtigen Wochenende sind
        if "match-row" in row_class and aktuelles_datum is not None:
            # Heim- und Gastmannschaft exakt über die dafür vorgesehenen CSS-Klassen auslesen
            heim_node = tr.select_one(".club-name-home, .column-club:nth-of-type(3)")
            gast_node = tr.select_one(".club-name-guest, .column-club:nth-of-type(5)")
            
            # Wenn es diese Klassen nicht gibt (passiert bei Turnieren), nimm allgemeine Klassen
            if not heim_node or not gast_node:
                clubs = tr.select(".club-name")
                if len(clubs) >= 2:
                    heim_node = clubs[0]
                    gast_node = clubs[1]
            
            if heim_node and gast_node:
                heim = bereinige_string(heim_node.get_text(strip=True))
                gast = bereinige_string(gast_node.get_text(strip=True))
                
                # Wir nehmen das Spiel nur auf, wenn Frohlinde auch mitspielt
                if "Frohlinde" in heim or "Frohlinde" in gast:
                    ist_heim = "Frohlinde" in heim
                    ist_turnier = "turnier" in aktueller_wettbewerb.lower()
                    
                    spiele.append({
                        "tag": aktuelles_datum,
                        "zeit": aktuelle_zeit,
                        "team": aktueller_wettbewerb,
                        "heim": heim,
                        "gast": gast,
                        "ist_heim": ist_heim,
                        "ist_turnier": ist_turnier
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
