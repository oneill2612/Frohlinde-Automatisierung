import os
import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

URL = "https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"

def bereinige_team(team_str):
    # Entfernt Liga-Zusätze und interne Kürzel wie AME, ME, Kinderfußball
    text = re.sub(r'(AME|ME|Kinderfußball|Kreisliga\s*[A-Z0-9]?|Bezirksliga\s*[A-Z0-9]?|Kreisklasse\s*[A-Z0-9]?)', '', team_str, flags=re.IGNORECASE)
    # Normiert gängige Bezeichnungen
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def bereinige_vereinsname(name):
    name = re.sub(r'\s+', ' ', name)
    return name.strip()

def hole_spieldaten():
    spiele = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Desktop-Viewport verhindert mobile Redirection
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, timeout=60000, wait_until="networkidle")

        # 1. Cookie-Banner schließen
        try:
            btn = page.locator("button:has-text('Zustimmen'), button:has-text('Akzeptieren'), #cmpwelcomebtnyes a")
            if btn.count() > 0:
                btn.first.click(timeout=3000)
        except Exception:
            pass

        # 2. Mehrfach "Mehr laden" klicken
        for _ in range(6):
            try:
                load_more = page.locator(".load-more-button, a:has-text('Mehr laden')")
                if load_more.count() > 0 and load_more.first.is_visible():
                    load_more.first.click()
                    time.sleep(2)
                else:
                    break
            except Exception:
                break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # Nächstes Wochenende ermitteln (kommender Samstag & Sonntag)
    heute = datetime.now()
    tage_bis_sa = (5 - heute.weekday()) % 7
    if tage_bis_sa == 0 and heute.weekday() != 5:
        tage_bis_sa = 7
    samstag = heute + timedelta(days=tage_bis_sa)
    sonntag = samstag + timedelta(days=1)
    
    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")

    # Fussball.de-Zeilen auslesen
    rows = soup.select(".match-row, tr.row-headline, tr.odd, tr.even, tr")

    aktueller_tag = None

    for r in rows:
        row_text = r.get_text(" ", strip=True)
        
        # Datumszeile erkennen
        if sa_str in row_text or (f"{samstag.day}." in row_text and "Samstag" in row_text):
            aktueller_tag = "SA"
        elif so_str in row_text or (f"{sonntag.day}." in row_text and "Sonntag" in row_text):
            aktueller_tag = "SO"

        # Nur weiter parsen, wenn wir uns im aktuellen Wochenende befinden und Frohlinde involviert ist
        if "Frohlinde" in row_text:
            # Uhrzeit
            time_match = re.search(r'(\d{1,2}:\d{2})', row_text)
            zeit = time_match.group(1) if time_match else "--:--"

            # Teams extrahieren (Fussball.de club-names oder Regex-Aufteilung)
            team_nodes = r.select(".club-name, .club-name-home, .club-name-guest, td.column-club")
            if len(team_nodes) >= 2:
                heim = bereinige_vereinsname(team_nodes[0].get_text(strip=True))
                gast = bereinige_vereinsname(team_nodes[1].get_text(strip=True))
            elif " - " in row_text or " : " in row_text:
                parts = re.split(r'\s+[-:]\s+', row_text)
                heim = parts[0].split()[-2:] if len(parts[0].split()) >= 2 else parts[0]
                heim = " ".join(heim) if isinstance(heim, list) else heim
                gast = parts[1].split()[:3]
                gast = " ".join(gast)
            else:
                heim = "FC Frohlinde"
                gast = "Gegner"

            ist_heim = "Frohlinde" in heim
            ist_turnier = "turnier" in row_text.lower() or "hallenturnier" in row_text.lower()

            # Altersklasse / Teamkategorie
            team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen|Alte Herren)', row_text)
            team_label = team_match.group(1) if team_match else "Team"

            tag_zugeordnet = aktueller_tag if aktueller_tag else ("SA" if "Samstag" in row_text else "SO")

            spiele.append({
                "tag": tag_zugeordnet,
                "zeit": zeit,
                "team": bereinige_team(team_label),
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

    # Screenshot erstellen
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.goto(f"file://{os.path.abspath('output.html')}")
        page.screenshot(path="spielplan.png")
        browser.close()

    # Per Telegram versenden
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
