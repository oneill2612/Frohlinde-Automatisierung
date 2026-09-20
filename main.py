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

        try:
            page.wait_for_selector(".club-name, .column-club", timeout=10000)
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

    aktuelles_datum_tag = None
    aktuelle_zeit = "--:--"
    aktueller_wettbewerb = "Senioren"

    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        row_text = bereinige_string(row_text)

        if not row_text: continue

        # 1. EXTREM SIMPLER DATUMS-CHECK
        # Sobald "26.09." irgendwo im Text der Zeile auftaucht, speichern wir "SA".
        if sa_such_datum in row_text:
            aktuelles_datum_tag = "SA"
        elif so_such_datum in row_text:
            aktuelles_datum_tag = "SO"

        # Zeit auslesen
        time_match = re.search(r'\b(\d{1,2}:\d{2})\b', row_text)
        if time_match:
            aktuelle_zeit = time_match.group(1)

        # Team/Altersklasse auslesen
        team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen|Alte Herren)', row_text, re.IGNORECASE)
        if team_match:
            aktueller_wettbewerb = team_match.group(1).strip()

        # 2. VEREINE AUSLESEN
        clubs = tr.select(".club-name, .column-club")
        
        # Wir werten die Zeile nur als Spiel, wenn wirklich zwei Vereine drinstehen
        if len(clubs) >= 2:
            heim = bereinige_string(clubs[0].get_text(strip=True))
            gast = bereinige_string(clubs[-1].get_text(strip=True))

            # 3. SPIEL SPEICHERN (Nur wenn Frohlinde dabei ist und wir wissen, welcher Tag ist)
            if ("Frohlinde" in heim or "Frohlinde" in gast) and aktuelles_datum_tag:
                ist_heim = "Frohlinde" in heim
                ist_turnier = "turnier" in aktueller_wettbewerb.lower()

                spiele.append({
                    "tag": aktuelles_datum_tag,
                    "zeit": aktuelle_zeit,
                    "team": aktueller_wettbewerb,
                    "heim": heim,
                    "gast": gast,
                    "ist_heim": ist_heim,
                    "ist_turnier": ist_turnier
                })

    # Doppelte Spiele entfernen
    unique_spiele = []
    for sp in spiele:
        if sp not in unique_spiele:
            unique_spiele.append(sp)

    if not unique_spiele:
        unique_spiele.append({
            "tag": "SA",
            "zeit": "00:00",
            "team": "Fehler-Diagnose",
            "heim": "Kein Spiel gefunden",
            "gast": "Datum 26.09. / 27.09. wurde nicht erkannt",
            "ist_heim": True,
            "ist_turnier": False
        })

    return sa_str, so_str, unique_spiele

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
