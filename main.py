import os
import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

URL = "https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"

def bereinige_team(text):
    if not text:
        return ""
    # Bereinigt Liga-Zusätze und unsichtbare Zeichen von fussball.de
    text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', text)
    text = re.sub(r'\b(AME|ME|FS|TU|Kinderfußball|Kreisliga\s*[A-Z0-9]?|Bezirksliga\s*[A-Z0-9]?|Kreisklasse\s*[A-Z0-9]?|Kreisfreundschaftsspiele|Vereinsturnier)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def hole_spieldaten():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, timeout=60000, wait_until="networkidle")

        # 1. Cookie-Banner schließen
        try:
            btn = page.locator("button:has-text('Zustimmen'), button:has-text('Akzeptieren'), #cmpwelcomebtnyes a")
            if btn.count() > 0:
                btn.first.click(timeout=3000)
        except Exception:
            pass

        # 2. "Mehr laden" klicken (bis zu 12 Mal), damit auch Vortage/Samstag da sind
        for _ in range(12):
            try:
                load_more = page.locator(".load-more-button, a:has-text('Mehr laden')")
                if load_more.count() > 0 and load_more.first.is_visible():
                    load_more.first.click()
                    page.wait_for_timeout(1500)
                else:
                    break
            except Exception:
                break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    spiele = []

    # Relevantes Wochenende ermitteln (aktuelles/nächstes Wochenende)
    heute = datetime.now()
    if heute.weekday() in [5, 6]: # Sa oder So
        samstag = heute - timedelta(days=(heute.weekday() - 5))
    else:
        tage_bis_sa = (5 - heute.weekday()) % 7
        samstag = heute + timedelta(days=tage_bis_sa)
    sonntag = samstag + timedelta(days=1)

    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")
    sa_short = samstag.strftime("%d.%m")
    so_short = sonntag.strftime("%d.%m")

    aktueller_tag = None
    aktuelle_zeit = "--:--"
    aktuelles_team = "Team"
    ist_turnier = False

    # Alle Zeilen der Tabelle durchgehen
    for tr in soup.select("table tr"):
        text = tr.get_text(" ", strip=True)
        text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', text)

        # Typ 1: Datums- und Team-Überschrift (z.B. "Sonntag, 20.09.2026 - 11:00 Uhr | A-Junioren | Kreisliga A")
        if ("Samstag" in text or "Sonntag" in text) and "Uhr" in text:
            if sa_short in text or "Samstag" in text:
                aktueller_tag = "SA"
            elif so_short in text or "Sonntag" in text:
                aktueller_tag = "SO"
            else:
                aktueller_tag = None

            # Zeit filtern (z.B. 11:00)
            t_match = re.search(r'(\d{1,2}:\d{2})\s*Uhr', text)
            if t_match:
                aktuelle_zeit = t_match.group(1)

            # Team-Klasse filtern (z.B. A-Junioren, 1. Mannschaft etc.)
            m_team = re.search(r'\|\s*([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen)', text, re.IGNORECASE)
            if m_team:
                aktuelles_team = m_team.group(1).strip()
            else:
                aktuelles_team = "FC Frohlinde"

            ist_turnier = "turnier" in text.lower() or "tu |" in text.lower()
            continue

        # Typ 2: Spielpaarung (enthält die beiden Vereine)
        clubs = tr.select(".club-name, td.column-club")
        if len(clubs) >= 2 and aktueller_tag:
            heim = clubs[0].get_text(strip=True)
            gast = clubs[1].get_text(strip=True)

            if "Frohlinde" in heim or "Frohlinde" in gast:
                ist_heim = "Frohlinde" in heim
                spiele.append({
                    "tag": aktueller_tag,
                    "zeit": aktuelle_zeit,
                    "team": bereinige_team(aktuelles_team),
                    "heim": bereinige_team(heim),
                    "gast": bereinige_team(gast),
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
