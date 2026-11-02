"""ScanniX offline engine: official list downloads, local storage, matching,
country programme review, screening history and standalone HTML/CSV reports.

Everything is stored on this computer in ~/.scannix (SQLite). Nothing you
screen is sent anywhere unless you use the optional Live API check.
"""
import csv, datetime, hashlib, html, io, json, logging, os, re, sqlite3, threading, time
import xml.etree.ElementTree as ET

try:
    import truststore  # Windows / macOS certificate store (company proxies)
    truststore.inject_into_ssl()
except Exception:
    pass
try:
    import requests
except ImportError:
    requests = None
try:
    from unidecode import unidecode
except ImportError:
    def unidecode(s): return s
try:
    from rapidfuzz import fuzz
    def _ratio(a, b): return fuzz.ratio(a, b)
    def _tsr(a, b): return fuzz.token_sort_ratio(a, b)
except ImportError:
    from difflib import SequenceMatcher
    def _ratio(a, b): return SequenceMatcher(None, a, b).ratio() * 100
    def _tsr(a, b): return _ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))

HERE = getattr(__import__("sys"), "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(os.path.expanduser("~"), ".scannix")
os.makedirs(APP_DIR, exist_ok=True)
DB_PATH = os.path.join(APP_DIR, "scannix.db")
CFG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "app.log")
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scannix")

# ------------------------------------------------------------------ text
with open(os.path.join(HERE, "scannix_text.json"), encoding="utf-8") as f:
    TEXT = json.load(f)
LANGS = {"en": "English", "ar": "العربية", "fr": "Français", "es": "Español", "hi": "हिन्दी", "ru": "Русский", "zh": "中文"}
COUNTRIES = dict(sorted(TEXT["countries"].items(), key=lambda kv: kv[1]))  # ISO code -> English name


def t(key, lang="en"):
    return TEXT["copy"].get(lang, {}).get(key) or TEXT["copy"]["en"].get(key) or key


def disclaimer(lang="en"):
    d = TEXT["disclaimer"].get(lang) or TEXT["disclaimer"]["en"]
    return d["title"], d["text"]


def type_label(kind, lang="en"):
    return t({"individual": "individual", "entity": "entity", "vessel": "vessel", "aircraft": "aircraft"}.get(kind, "other"), lang)


