import os
import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

URL = "https://www.fussball.de/verein/fc-frohlinde-westfalen/-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"

def bereinige_text(text):
    text = re.sub(r'(AME|ME|Kinderfußball|Kreisliga\s*[A-Z]?|Bezirksliga\s*[A-Z]?)', '', text)
    return ' '.join(text.split()).strip()

def hole_spieldaten():
    spiele = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, timeout=60000)

        # Cookie-Banner akzeptieren
        try:
            page.locator("button:has-text('Zustimmen')").click(timeout=5000)
        except Exception:
            pass

        # "Mehr laden" anklicken, bis alles sichtbar ist
        for _ in range(6):
            try:
                load_more = page.locator(".load-more-button, a:has-text('Mehr laden')")
                if load_more.is_visible():
                    load_more.click()
                    time.sleep(2)
                else:
                    break
            except Exception:
                break

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".match-row, tr.row-headline, tr")

    heute = datetime.now()
    samstag = heute + timedelta((5 - heute.weekday()) % 7)
    sonntag = samstag + timedelta(days=1)
    sa_str = samstag.strftime("%d.%m.%Y")
    so_str = sonntag.strftime("%d.%m.%Y")

    for r in rows:
        text = r.get_text(" ", strip=True)
        if "FC Frohlinde" in text or "Frohlinde" in text:
            time_match = re.search(r'(\d{2}:\d{2})', text)
            zeit = time_match.group(1) if time_match else "--:--"

            is_turnier = "turnier" in text.lower()
            ist_heim = "FC Frohlinde" in text.split(" - ")[0] if " - " in text else True

            team = "Team"
            team_match = re.search(r'([A-G]\d?-Junioren|\d+\.\s*Mannschaft|Herren|Frauen)', text)
            if team_match:
                team = team_match.group(1)

            spiele.append({
                "tag": "SA" if (sa_str in text or "Samstag" in text) else "SO",
                "zeit": zeit,
                "team": bereinige_text(team),
                "heim": "FC Frohlinde" if ist_heim else "Gegner",
                "gast": "Gegner" if ist_heim else "FC Frohlinde",
                "ist_heim": ist_heim,
                "ist_turnier": is_turnier
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

    # Screenshot in Social-Media-Auflösung 1080x1920 erstellen
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.goto(f"file://{os.path.abspath('output.html')}")
        page.screenshot(path="spielplan.png")
        browser.close()

    # Per Telegram an dein Handy senden
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open("spielplan.png", "rb") as photo:
            requests.post(
                url,
                data={"chat_id": chat_id, "caption": f"⚽ Wochenend-Spielplan FC Frohlinde ({sa_str} - {so_str})"},
                files={"photo": photo}
            )

if __name__ == "__main__":
    erstelle_und_sende()
