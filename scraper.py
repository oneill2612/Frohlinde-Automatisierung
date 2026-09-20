import re
import json
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright


URL = (
    "https://www.fussball.de/verein/"
    "fc-frohlinde-westfalen/"
    "-/id/00ES8GN8OC00006VVV0AG08LVUPGND5I#!/"
)

VEREIN = "FC Frohlinde"

# True = nur Jugend
NUR_JUGEND = True

JUGEND_STUFEN = [
    "A-Junioren",
    "B-Junioren",
    "C-Junioren",
    "D-Junioren",
    "E-Junioren",
    "F-Junioren",
    "G-Junioren",
]

# Mannschaftsbezeichnungen für das Plakat
TEAM_NAMEN = {
    "FC Frohlinde": "1. Mannschaft",
    "FC Frohlinde II": "2. Mannschaft",
    "FC Frohlinde III": "E3",
    "FC Frohlinde IV": "E4",
}


def normalisiere(text):
    if not text:
        return ""

    text = text.replace("\u200b", "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def ist_jugend(liga):
    return any(stufe in liga for stufe in JUGEND_STUFEN)


def team_name(team):
    team = normalisiere(team)

    return TEAM_NAMEN.get(team, team)


def spiel_ist_fc_frohlinde(heim, gast):
    return (
        VEREIN.lower() in heim.lower()
        or VEREIN.lower() in gast.lower()
    )


def ermittle_wochenende():
    """
    Das kommende Wochenende.

    Wenn heute Sonntag ist, wird das nächste Wochenende genommen.
    """
    heute = datetime.now()

    tage_bis_samstag = (5 - heute.weekday()) % 7

    if tage_bis_samstag == 0 and heute.weekday() == 6:
        tage_bis_samstag = 6

    samstag = heute + timedelta(days=tage_bis_samstag)
    sonntag = samstag + timedelta(days=1)

    return samstag.date(), sonntag.date()


def parse_datum(text):
    """
    Unterstützt z.B.

    Samstag, 26.09.2026 - 10:00 Uhr
    Sa, 26.09.26 | 10:00
    """

    text = normalisiere(text)

    match = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})",
        text
    )

    if not match:
        return None

    tag = int(match.group(1))
    monat = int(match.group(2))
    jahr = int(match.group(3))

    if jahr < 100:
        jahr += 2000

    try:
        return datetime(jahr, monat, tag).date()
    except ValueError:
        return None


def parse_zeit(text):
    match = re.search(r"(\d{1,2}):(\d{2})", text)

    if not match:
        return ""

    return f"{int(match.group(1)):02d}:{match.group(2)}"


def extrahiere_spiele(html):
    """
    Extraktion aus der aktuellen Fussball.de-Struktur.
    """

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    spiele = []

    # Fussball.de verwendet club-matchplan-table für die Spielplanbereiche.
    rows = soup.select(".club-matchplan-table")

    for row in rows:

        text = normalisiere(row.get_text(" ", strip=True))

        if not text:
            continue

        # Datum suchen
        datum = parse_datum(text)

        if not datum:
            continue

        zeit = parse_zeit(text)

        # Wettbewerb
        wettbewerb = ""

        for element in row.select(
            ".column-competition, "
            ".column-league, "
            ".league-name, "
            ".competition"
        ):
            wettbewerb = normalisiere(element.get_text(" ", strip=True))
            if wettbewerb:
                break

        # Info-Kürzel
        info = ""

        match_info = re.search(
            r"\b(ME|FS|TU|PO|FE|BL|P)\b",
            text
        )

        if match_info:
            info = match_info.group(1)

        # Mannschaften
        heim = ""
        gast = ""

        home_selectors = [
            ".club-name-home",
            ".team-home",
            ".column-home .team-name",
            ".team-home-name",
        ]

        guest_selectors = [
            ".club-name-guest",
            ".team-guest",
            ".column-guest .team-name",
            ".team-guest-name",
        ]

        for selector in home_selectors:
            element = row.select_one(selector)
            if element:
                heim = normalisiere(element.get_text(" ", strip=True))
                break

        for selector in guest_selectors:
            element = row.select_one(selector)
            if element:
                gast = normalisiere(element.get_text(" ", strip=True))
                break

        # Fallback:
        # Suche nach Elementen mit Heim/Gast-Klassen
        if not heim:
            for element in row.find_all(class_=True):
                classes = " ".join(element.get("class", []))

                if "home" in classes.lower():
                    kandidat = normalisiere(
                        element.get_text(" ", strip=True)
                    )

                    if (
                        kandidat
                        and len(kandidat) < 100
                        and kandidat != wettbewerb
                    ):
                        heim = kandidat
                        break

        if not gast:
            for element in row.find_all(class_=True):
                classes = " ".join(element.get("class", []))

                if "guest" in classes.lower():
                    kandidat = normalisiere(
                        element.get_text(" ", strip=True)
                    )

                    if (
                        kandidat
                        and len(kandidat) < 100
                        and kandidat != wettbewerb
                    ):
                        gast = kandidat
                        break

        # Letzte Möglichkeit:
        # FC Frohlinde aus dem Text erkennen und Nachbarinformationen
        if not spiel_ist_fc_frohlinde(heim, gast):
            continue

        spiele.append({
            "datum": datum.isoformat(),
            "zeit": zeit,
            "heim": team_name(heim),
            "gast": team_name(gast),
            "heim_original": heim,
            "gast_original": gast,
            "wettbewerb": wettbewerb,
            "info": info,
        })

    return spiele


def lade_spielplan():
    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },
            locale="de-DE"
        )

        print("Öffne Fussball.de ...")

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        # Cookie-Banner
        cookie_buttons = [
            "button:has-text('Zustimmen')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Akzeptieren')",
        ]

        for selector in cookie_buttons:
            try:
                button = page.locator(selector).first

                if button.is_visible(timeout=1000):
                    button.click()
                    page.wait_for_timeout(1000)
                    break

            except Exception:
                pass

        # Mehr laden
        for _ in range(10):

            try:
                button = page.locator(
                    ".load-more-button"
                ).first

                if not button.is_visible(timeout=1000):
                    break

                button.click()
                page.wait_for_timeout(1500)

            except Exception:
                break

        html = page.content()

        browser.close()

    return html


def filtere_kommendes_wochenende(spiele):

    samstag, sonntag = ermittle_wochenende()

    print(
        f"Gesuchtes Wochenende: "
        f"{samstag.strftime('%d.%m.%Y')} - "
        f"{sonntag.strftime('%d.%m.%Y')}"
    )

    gefiltert = []

    for spiel in spiele:

        datum = datetime.fromisoformat(
            spiel["datum"]
        ).date()

        if datum not in [samstag, sonntag]:
            continue

        if NUR_JUGEND and not ist_jugend(
            spiel.get("wettbewerb", "")
        ):
            continue

        gefiltert.append(spiel)

    gefiltert.sort(
        key=lambda x: (
            x["datum"],
            x["zeit"]
        )
    )

    return gefiltert


def main():

    html = lade_spielplan()

    print("HTML geladen.")

    spiele = extrahiere_spiele(html)

    print(
        f"{len(spiele)} FC-Frohlinde-Spiele erkannt."
    )

    spiele = filtere_kommendes_wochenende(
        spiele
    )

    print(
        f"{len(spiele)} Spiele für das kommende "
        f"Wochenende."
    )

    Path("output").mkdir(
        exist_ok=True
    )

    with open(
        "output/spiele.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            spiele,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        json.dumps(
            spiele,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()
