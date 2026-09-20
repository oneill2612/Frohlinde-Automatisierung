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
    if not text: return ""
    text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', text)
    text = re.sub(r'\b(AME|ME|FS|TU|Kinderfußball|Kreisliga\s*[A-Z0-9]?|Bezirksliga\s*[A-Z0-9]?|Kreisklasse\s*[A-Z0-9]?|Kreisfreundschaftsspiele|Vereinsturnier)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def hole_spieldaten():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(URL, timeout=60000, wait_until="networkidle")

        # 1. Cookie-Banner per JavaScript wegklicken
        try:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a'));
                const accept = btns.find(b => b.innerText && (b.innerText.includes('Zustimmen') || b.innerText.includes('Akzeptieren')));
                if (accept) accept.click();
            }""")
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # 2. "Mehr laden" zuverlässig per JavaScript klicken
        for runde in range(12):
            # Ans Ende scrollen
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1200)

            # Button per Text finden und auslösen
            geklickt = page.evaluate("""() => {
                const elements = Array.from(document.querySelectorAll('a, button, div, span'));
                const btn = elements.reverse().find(el => {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    return t === 'mehr laden' || t.startsWith('mehr laden');
                });
                if (btn) {
                    btn.scrollIntoView();
                    btn.click();
                    return true;
                }
                return false;
            }""")

            if geklickt:
                page.wait_for_timeout(2500)  # Dem Nachladen Zeit geben
            else:
                break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    spiele = []

    # Dynamisches Wochenende:
    # Heute (So) -> nimmt 26./27.09.
    # An einem Donnerstag -> nimmt Sa/So der aktuellen Woche!
    heute = datetime.now()
    tage_bis_sa = (5 - heute.weekday()) % 7
    if tage_bis_sa == 0:
        tage_bis_sa = 7  # Wenn heute Sa ist, nächstes WE anpeilen

    samstag = heute + timedelta(days=tage_bis_sa)
    sonntag = samstag + timedelta(days=1)

    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")
    sa_tag = samstag.strftime("%d.%m.")
    so_tag = sonntag.strftime("%d.%m.")

    aktueller_tag = None
    aktuelle_zeit = "--:--"
    aktuelles_team = "Senioren"

    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        row_text = re.sub(r'[\u200b\u200e\u200f\xa0]', ' ', row_text)
        if not row_text:
            continue

        # Tag-Erkennung
        if sa_tag in row_text:
            aktueller_tag = "SA"
        elif so_tag in row_text:
            aktueller_tag = "SO"
        elif re.search(r'\b\d{2}\.\d{2}\.\b', row_text) and (sa_tag not in row_text and so_tag not in row_text):
            # Anderes Datum (z.B. Do davor oder Di danach)
            aktueller_tag = None

        # Uhrzeit
        t_match = re.search(r'\b(\d{1,2}:\d{2})\b', row_text)
        if t_match:
            aktuelle_zeit = t_match.group(1)

        # Altersklasse / Team
        team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen|Alte Herren)', row_text, re.IGNORECASE)
        if team_match:
            aktuelles_team = team_match.group(1).strip()

        # Vereine prüfen
        clubs = tr.select(".club-name")
        if len(clubs) >= 2 and aktueller_tag:
            heim = clubs[0].get_text(strip=True)
            gast = clubs[1].get_text(strip=True)

            if "Frohlinde" in heim or "Frohlinde" in gast:
                ist_heim = "Frohlinde" in heim
                ist_turnier = "turnier" in row_text.lower()

                spiele.append({
                    "tag": aktueller_tag,
                    "zeit": aktuelle_zeit,
                    "team": bereinige_team(aktuelles_team),
                    "heim": heim,
                    "gast": gast,
                    "ist_heim": ist_heim,
                    "ist_turnier": ist_turnier
                })

    # Duplikate filtern
    unique_spiele = []
    for sp in spiele:
        if sp not in unique_spiele:
            unique_spiele.append(sp)

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

    # Screenshot in 1080x1920 erstellen
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
