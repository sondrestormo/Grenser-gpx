from flask import Flask, request, render_template, send_file
import requests
import gpxpy
import gpxpy.gpx
from shapely.geometry import shape, Polygon, MultiPolygon
import tempfile
import json
import pandas as pd
import folium
from fastkml import kml

app = Flask(__name__)

WFS_URL = "https://wfs.geonorge.no/skwms1/wfs.eiendom"
GEO_URL = "https://ws.geonorge.no/adresser/v1/sok"

def fetch_geojson(kommune, gnr, bnr):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": "matrikkel:Eiendom",
        "outputFormat": "application/json",
        "CQL_FILTER": f"kommunenummer='{kommune}' AND gardsnummer='{gnr}' AND bruksnummer='{bnr}'"
    }
    r = requests.get(WFS_URL, params=params)
    if r.status_code != 200 or not r.text.strip().startswith("{"):
        raise Exception("Ugyldig svar fra Kartverket WFS – sjekk gårds-/bruksnummer")
    return r.json()

def lookup_by_address(adresse):
    r = requests.get(GEO_URL, params={"sok": adresse, "treffPerSide": 1})
    if r.status_code != 200 or not r.text.strip().startswith("{"):
        raise Exception("Ugyldig svar fra adresseoppslag – sjekk stavemåte")
    data = r.json()
    if data["adresser"]:
        a = data["adresser"][0]
        return a["adressekode"]["kommunenummer"], a["matrikkelnummer"]["gardsnummer"], a["matrikkelnummer"]["bruksnummer"]
    return None

def convert_to_gpx(geojson):
    gpx = gpxpy.gpx.GPX()
    for feature in geojson['features']:
        geom = shape(feature['geometry'])
        polygons = [geom] if isinstance(geom, Polygon) else geom.geoms if isinstance(geom, MultiPolygon) else []
        for polygon in polygons:
            segment = gpxpy.gpx.GPXTrackSegment()
            for lon, lat in polygon.exterior.coords:
                segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon))
            gpx.tracks.append(gpxpy.gpx.GPXTrack(segments=[segment]))
    return gpx

def convert_to_kml(geojson):
    k = kml.KML()
    ns = "{http://www.opengis.net/kml/2.2}"
    doc = kml.Document(ns, "1", "Eiendom", "Eiendomsgrenser")
    k.append(doc)
    for feature in geojson["features"]:
        geom = shape(feature["geometry"])
        placemark = kml.Placemark(ns, "2", "Eiendom", "")
        placemark.geometry = geom
        doc.append(placemark)
    return k.to_string(prettyprint=True)

def create_map(geojson):
    if not geojson["features"]:
        return ""
    geom = shape(geojson["features"][0]["geometry"])
    bounds = geom.bounds
    m = folium.Map(location=[(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2], zoom_start=16)
    folium.GeoJson(geojson, name="Eiendom").add_to(m)
    map_path = tempfile.NamedTemporaryFile(delete=False, suffix=".html").name
    m.save(map_path)
    return map_path

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        filetype = request.form.get("filetype", "gpx")
        result_geojson = {"type": "FeatureCollection", "features": []}

        try:
            if "fil" in request.files and request.files["fil"].filename:
                df = pd.read_csv(request.files["fil"])
                for _, row in df.iterrows():
                    gj = fetch_geojson(str(row["kommune"]), str(row["gnr"]), str(row["bnr"]))
                    result_geojson["features"].extend(gj["features"])
            elif request.form.get("adresse"):
                parts = lookup_by_address(request.form.get("adresse"))
                if not parts:
                    return "Fant ikke eiendom for oppgitt adresse"
                kommune, gnr, bnr = parts
                gj = fetch_geojson(kommune, gnr, bnr)
                result_geojson["features"].extend(gj["features"])
            else:
                kommune = request.form["kommune"]
                gnr = request.form["gnr"]
                bnr = request.form["bnr"]
                gj = fetch_geojson(kommune, gnr, bnr)
                result_geojson["features"].extend(gj["features"])

            map_path = create_map(result_geojson)
            with open(map_path, "r", encoding="utf-8") as f:
                map_html = f.read()

            if filetype == "gpx":
                gpx = convert_to_gpx(result_geojson)
                output = gpx.to_xml()
                ext = ".gpx"
            else:
                output = convert_to_kml(result_geojson)
                ext = ".kml"

            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(output.encode("utf-8"))
                tmp_path = tmp.name

            return render_template("index.html", map_html=map_html, download_link=tmp_path.split("/")[-1])
        except Exception as e:
            return f"Feil: {e}"

    return render_template("index.html", map_html=None)

@app.route("/nedlast/<path:filename>")
def nedlast(filename):
    return send_file(f"/tmp/{filename}", as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)