# ------------------------------------------------------------------ sources
SOURCES = [
    {"id": "ofac", "code": "US", "name": "OFAC SDN", "authority": "US Department of the Treasury", "country": "United States",
     "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
     "website": "https://ofac.treasury.gov/sanctions-list-service"},
    {"id": "un", "code": "UN", "name": "UN Consolidated", "authority": "United Nations Security Council", "country": "United Nations",
     "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
     "website": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list"},
    {"id": "uk", "code": "UK", "name": "UK Sanctions List", "authority": "UK Foreign, Commonwealth & Development Office", "country": "United Kingdom",
     "url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml",
     "website": "https://www.gov.uk/government/publications/the-uk-sanctions-list"},
    {"id": "eu", "code": "EU", "name": "EU Consolidated", "authority": "European Commission (FSF)", "country": "European Union",
     "url": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
     "website": "https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions"},
    {"id": "uae", "code": "AE", "name": "UAE Local Terrorist List", "authority": "UAE Executive Office for Control & Non-Proliferation", "country": "United Arab Emirates",
     "url": None, "website": "https://www.uaeiec.gov.ae/en-us/un-page"},
    {"id": "india", "code": "IN", "name": "India UAPA List", "authority": "Ministry of Home Affairs, India", "country": "India",
     "url": None, "website": "https://www.mha.gov.in/en/banned-organisations"},
]
SRC = {s["id"]: s for s in SOURCES}
ENTITY_TYPES = ["all", "individual", "entity", "vessel", "aircraft"]

DEFAULT_CFG = {"language": "en", "auto_update_daily": True, "threshold": 80, "lists": [s["id"] for s in SOURCES],
               "yente_url": "https://api.opensanctions.org", "opensanctions_api_key": "", "sanctions_io_key": "",
               "apify_token": "", "apify_actor": ""}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f: cfg.update(json.load(f))
        except Exception: pass
    return cfg


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ storage
def _db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS snapshots(source TEXT PRIMARY KEY, records TEXT, record_count INTEGER DEFAULT 0,
        updated_at TEXT, source_date TEXT, last_error TEXT, attempted_at TEXT);
    CREATE TABLE IF NOT EXISTS custom_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, name TEXT,
        countries TEXT, entity_type TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, query TEXT,
        country TEXT, entity_type TEXT, threshold INTEGER, lists TEXT, match_count INTEGER, top_score REAL, results TEXT);
    CREATE INDEX IF NOT EXISTS ix_hist_ts ON history(ts);
    """)
    return con


DB = _db()
DB_LOCK = threading.RLock()
UPDATE_LOCK = threading.Lock()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fmt_date(iso, lang="en"):
    if not iso: return t("never", lang)
    try:
        d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%d %b %Y, %H:%M") + " UTC"
    except Exception:
        return iso


def snapshots():
    with DB_LOCK:
        rows = DB.execute("SELECT source, record_count, updated_at, source_date, last_error, attempted_at FROM snapshots").fetchall()
    out = {r[0]: {"count": r[1] or 0, "updated_at": r[2], "source_date": r[3], "error": r[4], "attempted_at": r[5]} for r in rows}
    with DB_LOCK:
        for src, n in DB.execute("SELECT source, COUNT(*) FROM custom_entries GROUP BY source"):
            out.setdefault(src, {"count": 0, "updated_at": None, "source_date": None, "error": None, "attempted_at": None})
            out[src]["custom"] = n
    return out


def last_update():
    with DB_LOCK:
        r = DB.execute("SELECT MAX(updated_at) FROM snapshots").fetchone()
    return r[0] if r else None


def total_records():
    s = snapshots()
    return sum(v["count"] + v.get("custom", 0) for v in s.values())


# ------------------------------------------------------------------ parsing
def _local(tag): return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name and c.text and c.text.strip(): return c.text.strip()
    return ""


def _all(el, name): return [c.text.strip() for c in el.iter() if _local(c.tag) == name and c.text and c.text.strip()]


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen: seen.add(x); out.append(x)
    return out


def parse_ofac(raw):
    root = ET.fromstring(raw)
    pub = next((c for c in root if _local(c.tag) == "publshInformation"), None)
    source_date = _text(pub, "Publish_Date") if pub is not None else ""
    expected = int(_text(pub, "Record_Count") or 0) if pub is not None else 0
    kinds = {"Individual": "individual", "Entity": "entity", "Vessel": "vessel", "Aircraft": "aircraft"}
    recs = []
    for e in root:
        if _local(e.tag) != "sdnEntry": continue
        g = lambda n: next((c.text.strip() for c in e if _local(c.tag) == n and c.text), "")
        typ = g("sdnType")
        nm = " ".join(x for x in [g("firstName"), g("lastName")] if x) if typ == "Individual" else (g("lastName") or g("firstName"))
        aliases = []
        for aka in e.iter():
            if _local(aka.tag) == "aka":
                fn = next((c.text for c in aka if _local(c.tag) == "firstName" and c.text), "")
                ln = next((c.text for c in aka if _local(c.tag) == "lastName" and c.text), "")
                aliases.append(" ".join(x for x in [fn, ln] if x))
        countries = [c.text.strip() for a in e.iter() if _local(a.tag) == "address" for c in a if _local(c.tag) == "country" and c.text]
        recs.append({"id": g("uid"), "name": nm, "aliases": _uniq(aliases), "type": kinds.get(typ, "other"),
                     "countries": _uniq(countries), "programs": _all(e, "program"), "dates": _all(e, "dateOfBirth"),
                     "remarks": g("remarks")[:1500]})
    if expected and len(recs) != expected: raise ValueError("OFAC list count mismatch")
    return recs, source_date


def parse_un(raw):
    root = ET.fromstring(raw)
    source_date = root.attrib.get("dateGenerated", "")
    recs = []
    for tag, kind in (("INDIVIDUAL", "individual"), ("ENTITY", "entity")):
        for el in root.iter(tag):
            g = lambda n: (el.findtext(n) or "").strip()
            nm = " ".join(x for x in [g("FIRST_NAME"), g("SECOND_NAME"), g("THIRD_NAME"), g("FOURTH_NAME")] if x)
            aliases = [(a.findtext("ALIAS_NAME") or "").strip() for a in el.iter(f"{tag}_ALIAS")]
            nat = [v.text.strip() for n in el.iter("NATIONALITY") for v in n.iter("VALUE") if v.text]
            addr = [(a.findtext("COUNTRY") or "").strip() for a in el.iter(f"{tag}_ADDRESS")]
            dob = [(d.findtext("DATE") or d.findtext("YEAR") or "").strip() for d in el.iter("INDIVIDUAL_DATE_OF_BIRTH")]
            recs.append({"id": g("REFERENCE_NUMBER") or g("DATAID"), "name": nm, "aliases": _uniq(aliases), "type": kind,
                         "countries": _uniq(nat + addr), "programs": _uniq([g("UN_LIST_TYPE")]), "dates": _uniq(dob),
                         "remarks": g("COMMENTS1")[:1500]})
    return recs, source_date


def parse_uk(raw):
    root = ET.fromstring(raw)
    source_date = _text(root, "DateGenerated")
    kinds = {"Individual": "individual", "Entity": "entity", "Ship": "vessel"}
    recs = []
    for d in root.iter():
        if _local(d.tag) != "Designation": continue
        names = []
        for n in d.iter():
            if _local(n.tag) != "Name": continue
            full = " ".join(x for x in [_text(n, f"Name{i}") for i in range(1, 7)] if x)
            if full: names.append((full, _text(n, "NameType")))
        primary = next((n for n in names if n[1] == "Primary Name"), names[0] if names else ("", ""))
        recs.append({"id": _text(d, "UniqueID"), "name": primary[0],
                     "aliases": _uniq([n[0] for n in names if n is not primary] + _all(d, "NameNonLatinScript")),
                     "type": kinds.get(_text(d, "IndividualEntityShip"), "other"),
                     "countries": _uniq(_all(d, "AddressCountry") + _all(d, "Nationality") + _all(d, "CurrentBelievedFlagOfShip")),
                     "programs": _uniq(_all(d, "RegimeName")), "dates": _uniq(_all(d, "DOB")),
                     "remarks": _text(d, "OtherInformation")[:1500]})
    return recs, source_date


def parse_eu(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    rows = csv.reader(io.StringIO(text), delimiter=";")
    h = next(rows); col = {n: (h.index(n) if n in h else -1) for n in [
        "fileGenerationDate", "Entity_LogicalId", "Entity_EU_ReferenceNumber", "Entity_SubjectType_ClassificationCode",
        "Entity_Remark", "Entity_Regulation_Programme", "NameAlias_WholeName", "Address_CountryDescription",
        "Citizenship_CountryDescription", "BirthDate_BirthDate"]}
    if any(v < 0 for v in col.values()): raise ValueError("EU list format changed")
    g = lambda r, n: r[col[n]].strip() if col[n] < len(r) else ""
    m, source_date = {}, ""
    for r in rows:
        if not r: continue
        lid = g(r, "Entity_LogicalId")
        if not lid: continue
        source_date = source_date or g(r, "fileGenerationDate")
        rec = m.get(lid)
        if rec is None:
            st = g(r, "Entity_SubjectType_ClassificationCode")
            rec = m[lid] = {"id": g(r, "Entity_EU_ReferenceNumber") or lid, "name": "", "aliases": [], "type":
                            "individual" if st == "person" else "entity" if st == "enterprise" else "other",
                            "countries": [], "programs": [], "dates": [], "remarks": g(r, "Entity_Remark")[:1500]}
        whole = g(r, "NameAlias_WholeName")
        if whole:
            if not rec["name"]: rec["name"] = whole
            elif whole != rec["name"] and whole not in rec["aliases"]: rec["aliases"].append(whole)
        for c in (g(r, "Address_CountryDescription"), g(r, "Citizenship_CountryDescription")):
            if c and c != "UNKNOWN" and c not in rec["countries"]: rec["countries"].append(c)
        b = g(r, "BirthDate_BirthDate"); p = g(r, "Entity_Regulation_Programme")
        if b and b not in rec["dates"]: rec["dates"].append(b)
        if p and p not in rec["programs"]: rec["programs"].append(p)
    return list(m.values()), source_date


PARSERS = {"ofac": parse_ofac, "un": parse_un, "uk": parse_uk, "eu": parse_eu}


# ------------------------------------------------------------------ download / update
def _download(url, label, progress):
    if not requests: raise RuntimeError("The 'requests' component is missing - delete the .venv folder and run the launcher again")
    last_err = None
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=(20, 90), headers={"User-Agent": "ScanniX/2.0 (Compliance screening)"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0); buf = io.BytesIO(); last = 0
                for chunk in r.iter_content(256 * 1024):
                    buf.write(chunk); n = buf.tell()
                    if n - last >= 1024 * 1024:
                        last = n
                        progress(f"{label}: downloading {n / 1e6:.1f} MB" + (f" ({n * 100 // total}%)" if total else "") + " ...")
                return buf.getvalue()
        except Exception as e:
            last_err = e; log.warning("%s download attempt %d failed: %s", label, attempt + 1, e)
            progress(f"{label}: retrying ({type(e).__name__}) ...")
            time.sleep(4)
    raise RuntimeError(f"Could not download. Check internet / company firewall. {last_err}"[:300])


def update_source(sid, progress=print):
    s = SRC[sid]
    with DB_LOCK:
        DB.execute("INSERT OR IGNORE INTO snapshots(source) VALUES(?)", (sid,))
        DB.execute("UPDATE snapshots SET attempted_at=? WHERE source=?", (now(), sid)); DB.commit()
    try:
        progress(f"{s['name']}: connecting ...")
        raw = _download(s["url"], s["name"], progress)
        progress(f"{s['name']}: reading records ...")
        recs, sdate = PARSERS[sid](raw)
        if len(recs) < 100 or any(not r["name"] or not r["id"] for r in recs):
            raise ValueError("Incomplete sanctions list - previous list kept")
        with DB_LOCK:
            DB.execute("UPDATE snapshots SET records=?, record_count=?, updated_at=?, source_date=?, last_error=NULL WHERE source=?",
                       (json.dumps(recs, ensure_ascii=False), len(recs), now(), sdate, sid)); DB.commit()
        progress(f"{s['name']}: {len(recs):,} records loaded"); log.info("%s updated %d (%s)", sid, len(recs), hashlib.sha256(raw).hexdigest()[:12])
        return None
    except Exception as e:
        with DB_LOCK:
            DB.execute("UPDATE snapshots SET last_error=? WHERE source=?", (str(e)[:400], sid)); DB.commit()
        progress(f"{s['name']}: update failed - {e}"); log.error("%s failed: %s", sid, e)
        return f"{s['name']}: {e}"


def update_all(progress=print, done=None, only=None):
    if not UPDATE_LOCK.acquire(blocking=False):
        progress("An update is already running - please wait ..."); return None
    errors = []
    try:
        for s in SOURCES:
            if s["url"] and (only is None or s["id"] == only):
                err = update_source(s["id"], progress)
                if err: errors.append(err)
        INDEX.invalidate()
        progress(t("partial") if errors else f"{t('saved')} {total_records():,} records ready.")
    finally:
        UPDATE_LOCK.release()
    if done: done(errors)
    return errors


def update_due():
    s = snapshots()
    for x in SOURCES:
        if not x["url"]: continue
        u = s.get(x["id"], {}).get("updated_at")
        if not u: return True
        if (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(u)).total_seconds() > 86400: return True
    return False


def scheduler(cfg_getter, progress=lambda m: None, done=None):
    while True:
        cfg = cfg_getter()
        if total_records() == 0 or (cfg.get("auto_update_daily") and update_due()):
            update_all(progress, done)
        time.sleep(1800)


# ------------------------------------------------------------------ matching
AIRLINE = re.compile(r"\b(air|airline|airlines|airways|aviation|aero|aerospace)\b", re.I)
SHIPPING = re.compile(r"\b(shipping|maritime|tanker|tankers|navigation|marine|lines)\b", re.I)


def normalize(s):
    s = unidecode(s or "").lower()
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", s).split())


def type_matches(rec, kind):
    if kind == "all" or rec["type"] == kind: return True
    if kind == "aircraft": return rec["type"] == "entity" and bool(AIRLINE.search(rec["name"]))
    if kind == "vessel": return rec["type"] == "entity" and bool(SHIPPING.search(rec["name"]))
    return False


def country_matches(countries, country):
    # Records without country data are never excluded: lists often omit addresses.
    if not countries or not country: return True
    q = normalize(country)
    for c in countries:
        n = normalize(c)
        if n and (q in n or n in q): return True
    return False


def _tk(tok): return tok if len(tok) <= 3 else tok[:3]


class Index:
    def __init__(self): self._data = None; self._lock = threading.Lock()

    def invalidate(self): self._data = None

    def get(self):
        with self._lock:
            if self._data is None: self._data = self._build()
            return self._data

    def _build(self):
        records = []
        with DB_LOCK:
            rows = DB.execute("SELECT source, records FROM snapshots WHERE records IS NOT NULL").fetchall()
            customs = DB.execute("SELECT id, source, name, countries, entity_type, created_at FROM custom_entries").fetchall()
        for sid, raw in rows:
            for r in json.loads(raw): r["source"] = sid; records.append(r)
        for cid, src, nm, ctry, et, created in customs:
            records.append({"id": f"USER-{cid}", "name": nm, "aliases": [], "type": et or "other", "countries": json.loads(ctry or "[]"),
                            "programs": ["Added on this computer"], "dates": [], "remarks": f"Added {created[:10]}", "source": src, "custom": True})
        entries = []
        for r in records:
            for v in [r["name"]] + list(r.get("aliases") or []):
                n = normalize(v)
                if n: entries.append((r, v, n, {_tk(x) for x in n.split()}))
        return records, entries


INDEX = Index()


def screen_name(name, threshold=80, kind="all", country="", lists=None, limit=50):
    q = normalize(name)
    if len(q) < 2: return []
    lists = set(lists or SRC)
    qkeys = {_tk(x) for x in q.split()}
    best = {}
    _, entries = INDEX.get()
    for rec, value, norm, keys in entries:
        if rec["source"] not in lists or not (qkeys & keys): continue
        if not type_matches(rec, kind) or not country_matches(rec.get("countries"), country): continue
        score = max(_ratio(q, norm), _tsr(q, norm))
        if score < threshold: continue
        k = (rec["source"], rec["id"])
        if k not in best or score > best[k]["score"]:
            best[k] = dict(rec, score=round(score), matched=value)
    return sorted(best.values(), key=lambda m: -m["score"])[:limit]


def list_records(source="all", name="", country=""):
    recs, _ = INDEX.get(); q = normalize(name)
    out = []
    for r in recs:
        if source != "all" and r["source"] != source: continue
        if q and not any(q in normalize(v) for v in [r["name"]] + list(r.get("aliases") or [])): continue
        if country and not (r.get("countries") and country_matches(r["countries"], country)): continue
        out.append(r)
    return out


# ------------------------------------------------------------------ custom entries (UAE / India / any list)
def add_entries(source, entries, kind="individual"):
    rows = [(source, e["name"].strip()[:300], json.dumps([e["country"].strip()] if e.get("country") else []), kind, now())
            for e in entries if e.get("name", "").strip()]
    with DB_LOCK:
        DB.executemany("INSERT INTO custom_entries(source, name, countries, entity_type, created_at) VALUES(?,?,?,?,?)", rows); DB.commit()
    INDEX.invalidate(); return len(rows)


def custom_entries():
    with DB_LOCK:
        rows = DB.execute("SELECT id, source, name, countries, entity_type, created_at FROM custom_entries ORDER BY id DESC").fetchall()
    return [{"id": r[0], "source": r[1], "name": r[2], "countries": json.loads(r[3] or "[]"), "type": r[4], "created_at": r[5]} for r in rows]


def delete_entry(eid):
    with DB_LOCK: DB.execute("DELETE FROM custom_entries WHERE id=?", (eid,)); DB.commit()
    INDEX.invalidate()


def parse_name_lines(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        name, _, country = line.partition(",")
        if name.strip(): out.append({"name": name.strip(), "country": country.strip()})
    return out


# ------------------------------------------------------------------ spreadsheets
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SHEET_ROWS = 5001
MAX_BATCH_ROWS = 200


def read_sheet(path):
    if os.path.getsize(path) > MAX_UPLOAD_BYTES: raise ValueError("File is larger than 5 MB.")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True); ws = wb.worksheets[0]
        rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_SHEET_ROWS: break
            rows.append(["" if v is None else str(v).strip() for v in r[:50]])
        wb.close()
    elif ext in (".csv", ".txt"):
        with open(path, "rb") as f: raw = f.read()
        text = raw.decode("utf-8-sig", errors="replace")
        try: dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error: dialect = csv.excel
        rows = [[c.strip() for c in r[:50]] for i, r in enumerate(csv.reader(io.StringIO(text), dialect)) if i < MAX_SHEET_ROWS]
    else:
        raise ValueError("Use a CSV or Excel (.xlsx) file.")
    rows = [r for r in rows if any(r)]
    if not rows: raise ValueError(t("noRows"))
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # Only treat the first row as column titles when it looks like one; otherwise
    # the first name in a header-less file would silently be skipped.
    if len(rows) > 1 and looks_like_header(rows[0]):
        return [h or f"Column {i + 1}" for i, h in enumerate(rows[0])], rows[1:]
    return [f"Column {i + 1}" for i in range(width)], rows


HEADER_WORDS = re.compile(r"name|nom|nombre|nome|имя|नाम|اسم|姓名|entity|customer|client|company|party|vessel|beneficiary|country|nationality|pays|país|pais|دولة|بلد|страна|देश|国家", re.I)


def looks_like_header(row):
    return any(HEADER_WORDS.search(c) for c in row) and not any(re.fullmatch(r"\d+", c.strip()) for c in row)


def guess_column(header, words):
    for i, h in enumerate(header):
        if any(w in h.lower() for w in words): return i
    return None


COUNTRY_ALIASES = {"dprk": "KP", "democratic people s republic of korea": "KP", "korea north": "KP", "north korea": "KP",
                   "iran": "IR", "russia": "RU", "syria": "SY", "burma": "MM", "venezuela": "VE", "drc": "CD", "dr congo": "CD",
                   "congo kinshasa": "CD", "belarus": "BY", "cuba": "CU", "sudan": "SD", "south sudan": "SS", "libya": "LY",
                   "usa": "US", "united states": "US", "uk": "GB", "uae": "AE", "laos": "LA", "vietnam": "VN", "bolivia": "BO",
                   "tanzania": "TZ", "moldova": "MD", "palestine": "PS", "turkey": "TR", "turkiye": "TR"}


def country_code(text):
    """Exact country-name lookup (not fuzzy), so ordinary names are never mistaken for countries."""
    n = normalize(text)
    if not n: return None
    if len(n) == 2 and n.upper() in COUNTRIES: return n.upper()
    for code, name in COUNTRIES.items():
        if normalize(name) == n: return code
    return COUNTRY_ALIASES.get(n)


def country_flag(*values):
    """First value that names a country with a confirmed OFAC or UN sanctions programme."""
    for v in values:
        code = country_code(v)
        if code and (OFAC_PROGRAMMES.get(code) or UN_PROGRAMMES.get(code)):
            progs = OFAC_PROGRAMMES.get(code, []) + UN_PROGRAMMES.get(code, [])
            return {"code": code, "country": COUNTRIES[code], "programmes": [p["name"] for p in progs],
                    "authorities": sorted({p["authority"] for p in progs}), "url": progs[0]["url"]}
    return None


def screen_batch(rows, name_col, country_col, threshold, kind, lists, progress=lambda i, n: None):
    usable = [r for r in rows if name_col < len(r) and r[name_col].strip()][:MAX_BATCH_ROWS]
    out = []
    for i, r in enumerate(usable):
        nm = r[name_col].strip(); ctry = r[country_col].strip() if country_col is not None and country_col < len(r) else ""
        hits = screen_name(nm, threshold, kind, ctry, lists, limit=10)
        out.append({"row": i + 1, "name": nm, "country": ctry, "matches": hits, "country_flag": country_flag(nm, ctry)})
        progress(i + 1, len(usable))
    return out


# ------------------------------------------------------------------ history
def save_history(mode, query, country, kind, threshold, lists, matches):
    slim = [{k: m.get(k) for k in ("name", "matched", "score", "source", "type", "id", "countries", "programs")} for m in matches[:50]]
    with DB_LOCK:
        DB.execute("INSERT INTO history(ts, mode, query, country, entity_type, threshold, lists, match_count, top_score, results) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (now(), mode, query, country, kind, threshold, ",".join(lists), len(matches), matches[0]["score"] if matches else 0,
                    json.dumps(slim, ensure_ascii=False))); DB.commit()


def history(name="", country="", date_from="", date_to=""):
    sql, p = "SELECT id, ts, mode, query, country, entity_type, threshold, lists, match_count, top_score, results FROM history WHERE 1=1", []
    if name: sql += " AND lower(query) LIKE ?"; p.append(f"%{name.lower()}%")
    if country: sql += " AND (lower(country) LIKE ? OR lower(query) LIKE ?)"; p += [f"%{country.lower()}%"] * 2
    if date_from: sql += " AND substr(ts,1,10) >= ?"; p.append(date_from)
    if date_to: sql += " AND substr(ts,1,10) <= ?"; p.append(date_to)
    with DB_LOCK:
        rows = DB.execute(sql + " ORDER BY id DESC LIMIT 2000", p).fetchall()
    return [{"id": r[0], "ts": r[1], "mode": r[2], "query": r[3], "country": r[4], "type": r[5], "threshold": r[6],
             "lists": r[7], "match_count": r[8], "top_score": r[9], "results": json.loads(r[10] or "[]"),
             # Worked out when shown, so older history entries (e.g. "North Korea") are flagged too.
             "country_flag": country_flag(r[3] or "", r[4] or "")} for r in rows]


def delete_history(hid):
    with DB_LOCK: DB.execute("DELETE FROM history WHERE id=?", (hid,)); DB.commit()


# ------------------------------------------------------------------ country / trade review
OFAC_DIR = "https://ofac.treasury.gov/sanctions-programs-and-country-information"
PROGRAMME_CHECKED = "2026-10-24"
_TARGETED = "Targeted blocking and/or activity-specific restrictions. This is not a blanket ban on all trade with the country. Review designated parties, ownership, goods, services and licences."


def _o(name, path, upd, scope=_TARGETED): return {"name": name, "url": f"{OFAC_DIR}/{path}", "scope": scope, "updated": upd, "authority": "OFAC"}


OFAC_PROGRAMMES = {
    "AF": [_o("Afghanistan-Related Sanctions", "afghanistan-related-sanctions", "2022-02-25")],
    "BY": [_o("Belarus Sanctions", "belarus-sanctions", "2026-10-21")],
    "MM": [_o("Burma-Related Sanctions", "burma", "2026-10-09")],
    "CF": [_o("Central African Republic Sanctions", "central-african-republic-sanctions", "2023-12-08")],
    "CN": [_o("Chinese Military Companies Sanctions", "chinese-military-companies-sanctions", "2022-06-01", "Securities-investment restrictions involving covered Chinese military-industrial complex companies; not a general China trade embargo.")],
    "CU": [_o("Cuba Sanctions", "cuba-sanctions", "2026-10-15", "Broad restrictions under Cuban Assets Control Regulations, 31 CFR Part 515, with exemptions and general/specific licences. Part 516 implements targeted EO 14404 authorities. Persons subject to US jurisdiction must review trade, payments and travel-related rules; nationality alone does not resolve permitted dealings.")],
    "CD": [_o("Democratic Republic of the Congo-Related Sanctions", "democratic-republic-of-the-congo-related-sanctions", "2026-09-23")],
    "HK": [_o("Hong Kong-Related Sanctions", "hong-kong-related-sanctions", "2026-07-17")],
    "IR": [_o("Iran Sanctions", "iran-sanctions", "2026-10-21", "Broad US restrictions and targeted/secondary sanctions authorities. Humanitarian and other authorisations may apply; examine US nexus, goods, parties, banks and licences.")],
    "IQ": [_o("Iraq-Related Sanctions", "iraq-related-sanctions", "2026-10-05")],
    "LB": [_o("Lebanon-Related Sanctions", "lebanon-related-sanctions", "2026-02-27")],
    "LY": [_o("Libya Sanctions", "libya-sanctions", "2026-09-30")],
    "ML": [_o("Mali-Related Sanctions", "mali-related-sanctions", "2026-05-28")],
    "NI": [_o("Nicaragua-related Sanctions", "nicaragua-related-sanctions", "2026-04-16")],
    "KP": [_o("North Korea Sanctions", "north-korea-sanctions", "2026-10-21", "Broad US trade and financial restrictions, targeted blocking, and certain secondary sanctions. UN measures also apply through national implementation. Humanitarian exemptions/licences have conditions; no automatic transaction clearance.")],
    "SY": [_o("Promoting Accountability for Assad and Regional Stabilization Sanctions (PAARSS)", "paarss", "2026-09-24", "Targeted Assad-related and regional-stabilization sanctions remain. The former broad Syria sanctions programme was terminated effective July 1, 2025; do not label Syria as still under that blanket embargo. Check remaining designations, export controls and current authorisations.")],
    "RU": [_o("Russian Harmful Foreign Activities Sanctions", "russian-harmful-foreign-activities-sanctions", "2026-10-21", "Extensive targeted, sectoral, services, financial and trade restrictions with licence-dependent exceptions; not an automatic worldwide ban on every Russia-related transaction."),
           _o("Ukraine-/Russia-related Sanctions", "ukraine-russia-related-sanctions", "2026-10-09", "Targeted and territorial restrictions, including certain occupied regions of Ukraine. Selecting a country cannot resolve the regional location or transaction scope.")],
    "UA": [_o("Ukraine-/Russia-related Sanctions", "ukraine-russia-related-sanctions", "2026-10-09", "Restrictions concern designated parties and certain occupied regions; Ukraine as a whole is not subject to a blanket US trade embargo.")],
    "SO": [_o("Somalia Sanctions", "somalia-sanctions", "2023-05-24")],
    "SS": [_o("South Sudan-Related Sanctions", "south-sudan-related-sanctions", "2023-12-08")],
    "SD": [_o("Sudan and Darfur Sanctions", "sudan-and-darfur-sanctions", "2026-06-26", "Targeted Sudan/Darfur sanctions remain. The earlier comprehensive Sudan embargo was revoked in 2017; current programme presence does not mean all Sudan trade is banned.")],
    "VE": [_o("Venezuela-Related Sanctions", "venezuela-related-sanctions", "2026-10-16", "Government blocking, targeted and sector-specific restrictions with changing licences; not a universal ban on dealings with every Venezuelan person or business.")],
    "YE": [_o("Yemen-related Sanctions", "yemen-related-sanctions", "2021-11-18")],
}
for _c in ("IR", "RU", "KP"):
    OFAC_PROGRAMMES[_c].append(_o("Countering America's Adversaries Through Sanctions Act-Related Sanctions", "countering-americas-adversaries-through-sanctions-act-related-sanctions", "2026-02-24", "CAATSA contains authorities concerning Iran, Russia and North Korea. Applicability depends on the specific statutory conduct and other overlapping programmes."))
OFAC_PROGRAMMES["RU"].append(_o("Magnitsky Sanctions", "the-magnitsky-sanctions", "2023-08-17", "Russia-focused statutory targeted designations; distinct from worldwide Global Magnitsky sanctions."))
UN_PROGRAMMES = {"KP": [{"name": "DPRK / 1718 Sanctions Committee", "authority": "UN", "url": "https://main.un.org/securitycouncil/en/sanctions/1718", "updated": "",
                         "scope": "Security Council DPRK measures include arms, proliferation, sectoral and financial restrictions. Check resolutions, committee exemptions and the law implementing them in the relevant jurisdiction."}]}
JURISDICTIONS = [
    {"id": "uae", "name": "UAE", "url": "https://www.uaeiec.gov.ae/en-us/un-page", "scope": "UN sanctions implementation and targeted financial sanctions; check applicable UAE trade and export controls separately."},
    {"id": "us", "name": "United States", "url": OFAC_DIR, "scope": "OFAC programme rules, authorisations and exceptions; also check applicable export controls."},
    {"id": "uk", "name": "United Kingdom", "url": "https://www.gov.uk/government/collections/uk-sanctions-regimes-under-the-sanctions-act", "scope": "Regime-specific legislation and guidance for financial, trade and services restrictions."},
    {"id": "eu", "name": "European Union", "url": "https://finance.ec.europa.eu/eu-and-world/sanctions-restrictive-measures/overview-sanctions-and-related-resources_en", "scope": "EU restrictive measures and underlying regulations; also check customs and export controls."},
    {"id": "un", "name": "United Nations", "url": "https://main.un.org/securitycouncil/en/sanctions/information", "scope": "Regime-specific Security Council measures and exceptions, as implemented under applicable national law."},
    {"id": "india", "name": "India", "url": "https://www.dgft.gov.in/CP/", "scope": "Current DGFT notifications, import/export policy and SCOMET controls, alongside implementation of UN measures."},
]
_REFS = {"us": {"IR": f"{OFAC_DIR}/iran-sanctions", "KP": f"{OFAC_DIR}/north-korea-sanctions", "CU": f"{OFAC_DIR}/cuba-sanctions", "RU": f"{OFAC_DIR}/ukraine-russia-related-sanctions"},
         "un": {"KP": "https://main.un.org/securitycouncil/en/sanctions/1718"},
         "india": {"KP": "https://www.mea.gov.in/Implementation-of-UNSC-Sanctions-DPRK-Iran.htm", "IR": "https://www.mea.gov.in/Implementation-of-UNSC-Sanctions-DPRK-Iran.htm"}}


def review_country(code, jurisdictions):
    if code not in COUNTRIES or not jurisdictions: raise ValueError("Choose a country and at least one jurisdiction.")
    rows = []
    for j in JURISDICTIONS:
        if j["id"] not in jurisdictions: continue
        progs = OFAC_PROGRAMMES.get(code, []) if j["id"] == "us" else UN_PROGRAMMES.get(code, []) if j["id"] == "un" else []
        rows.append({"id": j["id"], "name": j["name"], "scope": j["scope"], "programmes": progs,
                     "url": progs[0]["url"] if progs else _REFS.get(j["id"], {}).get(code, j["url"]),
                     "confirmed": bool(progs), "checked": PROGRAMME_CHECKED if progs else None})
    return {"code": code, "country": COUNTRIES[code], "rows": rows}


# ------------------------------------------------------------------ reports
def csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def write_csv(path, header, rows, notes=(), lang="en"):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(header)
        for r in rows: w.writerow([csv_safe(v) for v in r])
        w.writerow([])
        for n in notes: w.writerow([csv_safe(n)])
        w.writerow(list(disclaimer(lang)))


def html_report(title, meta, header, rows, notes=(), lang="en", danger_col=None):
    e = lambda v: html.escape("" if v is None else str(v))
    dt, dx = disclaimer(lang)
    def cell(i, c):
        hit = danger_col is not None and i in (danger_col if isinstance(danger_col, (tuple, list)) else (danger_col,)) and str(c) not in ("0", "", "—")
        return f'<td class="{"hit" if hit else ""}">{e(c)}</td>'
    body = "".join("<tr>" + "".join(cell(i, c) for i, c in enumerate(r)) + "</tr>" for r in rows)
    meta_html = "".join(f"<div><span>{e(k)}</span><b>{e(v)}</b></div>" for k, v in meta)
    notes_html = "".join(f"<p class='fine'>{e(n)}</p>" for n in notes)
    rtl = ' dir="rtl"' if lang == "ar" else ""
    empty = f'<tr><td colspan="{len(header)}">—</td></tr>'
    return f"""<!doctype html><html lang="{lang}"{rtl}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | ScanniX</title><style>
