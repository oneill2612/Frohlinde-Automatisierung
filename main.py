import os
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

CLUB_ID = "00ES8GN8OC00006VVV0AG08LVUPGND5I"
BASE_URL = f"https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/{CLUB_ID}#!/"

def bereinige_team(text):
    if not text:
        return ""
    text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', text)
    text = re.sub(r'\b(AME|ME|FS|TU|Kinderfußball|Kreisliga\s*[A-Z0-9]?|Bezirksliga\s*[A-Z0-9]?|Kreisklasse\s*[A-Z0-9]?|Kreisfreundschaftsspiele|Vereinsturnier)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def hole_spieldaten():
    html_blobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 1. Startseite öffnen (setzt Cookies und Session auf fussball.de)
        page.goto(BASE_URL, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        html_blobs.append(page.content())

        # 2. AJAX-Seiten direkt über den internen Endpunkt abrufen (ohne Button-Klick-Zwang)
        for offset in range(1, 6):
            ajax_url = f"https://www.fussball.de/ajax.club.matchplan/-/id/{CLUB_ID}/mime-type/HTML/mode/PAGE/offset/{offset}"
            try:
                resp = page.request.get(ajax_url, timeout=10000)
                if resp.status == 200:
                    text = resp.text()
                    if text and len(text) > 300:
                        html_blobs.append(text)
            except Exception:
                pass

        browser.close()

    # Alle geladenen HTML-Blöcke zusammenführen
    gesamtes_html = "\n".join(html_blobs)
    soup = BeautifulSoup(gesamtes_html, "html.parser")
    spiele = []

    # Automatisches Ermitteln des kommenden Wochenendes (Sa & So)
    heute = datetime.now()
    tage_bis_sa = (5 - heute.weekday()) % 7
    if tage_bis_sa == 0:
        tage_bis_sa = 7  # Falls heute Samstag ist, nimm das nächste Wochenende

    samstag = heute + timedelta(days=tage_bis_sa)
    sonntag = samstag + timedelta(days=1)

    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")
    sa_such_datum = samstag.strftime("%d.%m.")
    so_such_datum = sonntag.strftime("%d.%m.")

    aktuelles_datum_tag = None
    aktuelle_zeit = "--:--"
    aktueller_wettbewerb = "Senioren"

    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        row_text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', row_text)
        if not row_text:
            continue

        # 1. Datum ermitteln: Wenn ein neues Datum in der Zeile steht, merken
        if sa_such_datum in row_text:
            aktuelles_datum_tag = "SA"
        elif so_such_datum in row_text:
            aktuelles_datum_tag = "SO"
        elif re.search(r'\b(Mo|Di|Mi|Do|Fr|Sa|So),\s*\d{2}\.\d{2}\.', row_text):
            # Ein anderer Wochentag (z. B. Do oder Di davor/danach)
            aktuelles_datum_tag = None

        # 2. Uhrzeit erfassen
        time_match = re.search(r'\b(\d{1,2}:\d{2})\b', row_text)
        if time_match:
            aktuelle_zeit = time_match.group(1)

        # 3. Mannschaft / Altersklasse erfassen
        team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen|Alte Herren|Mini[s]?)', row_text, re.IGNORECASE)
        if team_match:
            aktueller_wettbewerb = team_match.group(1).strip()

        # 4. Vereine auslesen (ausschließlich .club-name verhindert Doppel-Erfassung)
        club_nodes = tr.select(".club-name")
        if len(club_nodes) >= 2 and aktuelles_datum_tag:
            heim = club_nodes[0].get_text(strip=True)
            gast = club_nodes[1].get_text(strip=True)

            if "Frohlinde" in heim or "Frohlinde" in gast:
                ist_heim = "Frohlinde" in heim
                ist_turnier = "turnier" in row_text.lower()

                spiele.append({
                    "tag": aktuelles_datum_tag,
                    "zeit": aktuelle_zeit,
                    "team": bereinige_team(aktueller_wettbewerb),
                    "heim": heim,
                    "gast": gast,
                    "ist_heim": ist_heim,
                    "ist_turnier": ist_turnier
                })

    # Duplikate entfernen
    unique_spiele = []
    for sp in spiele:
        if sp not in unique_spiele:
            unique_spiele.append(sp)

    # Nach Spielzeit sortieren
    unique_spiele.sort(key=lambda s: s["zeit"])

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

    # Screenshot in 1080x1920 rendern
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
