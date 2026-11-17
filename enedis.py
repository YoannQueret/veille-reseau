"""Adaptateur du service public utilisé par enedis.fr (pas une API contractuelle)."""
import hashlib
import json
import urllib.parse
import urllib.request

SOURCE = "https://www.enedis.fr"
GEOCODER = "https://data.geopf.fr/geocodage/search/"


class SourceError(Exception):
    pass


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "VeilleReseau/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = response.read(8_000_001)
            if len(data) > 8_000_000:
                raise SourceError("Réponse Enedis trop volumineuse")
            return json.loads(data)
    except (OSError, ValueError) as exc:
        raise SourceError("Service distant indisponible ou réponse illisible") from exc


def search(query):
    data = fetch_json(GEOCODER + "?" + urllib.parse.urlencode({"q": query, "maxResults": 6, "autoComplete": 1}))
    return [feature_to_place(f) for f in data.get("features", []) if f.get("properties", {}).get("type") in ("municipality", "locality", "street", "housenumber")]


def feature_to_place(feature):
    p = feature["properties"]
    lon, lat = feature["geometry"]["coordinates"][:2]
    return dict(label=p["label"], city=p["city"], postcode=p.get("postcode", ""),
                insee=p["citycode"], type=p["type"], street=p.get("street", ""),
                name=p.get("name", ""), district=p.get("district", ""), lat=lat, lon=lon)


def params(place):
    city = place["city"].replace("'Or", "_Or")
    return dict(insee=place["insee"], type=place["type"], adresse=place["label"],
                CPVille=city + " " + place["postcode"], name=place["name"], district=place["district"],
                city=city, street=place["street"], lat=place["lat"], long=place["lon"],
                departement=place["insee"][:2])


def source_url(place):
    return SOURCE + "/resultat-panne-interruption?" + urllib.parse.urlencode(params(place))


def truth(value):
    return value is True or value == "true"


def category(status):
    if status in ("Incident BT", "Incident HTA", "Delestage", "Délestage"):
        return "outage"
    if status in ("Travaux BT", "Travaux HTA"):
        return "works"
    if status in ("Coupure clôturée", "Réalimentation HTA"):
        return "restored"
    return None


def normalize(data, place):
    """Ne jamais assimiler une réponse vide/ambiguë ou une erreur à une absence de panne."""
    if not isinstance(data, dict):
        raise SourceError("Format Enedis inattendu")
    items = [v for k, v in data.items() if k.isdigit() and isinstance(v, dict)]
    for item in [data, *items]:
        if any(truth(item.get(flag)) for flag in ("errorMegacache", "errorServices", "errorEsri")) or item.get("error"):
            raise SourceError("Enedis signale une indisponibilité de ses données")
    mega = data.get("resultMegacache")
    if not isinstance(mega, dict) or mega.get("codeRetour") not in (0, "0"):
        raise SourceError("Réponse Enedis incomplète ou code retour inconnu")
    if not isinstance(mega.get("listeCrises"), list) or not isinstance(mega.get("listeCoupuresInfoReseau"), list):
        raise SourceError("Structure de réponse Enedis modifiée")
    crises = mega["listeCrises"]
    # Une crise régionale ne permet pas d'affirmer qu'une adresse précise est coupée.
    if crises:
        return dict(status="unknown", message="Vigilance Enedis : " + str(crises[0].get("titreCrise", "situation exceptionnelle")), events=[], definitive=False)
    if truth(data.get("noCoupure")):
        return dict(status="ok", message="Aucune coupure détectée par Enedis", events=[], definitive=True)
    if truth(data.get("streetNotFound")):
        raise SourceError("Rue non reconnue par Enedis ; affinez le lieu surveillé")
    events = []
    municipality = place["type"] in ("municipality", "locality")
    if municipality:
        for item in mega["listeCoupuresInfoReseau"]:
            kind = category(item.get("incidentCoupure"))
            if not kind:
                raise SourceError("Type de coupure Enedis inconnu")
            if truth(item.get("coupureClose")) or kind == "restored":
                continue
            key = item.get("idCoupure")
            if not key:
                raise SourceError("Identifiant de coupure Enedis absent")
            events.append(dict(key=str(key), kind=kind, title=item["incidentCoupure"],
                streets=", ".join(a.get("localisation", "") for a in item.get("listeAdresses", [])),
                source_started=item.get("dateCoupure"), eta=item.get("dateRealimentation"),
                details=dict(source_id=str(key), source_state=item.get("etatCoupure"),
                    insee=item.get("codeInsee"), households=item.get("nbFoyersCoupes"),
                    scope="municipality", addresses=[dict(label=a.get("localisation", ""), households=a.get("nbFoyersCoupes")) for a in item.get("listeAdresses", [])])))
    else:
        matched = [i for i in items if truth(i.get("resultRue"))]
        if not matched:
            raise SourceError("Enedis ne confirme pas le résultat pour cette adresse")
        for item in matched:
            kind = category(item.get("status"))
            if not kind:
                raise SourceError("Type de coupure Enedis inconnu")
            if kind == "restored":
                continue
            # Le résultat de rue n'expose pas d'idCoupure : épisode observé par rue/type.
            key = hashlib.sha256((place["insee"] + place["street"] + item["status"]).encode()).hexdigest()[:24]
            events.append(dict(key=key, kind=kind, title=item["status"], streets=place["street"] or place["label"], source_started=None, eta=item.get("dateRealim"),
                details=dict(source_id=None, source_state=None, insee=place["insee"], households=None,
                    scope="street", addresses=[dict(label=place["street"] or place["label"], households=None)])))
    events = list({e["key"]: e for e in events}.values())
    status = "outage" if any(e["kind"] == "outage" for e in events) else "works" if events else "ok"
    # Une commune sans résultat doit être explicitement reconnue par Enedis.
    if municipality and not events and data.get("streetNotFound") not in (False, "false"):
        raise SourceError("Résultat communal ambigu")
    message = {"outage": "Incident signalé par Enedis", "works": "Travaux signalés par Enedis", "ok": "Aucune coupure active détectée par Enedis"}[status]
    return dict(status=status, message=message, events=events, definitive=True)


def check(place):
    data = fetch_json(SOURCE + "/panne-interruption-ajax?" + urllib.parse.urlencode(params(place)))
    return normalize(data, place)
