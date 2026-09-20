import os
import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

URL = "https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"

def bereinige_teamname(text):
    if not text:
        return ""
    # Entfernt Liga-Zusätze, Kreisliga, AME etc.
    text = re.sub(r'\b(AME|ME|FS|TU|Kreisliga\s*[A-Z0-9]?|Bezirksliga\s*[A-Z0-9]?|Kreisklasse\s*[A-Z0-9]?|Vereinsturnier|Kreisfreundschaftsspiele)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def hole_spieldaten():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, timeout=60000, wait_until="networkidle")

        # 1. Cookie-Banner wegklicken
        try:
            cookie_btn = page.locator("button:has-text('Zustimmen'), button:has-text('Akzeptieren'), #cmpwelcomebtnyes a")
            if cookie_btn.count() > 0:
                cookie_btn.first.click(timeout=3000)
        except Exception:
            pass

        # 2. Mehrfach auf "Mehr laden" klicken
        for _ in range(8):
            try:
                load_more = page.locator(".load-more-button, a:has-text('Mehr laden')")
                if load_more.count() > 0 and load_more.first.is_visible():
                    load_more.first.click()
                    time.sleep(1.8)
                else:
                    break
            except Exception:
                break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    spiele = []

    # Relevantes Wochenende bestimmen (nächster Samstag & Sonntag)
    heute = datetime.now()
    tage_sa = (5 - heute.weekday()) % 7
    # Wenn heute Sa oder So ist, das aktuelle Wochenende nehmen
    if heute.weekday() in [5, 6]:
        samstag = heute - timedelta(days=(heute.weekday() - 5))
    else:
        samstag = heute + timedelta(days=tage_sa)
    sonntag = samstag + timedelta(days=1)

    sa_prefix = samstag.strftime("%d.%m")
    so_prefix = sonntag.strftime("%d.%m")
    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")

    # Fussball.de Spiel-Zeilen durchsuchen
    rows = soup.select("tr.odd, tr.even, tr.match-row, tr")

    aktueller_tag = None

    for r in rows:
        row_text = r.get_text(" ", strip=True)

        # Datumszeile (z.B. "Sonntag, 20.09.2026 - 11:00 Uhr | A-Junioren")
        if sa_prefix in row_text or "Samstag" in row_text:
            aktueller_tag = "SA"
        elif so_prefix in row_text or "Sonntag" in row_text:
            aktueller_tag = "SO"

        # Clubs aus den separaten Spalten holen
        clubs = r.select(".club-name, .column-club")
        if len(clubs) >= 2:
            heim = clubs[0].get_text(" ", strip=True)
            gast = clubs[1].get_text(" ", strip=True)
        elif " : " in row_text or " - " in row_text:
            # Fallback über Text-Split
            parts = re.split(r'\s+[:\-]\s+', row_text)
            if len(parts) >= 2:
                heim = parts[0].split()[-3:]
                heim = " ".join(heim)
                gast = parts[1].split()[:3]
                gast = " ".join(gast)
            else:
                continue
        else:
            continue

        if not ("Frohlinde" in heim or "Frohlinde" in gast):
            continue

        # Uhrzeit suchen (z.B. 11:00 oder 15:15)
        time_match = re.search(r'\b(\d{1,2}:\d{2})\b', row_text)
        zeit = time_match.group(1) if time_match else "--:--"

        # Teambezeichnung (z.B. A-Junioren, B-Junioren, Herren, 2. Mannschaft)
        team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen)', row_text, re.IGNORECASE)
        team_name = team_match.group(1) if team_match else "Senioren"

        ist_heim = "Frohlinde" in heim
        ist_turnier = "turnier" in row_text.lower() or "TU |" in row_text

        spiele.append({
            "tag": aktueller_tag if aktueller_tag else ("SA" if "Samstag" in row_text else "SO"),
            "zeit": zeit,
            "team": bereinige_teamname(team_name),
            "heim": bereinige_teamname(heim),
            "gast": bereinige_teamname(gast),
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
