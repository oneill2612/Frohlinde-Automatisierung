import json
import os
import requests

from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright


TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN"
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)


def lade_spiele():

    with open(
        "output/spiele.json",
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def formatiere_datum(datum):

    dt = datetime.fromisoformat(datum)

    tage = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag"
    ]

    return (
        f"{tage[dt.weekday()]}, "
        f"{dt.strftime('%d.%m.')}"
    )


def bereite_daten(spiele):

    ergebnis = []

    for spiel in spiele:

        ergebnis.append({
            **spiel,
            "datum_formatiert":
                formatiere_datum(
                    spiel["datum"]
                ),
            "heimspiel":
                "FC Frohlinde" in
                spiel["heim_original"]
        })

    return ergebnis


def erstelle_html(spiele):

    env = Environment(
        loader=FileSystemLoader(".")
    )

    template = env.get_template(
        "template.html"
    )

    return template.render(
        spiele=spiele,
        erstellt=datetime.now().strftime(
            "%d.%m.%Y %H:%M"
        )
    )


def erstelle_bild(html):

    Path("output").mkdir(
        exist_ok=True
    )

    html_datei = Path(
        "output/spielplan.html"
    )

    html_datei.write_text(
        html,
        encoding="utf-8"
    )

    bild_datei = Path(
        "output/spielplan.png"
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1080,
                "height": 1920
            },
            device_scale_factor=1
        )

        page.goto(
            html_datei.resolve().as_uri(),
            wait_until="networkidle"
        )

        page.screenshot(
            path=str(bild_datei),
            full_page=True
        )

        browser.close()

    return bild_datei


def sende_telegram(datei):

    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN fehlt."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID fehlt."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendPhoto"
    )

    caption = (
        "⚽ FC Frohlinde – Spielplan\n"
        "📅 Kommendes Wochenende"
    )

    with open(
        datei,
        "rb"
    ) as photo:

        response = requests.post(
            url,
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "caption":
                    caption
            },
            files={
                "photo":
                    photo
            },
            timeout=60
        )

    response.raise_for_status()

    print(
        "Telegram-Nachricht erfolgreich gesendet."
    )


def main():

    spiele = lade_spiele()

    if not spiele:
        print(
            "Keine Spiele gefunden."
        )
        return

    daten = bereite_daten(
        spiele
    )

    html = erstelle_html(
        daten
    )

    bild = erstelle_bild(
        html
    )

    sende_telegram(
        bild
    )


if __name__ == "__main__":
    main()