body{{font-family:Segoe UI,Manrope,Arial,sans-serif;margin:0;background:#f4f7fa;color:#1f262c}}
main{{max-width:1180px;margin:0 auto;padding:32px 24px}} header{{display:flex;justify-content:space-between;align-items:center;border-top:4px solid #006549;background:#fff;padding:18px 24px;border-radius:6px}}
.logo{{font-size:26px;font-weight:800;color:#1f262c}} .logo b{{color:#006549}} h1{{font-size:26px;margin:24px 0 6px}}
.note{{background:#e5f1ff;border-left:4px solid #0f64bf;padding:12px 16px;margin:18px 0;border-radius:4px;font-size:13px}} .note b{{color:#0f64bf;display:block;margin-bottom:2px}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:16px 0}} .meta div{{background:#fff;border:1px solid #d8dde2;padding:10px 14px;border-radius:4px}}
.meta span{{display:block;font-size:11px;color:#58626a;text-transform:uppercase;letter-spacing:.04em}} table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}
th{{background:#edf1f4;color:#58626a;text-align:left;font-size:11px;text-transform:uppercase;padding:9px;border-bottom:1px solid #d8dde2}} td{{padding:9px;border-bottom:1px solid #edf1f4;vertical-align:top}}
td.hit{{color:#c90f1a;font-weight:700}} .fine{{font-size:12px;color:#58626a}} @media print{{body{{background:#fff}} main{{padding:0}}}}
</style></head><body><main><header><div class="logo">Scanni<b>X</b></div><div class="fine">{e(fmt_date(now(), lang))}</div></header>
<h1>{e(title)}</h1><section class="note" aria-label="Disclaimer"><b>{e(dt)}</b>{e(dx)}</section><div class="meta">{meta_html}</div>
<table><thead><tr>{"".join(f"<th>{e(h)}</th>" for h in header)}</tr></thead><tbody>{body or empty}</tbody></table>
{notes_html}<p class="fine">{e(t("notice", lang))} {e(t("languageNotice", lang))}</p><p class="fine">Generated by ScanniX desktop · {e(now())}</p></main></body></html>"""
