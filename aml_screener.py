import re
#!/usr/bin/env python3
"""
ScanniX - AML / Sanctions screening (offline desktop edition)
=============================================================
Same screens as the ScanniX web app, running entirely on this computer:
  * Sanctions screening (individual / organisation / vessel / aircraft) and Country / trade review
  * Batch screening of CSV / Excel files
  * Screening history with name, country and date filters
  * Manage lists (add names to UAE, India or any list; download filtered lists)
  * Data sources with daily automatic updates (OFAC, UN, UK, EU)
  * Print, HTML and CSV reports that open in any browser

Run modes:
  python aml_screener.py            -> desktop app
  python aml_screener.py --update   -> refresh the official lists once and exit
  python aml_screener.py --server   -> local REST API on http://127.0.0.1:8765
"""
import json, os, sys, tempfile, threading, webbrowser, datetime
import scannix_core as core
from scannix_core import t

C = {"bg": "#f4f7fa", "card": "#ffffff", "fg": "#1f262c", "primary": "#006549", "primary_dark": "#004d38",
     "secondary": "#def3ea", "muted": "#edf1f4", "mutedfg": "#58626a", "accent": "#e8f1fa", "border": "#d8dde2",
     "input": "#b7c1c9", "warning": "#ac6900", "warnbg": "#fff6dd", "danger": "#c90f1a", "dangerbg": "#fdecec",
     "highlight": "#0f64bf", "hlbg": "#e5f1ff", "ok": "#00804f", "odd": "#f8fafb"}
F = "Segoe UI"


# ---------------------------------------------------------------- optional live APIs
def live_check(name, cfg):
    import requests
    out = {}
    if cfg.get("opensanctions_api_key") or "api.opensanctions.org" not in cfg["yente_url"]:
        try:
            h = {"Authorization": f"ApiKey {cfg['opensanctions_api_key']}"} if cfg.get("opensanctions_api_key") else {}
            r = requests.post(f"{cfg['yente_url'].rstrip('/')}/match/sanctions", json={"queries": {"q": {"schema": "Thing", "properties": {"name": [name]}}}}, headers=h, timeout=30)
            r.raise_for_status()
            out["opensanctions"] = [{"name": x["caption"], "score": x["score"], "datasets": x.get("datasets")} for x in r.json()["responses"]["q"]["results"]]
        except Exception as e: out["opensanctions"] = {"error": str(e)}
    if cfg.get("sanctions_io_key"):
        try:
            r = requests.get("https://api.sanctions.io/search/", params={"name": name, "min_score": cfg["threshold"] / 100},
                             headers={"Authorization": f"Bearer {cfg['sanctions_io_key']}", "Accept": "application/json; version=2.0"}, timeout=30)
            r.raise_for_status(); out["sanctions_io"] = r.json().get("results", r.json())
        except Exception as e: out["sanctions_io"] = {"error": str(e)}
    if cfg.get("apify_token") and cfg.get("apify_actor"):
        try:
            from apify_client import ApifyClient
            run = ApifyClient(cfg["apify_token"]).actor(cfg["apify_actor"]).call(run_input={"names": [name]})
            out["apify"] = list(ApifyClient(cfg["apify_token"]).dataset(run["defaultDatasetId"]).iterate_items())[:20]
        except Exception as e: out["apify"] = {"error": str(e)}
    return out or {"info": "No live API configured. Add keys in Settings."}


# ---------------------------------------------------------------- REST server
def run_server(port=8765):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs
    threading.Thread(target=core.scheduler, args=(core.load_cfg,), daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/status": self._send({"last_updated": core.last_update(), "records": core.total_records(), "sources": core.snapshots()})
            elif u.path == "/screen":
                hits = core.screen_name(q.get("name", ""), int(q.get("threshold", 80)), q.get("type", "all"), q.get("country", ""))
                self._send({"matches": [{k: m.get(k) for k in ("name", "matched", "score", "source", "type", "id", "countries", "programs")} for m in hits]})
            elif u.path == "/country": self._send(core.review_country(q.get("code", "").upper(), [j["id"] for j in core.JURISDICTIONS]))
            else: self._send({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/update": threading.Thread(target=core.update_all, daemon=True).start(); self._send({"started": True})
            else: self._send({"error": "not found"}, 404)
    print(f"ScanniX API on http://127.0.0.1:{port}  (/status /screen?name= /country?code= , POST /update)")
    HTTPServer(("127.0.0.1", port), H).serve_forever()


# ---------------------------------------------------------------- GUI
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from scannix_assets import LOGO_PNG, ICON_PNG

    cfg = core.load_cfg()
    root = tk.Tk(); root.title("ScanniX — AML Sanctions Screening")
    root.geometry("1360x860"); root.minsize(1100, 680); root.configure(bg=C["bg"])
    try: root.state("zoomed")
    except Exception: pass
    try:
        icon = tk.PhotoImage(data=ICON_PNG); root.iconphoto(True, icon)
    except Exception: icon = None
    try: logo = tk.PhotoImage(data=LOGO_PNG)
    except Exception: logo = None

    st = ttk.Style(root); st.theme_use("clam")
    st.configure(".", background=C["bg"], foreground=C["fg"], font=(F, 10))
    st.configure("Primary.TButton", background=C["primary"], foreground="#ffffff", bordercolor=C["primary"], lightcolor=C["primary"],
                 darkcolor=C["primary"], font=(F, 10, "bold"), padding=(18, 9), focuscolor=C["primary"])
    st.map("Primary.TButton", background=[("active", C["primary_dark"]), ("disabled", C["input"])])
    st.configure("Secondary.TButton", background=C["card"], foreground=C["fg"], bordercolor=C["input"], lightcolor=C["card"],
                 darkcolor=C["card"], font=(F, 10), padding=(14, 7), focuscolor=C["card"])
    st.map("Secondary.TButton", background=[("active", C["accent"])])
    st.configure("Ghost.TButton", background=C["bg"], foreground=C["mutedfg"], bordercolor=C["bg"], lightcolor=C["bg"], darkcolor=C["bg"],
                 font=(F, 10), padding=(10, 6), focuscolor=C["bg"])
    st.map("Ghost.TButton", background=[("active", C["muted"])], foreground=[("active", C["primary"])])
    st.configure("TEntry", fieldbackground=C["card"], bordercolor=C["input"], lightcolor=C["input"], darkcolor=C["input"], padding=8)
    st.map("TEntry", bordercolor=[("focus", C["primary"])], lightcolor=[("focus", C["primary"])])
    st.configure("TCombobox", fieldbackground=C["card"], background=C["card"], bordercolor=C["input"], arrowcolor=C["primary"], padding=7)
    st.map("TCombobox", fieldbackground=[("readonly", C["card"])], selectbackground=[("readonly", C["card"])], selectforeground=[("readonly", C["fg"])])
    st.configure("Card.TCheckbutton", background=C["card"], font=(F, 10))
    st.map("Card.TCheckbutton", background=[("active", C["card"])], indicatorcolor=[("selected", C["primary"]), ("!selected", C["card"])])
    st.configure("List.TCheckbutton", background=C["secondary"], foreground=C["primary"], font=(F, 10, "bold"), padding=(10, 8))
    st.map("List.TCheckbutton", background=[("active", C["secondary"])], indicatorcolor=[("selected", C["primary"]), ("!selected", C["card"])])
    st.configure("Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["fg"], rowheight=32, font=(F, 10), borderwidth=0)
    st.map("Treeview", background=[("selected", C["secondary"])], foreground=[("selected", C["fg"])])
    st.configure("Treeview.Heading", background=C["muted"], foreground=C["mutedfg"], font=(F, 9, "bold"), relief="flat", padding=(8, 8))
    st.map("Treeview.Heading", background=[("active", C["accent"])])
    st.configure("Horizontal.TScale", background=C["card"], troughcolor=C["secondary"])
    st.configure("Horizontal.TProgressbar", background=C["primary"], troughcolor=C["muted"], bordercolor=C["muted"])

    state = {"page": "screen", "mode": "all", "results": None, "query": None, "country_result": None,
             "batch_file": None, "batch_header": [], "batch_rows": [], "batch_results": None, "status": "Ready"}
    country_names = ["—"] + list(core.COUNTRIES.values())
    name_to_code = {v: k for k, v in core.COUNTRIES.items()}

    # ---------------------------------------------------------- helpers
    def L(): return cfg["language"]

    def lbl(parent, text="", size=10, weight="normal", fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=(F, size, weight), fg=fg or C["fg"], bg=bg or parent["bg"], **kw)

    def card(parent, top=None, **kw):
        f = tk.Frame(parent, bg=C["card"], highlightbackground=C["border"], highlightcolor=C["border"], highlightthickness=1, **kw)
        if top: tk.Frame(f, bg=top, height=3).pack(fill="x")
        return f

    def link(parent, text, url, size=9, fg=None):
        w = lbl(parent, text, size, "bold", fg or C["highlight"], cursor="hand2")
        w.bind("<Button-1>", lambda e: webbrowser.open(url)); return w

    def badge(parent, text, fg, bg, size=9):
        return tk.Label(parent, text=text, font=(F, size, "bold"), fg=fg, bg=bg, padx=8, pady=2)

    def make_tree(parent, cols, heads, widths, height=10):
        wrap = tk.Frame(parent, bg=C["card"])
        tr = ttk.Treeview(wrap, columns=cols, show="headings", height=height)
        for c in cols:
            tr.heading(c, text=heads[c].upper(), anchor="w"); tr.column(c, width=widths.get(c, 120), anchor="w", stretch=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tr.yview); tr.configure(yscrollcommand=sb.set)
        tr.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        tr.tag_configure("odd", background=C["odd"]); tr.tag_configure("high", foreground=C["danger"], font=(F, 10, "bold"))
        tr.tag_configure("mid", foreground=C["warning"], font=(F, 10, "bold")); tr.tag_configure("clear", foreground=C["ok"])
        return wrap, tr

    def save_file(kind, default):
        ext = ".html" if kind == "html" else ".csv"
        return filedialog.asksaveasfilename(defaultextension=ext, initialfile=default + ext,
                                            filetypes=[("HTML report", "*.html")] if kind == "html" else [("CSV", "*.csv")])

    def export(kind, title, meta, header, rows, notes=(), name="scannix_report", danger_col=None):
        if kind == "csv":
            p = save_file("csv", name)
            if p: core.write_csv(p, header, rows, [f"{k}: {v}" for k, v in meta] + list(notes), L()); set_status(f"Saved {p}")
            return
        doc = core.html_report(title, meta, header, rows, notes, L(), danger_col)
        if kind == "print":
            doc = doc.replace("</body>", "<script>window.onload=function(){window.print()}</script></body>")
            fd, p = tempfile.mkstemp(suffix=".html", prefix="scannix_print_"); os.close(fd)
            with open(p, "w", encoding="utf-8") as f: f.write(doc)
            webbrowser.open("file:///" + p.replace(os.sep, "/")); return
        p = save_file("html", name)
        if p:
            with open(p, "w", encoding="utf-8") as f: f.write(doc)
            set_status(f"Saved {p}")
            if messagebox.askyesno("ScanniX", "Report saved. Open it in your browser now?"): webbrowser.open("file:///" + p.replace(os.sep, "/"))

    def export_bar(parent, fn, bg=None):
        bar = tk.Frame(parent, bg=bg or parent["bg"])
        for key, kind in (("print", "print"), ("downloadHtml", "html"), ("downloadCsv", "csv")):
            ttk.Button(bar, text=("🖶  " if kind == "print" else "⭳  ") + t(key, L()), style="Ghost.TButton", command=lambda k=kind: fn(k)).pack(side="left", padx=2)
        return bar

    def country_box(parent, var, width=30):
        cb = ttk.Combobox(parent, textvariable=var, values=country_names, width=width, font=(F, 10))
        def filt(e):
            q = var.get().lower()
            cb["values"] = [c for c in country_names if q in c.lower()] if q and q != "—" else country_names
        cb.bind("<KeyRelease>", filt)
        def focus_in(e):
            if var.get() in ("—", t("chooseCountry", L()), t("countryPlaceholder", L())): var.set("")
        def focus_out(e):
            v = resolve_country(var.get())
            if v: var.set(v)
            elif not var.get().strip(): var.set("—")
        cb.bind("<FocusIn>", focus_in); cb.bind("<FocusOut>", focus_out)
        return cb

    def resolve_country(text):
        q = (text or "").strip().lower()
        if not q or q == "—": return None
        exact = [c for c in country_names[1:] if c.lower() == q]
        if exact: return exact[0]
        starts = [c for c in country_names[1:] if c.lower().startswith(q)]
        if len(starts) >= 1: return starts[0]
        part = [c for c in country_names[1:] if q in c.lower()]
        return part[0] if len(part) == 1 else None

    def country_value(var):
        v = var.get().strip()
        return "" if v in ("", "—", t("countryPlaceholder", L()), t("chooseCountry", L())) else (resolve_country(v) or v)

    # ---------------------------------------------------------- shell
    shell = tk.Frame(root, bg=C["bg"]); shell.pack(fill="both", expand=True)
    status_var = tk.StringVar(value=state["status"])

    def set_status(msg):
        state["status"] = msg; status_var.set(msg)

    def build():
        for w in shell.winfo_children(): w.destroy()
        rtl = L() == "ar"
        # sidebar
        side = tk.Frame(shell, bg=C["card"], width=250, highlightbackground=C["border"], highlightthickness=1)
        side.pack(side="right" if rtl else "left", fill="y"); side.pack_propagate(False)
        tk.Frame(side, bg=C["primary"], height=4).pack(fill="x")
        if logo: tk.Label(side, image=logo, bg=C["card"]).pack(anchor="w", padx=24, pady=(26, 30))
        else: lbl(side, "ScanniX", 22, "bold", C["primary"]).pack(anchor="w", padx=24, pady=(26, 30))
        lbl(side, t("workspace", L()).upper(), 8, "bold", C["mutedfg"]).pack(anchor="w", padx=26, pady=(0, 8))
        for page, key, ico in (("screen", "screening", "⌖"), ("batch", "batch", "▤"), ("history", "history", "↺"),
                               ("lists", "manage", "☰"), ("sources", "sources", "⛁")):
            sel = state["page"] == page
            row = tk.Frame(side, bg=C["secondary"] if sel else C["card"], cursor="hand2")
            row.pack(fill="x", padx=14, pady=2)
            if sel: tk.Frame(row, bg=C["primary"], width=3).pack(side="left", fill="y")
            a = lbl(row, ico, 12, fg=C["primary"] if sel else C["mutedfg"]); a.pack(side="left", padx=(12, 8), pady=10)
            b = lbl(row, t(key, L()), 11, "bold" if sel else "normal", C["primary"] if sel else C["fg"]); b.pack(side="left", pady=10)
            for w in (row, a, b): w.bind("<Button-1>", lambda e, p=page: go(p))
        foot = tk.Frame(side, bg=C["card"]); foot.pack(side="bottom", fill="x", padx=24, pady=20)
        tk.Frame(foot, bg=C["border"], height=1).pack(fill="x", pady=(0, 14))
        lbl(foot, "⛨", 16, fg=C["primary"]).pack(anchor="w")
        lbl(foot, t("legal", L()), 10, "bold").pack(anchor="w", pady=(4, 0))
        lbl(foot, t("footer", L()), 9, fg=C["mutedfg"]).pack(anchor="w")
        s = lbl(foot, "⚙  Settings", 9, "bold", C["primary"], cursor="hand2"); s.pack(anchor="w", pady=(12, 0)); s.bind("<Button-1>", lambda e: settings())
        lbl(foot, "SCANNIX / 2.0 DESKTOP", 8, fg=C["mutedfg"]).pack(anchor="w", pady=(10, 0))

        main = tk.Frame(shell, bg=C["bg"]); main.pack(side="left", fill="both", expand=True)
        top = tk.Frame(main, bg=C["card"], height=62, highlightbackground=C["border"], highlightthickness=1); top.pack(fill="x"); top.pack_propagate(False)
        crumb = tk.Frame(top, bg=C["card"]); crumb.pack(side="left", padx=28)
        names = {"screen": "screening", "batch": "batch", "history": "history", "lists": "manage", "sources": "sources"}
        lbl(crumb, "ScanniX   /   ", 10, fg=C["mutedfg"]).pack(side="left"); lbl(crumb, t(names[state["page"]], L()), 10, "bold").pack(side="left")
        lang_var = tk.StringVar(value=core.LANGS[L()])
        lb = ttk.Combobox(top, textvariable=lang_var, values=list(core.LANGS.values()), state="readonly", width=12, font=(F, 10)); lb.pack(side="right", padx=(6, 28))
        lb.bind("<<ComboboxSelected>>", lambda e: set_lang([k for k, v in core.LANGS.items() if v == lang_var.get()][0]))
        lbl(top, "文A", 10, fg=C["mutedfg"]).pack(side="right")

        sbar = tk.Frame(main, bg=C["card"], highlightbackground=C["border"], highlightthickness=1); sbar.pack(side="bottom", fill="x")
        tk.Label(sbar, textvariable=status_var, font=(F, 9), fg=C["mutedfg"], bg=C["card"], anchor="w").pack(side="left", padx=28, pady=6)
        lbl(sbar, f"© {datetime.date.today().year} ScanniX · " + t("noStorage", L()).replace("Signed-in screenings are saved to your private history only.", "Screenings are saved on this computer only."), 9, fg=C["mutedfg"]).pack(side="right", padx=28)

        # disclaimer
        dt, dx = core.disclaimer(L())
        disc = tk.Frame(main, bg=C["hlbg"], highlightbackground="#c4dcf5", highlightthickness=1); disc.pack(fill="x")
        din = tk.Frame(disc, bg=C["hlbg"]); din.pack(fill="x", padx=28, pady=10)
        lbl(din, "⛨", 14, fg=C["highlight"]).pack(side="left", anchor="n", padx=(0, 10))
        txt = tk.Frame(din, bg=C["hlbg"]); txt.pack(side="left", fill="x", expand=True)
        lbl(txt, dt, 10, "bold", C["highlight"], anchor="w").pack(fill="x")
        dl = lbl(txt, dx, 9, fg="#1e4665", anchor="w", justify="left", wraplength=1000); dl.pack(fill="x")
        txt.bind("<Configure>", lambda e: dl.configure(wraplength=max(400, e.width - 10)))

        # scrollable body
        canvas = tk.Canvas(main, bg=C["bg"], highlightthickness=0); vs = ttk.Scrollbar(main, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set); vs.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=C["bg"]); win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        def wheel(e):
            w = e.widget
            while w is not None:
                if isinstance(w, (ttk.Treeview, tk.Text)) or str(w).endswith("popdown"): return
                w = getattr(w, "master", None)
            canvas.yview_scroll(int(-e.delta / 120) if e.delta else (1 if e.num == 5 else -1), "units")
        root.bind_all("<MouseWheel>", wheel); root.bind_all("<Button-4>", wheel); root.bind_all("<Button-5>", wheel)
        inner = tk.Frame(body, bg=C["bg"]); inner.pack(fill="both", expand=True, padx=36, pady=26)
        state["anchor"] = None
        {"screen": page_screen, "batch": page_batch, "history": page_history, "lists": page_lists, "sources": page_sources}[state["page"]](inner)
        if state.pop("scroll", None) and state.get("anchor") is not None:
            def jump():
                body.update_idletasks(); h = max(1, body.winfo_height())
                canvas.yview_moveto(max(0, (state["anchor"].winfo_y() + state["anchor"].master.winfo_y() - 10) / h))
            root.after(50, jump)

    def go(page): state["page"] = page; build()

    def set_lang(code):
        cfg["language"] = code; core.save_cfg(cfg); build()

    def heading(parent, title, sub, eyebrow=None, action=None):
        h = tk.Frame(parent, bg=C["bg"]); h.pack(fill="x", pady=(0, 20))
        left = tk.Frame(h, bg=C["bg"]); left.pack(side="left", fill="x", expand=True)
        if eyebrow: lbl(left, "●  " + eyebrow, 8, "bold", C["primary"]).pack(anchor="w", pady=(0, 6))
        lbl(left, title, 24, "bold").pack(anchor="w")
        lbl(left, sub, 11, fg=C["mutedfg"]).pack(anchor="w", pady=(6, 0))
        if action: action(h)

    def update_button(parent, only=None):
        def run():
            set_status(t("updating", L()))
            threading.Thread(target=core.update_all, args=(ui_progress, ui_done, only), daemon=True).start()
        return ttk.Button(parent, text="⟳  " + t("refresh", L()), style="Secondary.TButton", command=run)

    # ---------------------------------------------------------- page: screening
    def stats_row(parent):
        snaps = core.snapshots(); total = core.total_records()
        active = sum(1 for s in core.SOURCES if snaps.get(s["id"], {}).get("count") or snaps.get(s["id"], {}).get("custom"))
        row = tk.Frame(parent, bg=C["bg"]); row.pack(fill="x", pady=(0, 22))
        cells = [(t("total", L()), f"{total:,}", C["primary"], t("official", L())), (t("active", L()), f"{active:02d}", C["highlight"], f"/ {len(core.SOURCES):02d}"),
                 (t("updated", L()), core.fmt_date(core.last_update(), L()), None, None), (t("frequency", L()), "◷  " + t("daily", L()).replace("02:00 UTC", "automatic"), None, None)]
        for i, (k, v, color, extra) in enumerate(cells):
            c = tk.Frame(row, bg=C["bg"]); c.grid(row=0, column=i * 2, sticky="nsew")
            lbl(c, k, 10, fg=C["mutedfg"]).pack(anchor="w")
            vr = tk.Frame(c, bg=C["bg"]); vr.pack(anchor="w", pady=(6, 0))
            lbl(vr, v, 26 if color else 12, "bold" if color else "normal", color or C["fg"]).pack(side="left")
            if extra and i == 0: badge(vr, "✓ " + extra, C["ok"], C["secondary"], 8).pack(side="left", padx=10)
            elif extra: lbl(vr, extra, 10, fg=C["mutedfg"]).pack(side="left", padx=6)
            row.columnconfigure(i * 2, weight=1)
            if i < 3: tk.Frame(row, bg=C["border"], width=1).grid(row=0, column=i * 2 + 1, sticky="ns", padx=18)
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(0, 24))

    def coverage_card(parent):
        cv = card(parent); snaps = core.snapshots()
        hd = tk.Frame(cv, bg=C["card"]); hd.pack(fill="x", padx=18, pady=14)
        lbl(hd, t("coverage", L()), 12, "bold").pack(side="left"); lbl(hd, "🌐", 12, fg=C["mutedfg"]).pack(side="right")
        tk.Frame(cv, bg=C["border"], height=1).pack(fill="x")
        for s in core.SOURCES:
            sn = snaps.get(s["id"], {}); n = (sn.get("count") or 0) + (sn.get("custom") or 0)
            r = tk.Frame(cv, bg=C["card"]); r.pack(fill="x", padx=18, pady=9)
            tk.Label(r, text=s["code"], font=(F, 9, "bold"), fg=C["primary"] if s["code"] != "IN" and s["code"] != "EU" and s["code"] != "UN" else C["highlight"],
                     bg=C["secondary"] if s["code"] not in ("IN", "EU", "UN") else C["hlbg"], width=4, pady=6).pack(side="left")
            mid = tk.Frame(r, bg=C["card"]); mid.pack(side="left", fill="x", expand=True, padx=10)
            lbl(mid, s["name"], 10, "bold", anchor="w").pack(fill="x")
            lbl(mid, s["authority"], 8, fg=C["mutedfg"], anchor="w", justify="left", wraplength=170).pack(fill="x")
            lbl(r, f"{n:,}", 10, "bold" if n else "normal", C["fg"] if n else C["mutedfg"]).pack(side="right")
        tk.Frame(cv, bg=C["border"], height=1).pack(fill="x")
        ft = tk.Frame(cv, bg=C["card"]); ft.pack(fill="x", padx=18, pady=12)
        lbl(ft, "✓  " + t("official", L()), 9, fg=C["primary"]).pack(side="left"); lbl(ft, f"{len(core.SOURCES):02d}", 9, "bold", C["primary"]).pack(side="right")
        return cv

    def page_screen(p):
        heading(p, t("screening", L()), t("subtitle", L()), "AML INTELLIGENCE", lambda h: update_button(h).pack(side="right", anchor="n"))
        stats_row(p)
        grid = tk.Frame(p, bg=C["bg"]); grid.pack(fill="x")
        grid.columnconfigure(0, weight=3); grid.columnconfigure(1, weight=1, minsize=300)
        form = card(grid, top=C["primary"]); form.grid(row=0, column=0, sticky="nsew", padx=(0, 22))
        coverage_card(grid).grid(row=0, column=1, sticky="nsew")
        hd = tk.Frame(form, bg=C["card"]); hd.pack(fill="x", padx=24, pady=16)
        lbl(hd, "⌖", 14, fg=C["primary"]).pack(side="left"); lbl(hd, t("newScreen", L()), 14, "bold").pack(side="left", padx=8)
        lbl(hd, "01 / SCREEN", 8, fg=C["mutedfg"]).pack(side="right")
        tk.Frame(form, bg=C["border"], height=1).pack(fill="x")
        fb = tk.Frame(form, bg=C["card"]); fb.pack(fill="x", padx=24, pady=20)

        # mode / entity selector
        sel = tk.Frame(fb, bg=C["muted"], highlightbackground=C["border"], highlightthickness=1); sel.pack(fill="x")
        opts = [("all", "⌖", "all"), ("individual", "👤", "individual"), ("entity", "🏢", "entity"),
                ("vessel", "⛴", "vessel"), ("aircraft", "✈", "aircraft"), ("country", "🌐", "countryScreen")]
        for i, (mode, ico, key) in enumerate(opts):
            on = state["mode"] == mode
            b = tk.Label(sel, text=f"{ico}  {t(key, L())}", font=(F, 10, "bold" if on else "normal"), cursor="hand2",
                         fg="#ffffff" if on else C["fg"], bg=C["primary"] if on else C["muted"], pady=12)
            b.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            b.bind("<Button-1>", lambda e, m=mode: (state.update(mode=m), build()))
        for c in range(3): sel.columnconfigure(c, weight=1, uniform="m")

        if state["mode"] == "country":
            country_form(fb, form); return

        lbl(fb, t("name", L()), 10, "bold").pack(anchor="w", pady=(20, 6))
        name_var = tk.StringVar(value=(state["query"] or {}).get("name", ""))
        ne = ttk.Entry(fb, textvariable=name_var, font=(F, 12)); ne.pack(fill="x"); ne.focus_set()
        row = tk.Frame(fb, bg=C["card"]); row.pack(fill="x", pady=(18, 0)); row.columnconfigure(0, weight=1); row.columnconfigure(1, weight=1)
        cl = tk.Frame(row, bg=C["card"]); cl.grid(row=0, column=0, sticky="ew", padx=(0, 20))
        lbl(cl, t("country", L()), 10, "bold").pack(anchor="w", pady=(0, 6))
        cvar = tk.StringVar(value=(state["query"] or {}).get("country", "") or "—"); country_box(cl, cvar).pack(fill="x")
        tl = tk.Frame(row, bg=C["card"]); tl.grid(row=0, column=1, sticky="ew")
        th = tk.Frame(tl, bg=C["card"]); th.pack(fill="x")
        thr = tk.IntVar(value=int(cfg.get("threshold", 80)))
        lbl(th, "☰  " + t("threshold", L()), 10, "bold").pack(side="left")
        tv = lbl(th, f"{thr.get()}%", 10, "bold", C["primary"]); tv.pack(side="right")
        ttk.Scale(tl, from_=50, to=100, variable=thr, command=lambda v: (thr.set(int(float(v))), tv.config(text=f"{int(float(v))}%"))).pack(fill="x", pady=(8, 0))
        rng = tk.Frame(tl, bg=C["card"]); rng.pack(fill="x"); lbl(rng, "50%", 8, fg=C["mutedfg"]).pack(side="left"); lbl(rng, "100%", 8, fg=C["mutedfg"]).pack(side="right")

        lbl(fb, t("lists", L()), 10, "bold").pack(anchor="w", pady=(20, 8))
        lw = tk.Frame(fb, bg=C["card"]); lw.pack(fill="x")
        lvars = {}
        for i, s in enumerate(core.SOURCES):
            v = tk.BooleanVar(value=s["id"] in cfg.get("lists", [])); lvars[s["id"]] = v
            ttk.Checkbutton(lw, text=s["name"], variable=v, style="List.TCheckbutton").grid(row=i // 3, column=i % 3, sticky="ew", padx=(0, 10), pady=(0, 10))
        for c in range(3): lw.columnconfigure(c, weight=1, uniform="l")

        foot = tk.Frame(form, bg=C["muted"]); foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=C["muted"]); fi.pack(fill="x", padx=24, pady=14)

        def run(*_):
            name = name_var.get().strip(); lists = [k for k, v in lvars.items() if v.get()]
            if len(core.normalize(name)) < 2: messagebox.showinfo("ScanniX", t("minName", L())); return
            if not lists: messagebox.showinfo("ScanniX", t("sourceRequired", L())); return
            if core.total_records() == 0: messagebox.showinfo("ScanniX", "The sanctions lists are still downloading — please wait a moment."); return
            cfg["threshold"] = thr.get(); cfg["lists"] = lists; core.save_cfg(cfg)
            country = country_value(cvar)
            set_status(t("screeningNow", L())); root.update_idletasks()
            hits = core.screen_name(name, thr.get(), state["mode"], country, lists)
            snaps = core.snapshots()
            missing = [core.SRC[x]["name"] for x in lists if core.SRC[x]["url"] and not snaps.get(x, {}).get("count")]
            state.update(results=hits, query={"name": name, "country": country, "type": state["mode"], "threshold": thr.get(), "lists": lists, "missing": missing, "at": core.now()})
            core.save_history("single", name, country, state["mode"], thr.get(), lists, hits)
            set_status(f"{t('historySaved', L())}  ·  {len(hits)} {t('matches', L()).lower()}"); state["scroll"] = "results"; build()

        def clear():
            state.update(results=None, query=None); build()
        ttk.Button(fi, text="⌕  " + t("screen", L()) + "  →", style="Primary.TButton", command=run).pack(side="right")
        ttk.Button(fi, text=t("reset", L()), style="Ghost.TButton", command=clear).pack(side="right", padx=10)
        lbl(fi, "⛨  Screenings are saved to the history on this computer only.", 9, fg=C["mutedfg"], wraplength=360, justify="left").pack(side="left")
        ne.bind("<Return>", run)
        results_section(p)

    def results_section(p):
        sec = tk.Frame(p, bg=C["bg"]); sec.pack(fill="x", pady=(30, 0)); state["anchor"] = sec
        tk.Frame(sec, bg=C["border"], height=1).pack(fill="x", pady=(0, 16))
        hd = tk.Frame(sec, bg=C["bg"]); hd.pack(fill="x")
        lbl(hd, t("results", L()), 14, "bold").pack(side="left")
        q, hits = state["query"], state["results"]
        cols = ("score", "matched", "name", "source", "type", "countries", "programs", "id")
        heads = {"score": t("score", L()), "matched": t("matched", L()), "name": t("listName", L()), "source": "List", "type": t("entryType", L()),
                 "countries": "Countries", "programs": t("programs", L()), "id": t("reference", L())}

        def rows():
            return [[f"{m['score']}%", m["matched"], m["name"], core.SRC[m["source"]]["name"], core.type_label(m["type"], L()),
                     "; ".join(m.get("countries") or []) or "—", "; ".join(m.get("programs") or []) or "—", m["id"]] for m in hits or []]

        def exp(kind):
            if q is None: messagebox.showinfo("ScanniX", t("readyText", L())); return
            meta = [(t("searchFor", L()), q["name"]), (t("country", L()), q["country"] or t("noCountry", L())), (t("entryType", L()), t(q["type"] if q["type"] != "entity" else "entity", L())),
                    (t("threshold", L()), f"{q['threshold']}%"), (t("lists", L()), ", ".join(core.SRC[x]["name"] for x in q["lists"])), (t("screenedAt", L()), core.fmt_date(q["at"], L())),
                    (t("matches", L()), str(len(hits)))]
            notes = ([t("incomplete", L()) + " " + ", ".join(q["missing"])] if q["missing"] else []) + ([] if hits else [t("noMatchesText", L())])
            export(kind, t("results", L()), meta, [heads[c] for c in cols], rows(), notes, "scannix_screening", danger_col=0)
        export_bar(hd, exp).pack(side="right")
        tk.Frame(sec, bg=C["border"], height=1).pack(fill="x", pady=(14, 0))

        if q is None:
            e = tk.Frame(sec, bg=C["bg"]); e.pack(fill="x", pady=40)
            tk.Label(e, text="⌖", font=(F, 22), fg=C["primary"], bg=C["secondary"], width=3, pady=6).pack()
            lbl(e, t("ready", L()), 14, "bold").pack(pady=(14, 4)); lbl(e, t("readyText", L()), 11, fg=C["mutedfg"]).pack()
        else:
            box = card(sec); box.pack(fill="x", pady=(16, 0))
            sm = tk.Frame(box, bg=C["card"]); sm.pack(fill="x", padx=20, pady=14)
            if q.get("missing"):
                w = tk.Frame(box, bg=C["warnbg"]); w.pack(fill="x", padx=20, pady=(0, 10))
                lbl(w, "⚠  " + t("incomplete", L()) + "  " + ", ".join(q["missing"]), 9, "bold", C["warning"]).pack(anchor="w", padx=12, pady=8)
            cf = core.country_flag(q["name"], q["country"] or "")
            if cf:
                w = tk.Frame(box, bg=C["dangerbg"]); w.pack(fill="x", padx=20, pady=(0, 10))
                lbl(w, f"⚠  {cf['country']} is subject to sanctions programmes ({'/'.join(cf['authorities'])}): " + "; ".join(cf["programmes"]), 10, "bold", C["danger"], wraplength=900, justify="left").pack(anchor="w", padx=12, pady=(8, 2))
                lbl(w, "Whether a specific transaction is prohibited has not been assessed. Use Country / trade for the full review.", 9, fg=C["danger"]).pack(anchor="w", padx=12, pady=(0, 8))
            if hits:
                lbl(sm, f"⚠  {len(hits)} {t('matches', L()).lower()} — {t('legal', L()).lower()}", 12, "bold", C["danger"]).pack(side="left")
                lbl(sm, f"“{q['name']}”", 11, fg=C["mutedfg"]).pack(side="left", padx=12)
                tw, tr = make_tree(box, cols, heads, {"score": 110, "matched": 200, "name": 220, "source": 130, "type": 100, "countries": 150, "programs": 160, "id": 90}, min(12, max(4, len(hits))))
                tw.pack(fill="x", padx=20, pady=(0, 10))
                for i, (m, r) in enumerate(zip(hits, rows())):
                    tr.insert("", "end", iid=str(i), values=r, tags=(("odd",) if i % 2 else ()) + (("high",) if m["score"] >= 95 else ("mid",)))
                tr.bind("<Double-1>", lambda e: tr.selection() and review(hits[int(tr.selection()[0])]))
                lbl(box, "Double-click a row to review the full sanctions record.", 9, fg=C["mutedfg"]).pack(anchor="w", padx=20, pady=(0, 14))
            else:
                lbl(sm, "✓  " + t("noMatches", L()), 12, "bold", C["ok"]).pack(side="left")
                lbl(box, t("noMatchesText", L()), 10, fg=C["mutedfg"]).pack(anchor="w", padx=20, pady=(0, 16))
        nt = tk.Frame(p, bg=C["bg"]); nt.pack(fill="x", pady=(28, 0))
        tk.Frame(nt, bg=C["border"], height=1).pack(fill="x", pady=(0, 12))
        lbl(nt, "ⓘ  " + t("notice", L()), 9, fg=C["mutedfg"]).pack(anchor="w"); lbl(nt, "     " + t("languageNotice", L()), 9, fg=C["mutedfg"]).pack(anchor="w", pady=(4, 0))

    def review(m):
        w = tk.Toplevel(root); w.title("ScanniX — " + t("details", L())); w.configure(bg=C["card"]); w.geometry("640x620")
        tk.Frame(w, bg=C["primary"], height=4).pack(fill="x")
        b = tk.Frame(w, bg=C["card"]); b.pack(fill="both", expand=True, padx=26, pady=20)
        lbl(b, t("details", L()).upper(), 8, "bold", C["mutedfg"]).pack(anchor="w")
        lbl(b, m["name"], 16, "bold", wraplength=580, justify="left").pack(anchor="w", pady=(4, 6))
        tags = tk.Frame(b, bg=C["card"]); tags.pack(anchor="w", pady=(0, 12))
        badge(tags, f"{m['score']}% {t('score', L()).lower()}", C["danger"] if m["score"] >= 95 else C["warning"], C["dangerbg"] if m["score"] >= 95 else C["warnbg"]).pack(side="left")
        badge(tags, core.SRC[m["source"]]["name"], C["primary"], C["secondary"]).pack(side="left", padx=6)
        badge(tags, core.type_label(m["type"], L()), C["mutedfg"], C["muted"]).pack(side="left")
        txt = tk.Text(b, wrap="word", font=(F, 10), bg=C["card"], relief="flat", height=20); txt.pack(fill="both", expand=True)
        txt.tag_configure("h", font=(F, 9, "bold"), foreground=C["mutedfg"], spacing1=10)
        for k, v in ((t("matched", L()), m.get("matched")), (t("aliases", L()), "\n".join(m.get("aliases") or []) or t("unavailable", L())),
                     ("Countries", "; ".join(m.get("countries") or []) or t("noCountry", L())), (t("programs", L()), "; ".join(m.get("programs") or []) or "—"),
                     (t("dates", L()), "; ".join(m.get("dates") or []) or "—"), (t("reference", L()), m["id"]), (t("remarks", L()), m.get("remarks") or "—")):
            txt.insert("end", k.upper() + "\n", "h"); txt.insert("end", str(v) + "\n")
        txt.configure(state="disabled")
        bt = tk.Frame(b, bg=C["card"]); bt.pack(fill="x", pady=(10, 0))
        link(bt, t("officialReference", L()) + " ↗", core.SRC[m["source"]]["website"]).pack(side="left")
        ttk.Button(bt, text=t("close", L()), style="Primary.TButton", command=w.destroy).pack(side="right")

    def country_form(fb, form):
        lbl(fb, t("destinationCountry", L()), 10, "bold").pack(anchor="w", pady=(20, 6))
        prev = state["country_result"]
        cvar = tk.StringVar(value=prev["country"] if prev else t("chooseCountry", L()))
        cb = country_box(fb, cvar, 40); cb.pack(fill="x")
        lbl(fb, t("tradeJurisdictions", L()), 10, "bold").pack(anchor="w", pady=(20, 8))
        jw = tk.Frame(fb, bg=C["card"]); jw.pack(fill="x"); jv = {}
        for i, j in enumerate(core.JURISDICTIONS):
            v = tk.BooleanVar(value=True); jv[j["id"]] = v
            ttk.Checkbutton(jw, text=j["name"], variable=v, style="List.TCheckbutton").grid(row=i // 3, column=i % 3, sticky="ew", padx=(0, 10), pady=(0, 10))
        for c in range(3): jw.columnconfigure(c, weight=1, uniform="j")
        lbl(fb, t("countryScope", L()), 9, fg=C["mutedfg"], wraplength=700, justify="left").pack(anchor="w", pady=(6, 0))
        foot = tk.Frame(form, bg=C["muted"]); foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=C["muted"]); fi.pack(fill="x", padx=24, pady=14)

        def run():
            code = name_to_code.get(resolve_country(cvar.get()) or "")
            if not code: messagebox.showinfo("ScanniX", t("chooseCountry", L())); return
            js = [k for k, v in jv.items() if v.get()]
            if not js: messagebox.showinfo("ScanniX", t("tradeJurisdictions", L())); return
            res = core.review_country(code, js); res["at"] = core.now(); state["country_result"] = res
            conf = [r for r in res["rows"] if r["confirmed"]]
            core.save_history("country", res["country"], res["country"], "country", 0, js,
                              [{"name": p["name"], "matched": r["name"], "score": 100, "source": "ofac" if r["id"] == "us" else "un", "type": "country", "id": code, "programs": [p["name"]]} for r in conf for p in r["programmes"]])
            set_status(t("historySaved", L())); state["scroll"] = "results"; build()
        ttk.Button(fi, text="🌐  " + t("checkCountry", L()) + "  →", style="Primary.TButton", command=run).pack(side="right")
        lbl(fi, "ⓘ  " + t("countryCoverage", L()), 9, fg=C["mutedfg"], wraplength=480, justify="left").pack(side="left", fill="x", expand=True)
        country_results(fb.master.master.master)

    def country_results(p):
        res = state["country_result"]
        sec = tk.Frame(p, bg=C["bg"]); sec.pack(fill="x", pady=(30, 0)); state["anchor"] = sec
        tk.Frame(sec, bg=C["border"], height=1).pack(fill="x", pady=(0, 16))
        hd = tk.Frame(sec, bg=C["bg"]); hd.pack(fill="x"); lbl(hd, t("countryResults", L()), 14, "bold").pack(side="left")

        def exp(kind):
            if not res: messagebox.showinfo("ScanniX", t("chooseCountry", L())); return
            rows = []
            for r in res["rows"]:
                status = (t("ofacConfirmed", L()) if r["id"] == "us" else t("unConfirmed", L())) if r["confirmed"] else t("programmeNotVerified", L())
                rows.append([r["name"], status, "; ".join(p["name"] for p in r["programmes"]) or "—", "; ".join(p["scope"] for p in r["programmes"]) or r["scope"],
                             t("transactionReview", L()), r["url"]])
            export(kind, f"{res['country']} · {t('reviewRequired', L())}", [(t("countryReview", L()), res["country"]), (t("checkedOn", L()), core.PROGRAMME_CHECKED), (t("screenedAt", L()), core.fmt_date(res["at"], L()))],
                   ["Jurisdiction", "Programme status", t("programmeEvidence", L()), "Scope", "Transaction", t("officialReference", L())], rows,
                   [t("transactionCaveat", L()), t("countryCoverage", L()), t("countryEvidenceCoverage", L())], "scannix_country_review", danger_col=1)
        export_bar(hd, exp).pack(side="right")
        if not res:
            e = tk.Frame(sec, bg=C["bg"]); e.pack(fill="x", pady=40)
            tk.Label(e, text="🌐", font=(F, 20), fg=C["primary"], bg=C["secondary"], width=3, pady=6).pack()
            lbl(e, t("ready", L()), 14, "bold").pack(pady=(14, 4)); lbl(e, t("chooseCountry", L()), 11, fg=C["mutedfg"]).pack(); return
        box = card(sec); box.pack(fill="x", pady=(16, 0)); bi = tk.Frame(box, bg=C["card"]); bi.pack(fill="x", padx=22, pady=18)
        lbl(bi, f"{res['country']}  ·  {t('reviewRequired', L())}", 15, "bold").pack(anchor="w")
        lbl(bi, core.fmt_date(res["at"], L()), 9, fg=C["mutedfg"]).pack(anchor="w", pady=(2, 12))
        conf = [r for r in res["rows"] if r["confirmed"]]
        for r in conf:
            cb = tk.Frame(bi, bg=C["dangerbg"], highlightbackground="#f3b9bd", highlightthickness=1); cb.pack(fill="x", pady=(0, 10))
            tk.Frame(cb, bg=C["danger"], width=4).pack(side="left", fill="y")
            ci = tk.Frame(cb, bg=C["dangerbg"]); ci.pack(side="left", fill="x", expand=True, padx=14, pady=10)
            lbl(ci, "⛔  " + (t("ofacConfirmed", L()) if r["id"] == "us" else t("unConfirmed", L())), 12, "bold", C["danger"]).pack(anchor="w")
            lbl(ci, r["name"], 9, "bold", C["danger"]).pack(anchor="w", pady=(2, 4))
            for pg in r["programmes"]:
                pr = tk.Frame(ci, bg=C["dangerbg"]); pr.pack(fill="x", pady=2)
                link(pr, f"{pg['name']}  ·  {t('officialReference', L())} ↗", pg["url"], 10, C["danger"]).pack(anchor="w")
                lbl(pr, pg["scope"], 9, fg=C["fg"], wraplength=820, justify="left").pack(anchor="w")
                if pg.get("updated"): lbl(pr, f"{t('programmeUpdated', L())}: {pg['updated']}", 8, fg=C["mutedfg"]).pack(anchor="w")
            lbl(ci, f"{t('checkedOn', L())}: {r['checked']}", 8, fg=C["mutedfg"]).pack(anchor="w", pady=(4, 0))
        tr = tk.Frame(bi, bg=C["warnbg"]); tr.pack(fill="x", pady=(0, 10))
        lbl(tr, "⚠  " + t("transactionReview", L()), 11, "bold", C["warning"]).pack(anchor="w", padx=14, pady=(10, 2))
        lbl(tr, t("transactionCaveat", L()), 9, wraplength=860, justify="left").pack(anchor="w", padx=14, pady=(0, 10))
        if not conf:
            lbl(bi, "ⓘ  " + t("countryUnverified", L()), 10, "bold", C["mutedfg"]).pack(anchor="w", pady=(0, 10))
        for r in res["rows"]:
            jr = tk.Frame(bi, bg=C["card"], highlightbackground=C["border"], highlightthickness=1); jr.pack(fill="x", pady=4)
            ji = tk.Frame(jr, bg=C["card"]); ji.pack(fill="x", padx=14, pady=10)
            top = tk.Frame(ji, bg=C["card"]); top.pack(fill="x")
            lbl(top, r["name"], 11, "bold").pack(side="left")
            if r["confirmed"]: badge(top, t("ofacConfirmed", L()) if r["id"] == "us" else t("unConfirmed", L()), C["danger"], C["dangerbg"]).pack(side="right")
            else: badge(top, t("programmeNotVerified", L()), C["mutedfg"], C["muted"]).pack(side="right")
            lbl(ji, r["scope"], 9, fg=C["mutedfg"], wraplength=860, justify="left").pack(anchor="w", pady=(4, 2))
            link(ji, t("officialReference", L()) + " ↗", r["url"]).pack(anchor="w")
        lbl(bi, t("countryCoverage", L()), 9, fg=C["mutedfg"], wraplength=880, justify="left").pack(anchor="w", pady=(12, 0))
        lbl(bi, t("countryEvidenceCoverage", L()), 9, fg=C["mutedfg"], wraplength=880, justify="left").pack(anchor="w", pady=(4, 0))

    # ---------------------------------------------------------- page: batch
    def page_batch(p):
        heading(p, t("batch", L()), t("batchSubtitle", L()), "AML INTELLIGENCE")
        up = card(p, top=C["primary"]); up.pack(fill="x"); ui = tk.Frame(up, bg=C["card"]); ui.pack(fill="x", padx=24, pady=20)
        lbl(ui, "⭱  " + t("uploadFile", L()), 13, "bold").pack(anchor="w")
        drop = tk.Frame(ui, bg=C["muted"], highlightbackground=C["input"], highlightthickness=1, cursor="hand2"); drop.pack(fill="x", pady=(12, 6))
        fname = state["batch_file"]
        d1 = lbl(drop, ("📄  " + os.path.basename(fname)) if fname else ("⭱  " + t("dropFile", L()).replace("Drop the file here or click", "Click")), 11, "bold", C["primary"]); d1.pack(pady=(18, 2))
        d2 = lbl(drop, (f"{len(state['batch_rows']):,} {t('rowsFound', L())}") if fname else t("uploadLimits", L()), 9, fg=C["mutedfg"]); d2.pack(pady=(0, 18))

        def choose(*_):
            pth = filedialog.askopenfilename(filetypes=[("Spreadsheets", "*.csv *.xlsx *.txt"), ("All files", "*.*")])
            if not pth: return
            try: header, rows = core.read_sheet(pth)
            except Exception as e: messagebox.showerror("ScanniX", f"{t('fileError', L())}\n\n{e}"); return
            state.update(batch_file=pth, batch_header=header, batch_rows=rows, batch_results=None); build()
        for w in (drop, d1, d2): w.bind("<Button-1>", choose)

        if fname:
            hdr = state["batch_header"]; none = t("noColumn", L())
            g = tk.Frame(ui, bg=C["card"]); g.pack(fill="x", pady=(12, 0))
            for c in range(4): g.columnconfigure(c, weight=1)
            ni = core.guess_column(hdr, ["name", "entity", "company", "customer", "party", "vessel", "beneficiary"]) or 0
            ci = core.guess_column(hdr, ["country", "nationality", "citizenship", "jurisdiction"])
            nv = tk.StringVar(value=hdr[ni]); cv = tk.StringVar(value=hdr[ci] if ci is not None else none)
            tv = tk.StringVar(value=t("all", L())); thr = tk.IntVar(value=int(cfg.get("threshold", 80)))
            for col, (label, widget) in enumerate([(t("nameColumn", L()), lambda f: ttk.Combobox(f, textvariable=nv, values=hdr, state="readonly")),
                                                   (t("countryColumn", L()), lambda f: ttk.Combobox(f, textvariable=cv, values=[none] + hdr, state="readonly")),
                                                   (t("entryType", L()), lambda f: ttk.Combobox(f, textvariable=tv, values=[t(k, L()) for k in core.ENTITY_TYPES], state="readonly"))]):
                f = tk.Frame(g, bg=C["card"]); f.grid(row=0, column=col, sticky="ew", padx=(0, 16))
                lbl(f, label, 10, "bold").pack(anchor="w", pady=(0, 6)); widget(f).pack(fill="x")
            f = tk.Frame(g, bg=C["card"]); f.grid(row=0, column=3, sticky="ew")
            th = tk.Frame(f, bg=C["card"]); th.pack(fill="x"); lbl(th, t("threshold", L()), 10, "bold").pack(side="left")
            tl = lbl(th, f"{thr.get()}%", 10, "bold", C["primary"]); tl.pack(side="right")
            ttk.Scale(f, from_=50, to=100, variable=thr, command=lambda v: (thr.set(int(float(v))), tl.config(text=f"{int(float(v))}%"))).pack(fill="x", pady=(10, 0))
            lbl(ui, t("lists", L()), 10, "bold").pack(anchor="w", pady=(18, 8))
            lw = tk.Frame(ui, bg=C["card"]); lw.pack(fill="x"); lvars = {}
            for i, s in enumerate(core.SOURCES):
                v = tk.BooleanVar(value=s["id"] in cfg.get("lists", [])); lvars[s["id"]] = v
                ttk.Checkbutton(lw, text=s["name"], variable=v, style="List.TCheckbutton").grid(row=0, column=i, sticky="ew", padx=(0, 8))
            for c in range(6): lw.columnconfigure(c, weight=1)
            usable = len([r for r in state["batch_rows"] if r and any(r)])
            lbl(ui, f"{min(usable, core.MAX_BATCH_ROWS):,} {t('rowsFound', L())}" + (f"  ·  {t('batchLimit', L())}" if usable > core.MAX_BATCH_ROWS else ""), 9, fg=C["mutedfg"]).pack(anchor="w", pady=(14, 0))
            foot = tk.Frame(up, bg=C["muted"]); foot.pack(fill="x"); fi = tk.Frame(foot, bg=C["muted"]); fi.pack(fill="x", padx=24, pady=14)
            pb = ttk.Progressbar(fi, length=260, mode="determinate"); pb.pack(side="left")

            def run():
                lists = [k for k, v in lvars.items() if v.get()]
                if not lists: messagebox.showinfo("ScanniX", t("sourceRequired", L())); return
                n_i = hdr.index(nv.get()); c_i = hdr.index(cv.get()) if cv.get() in hdr else None
                kind = core.ENTITY_TYPES[[t(k, L()) for k in core.ENTITY_TYPES].index(tv.get())]
                def job():
                    res = core.screen_batch(state["batch_rows"], n_i, c_i, thr.get(), kind, lists,
                                            lambda i, n: root.after(0, lambda: (pb.configure(maximum=n, value=i), set_status(f"{t('screeningNow', L())} {i}/{n}"))))
                    for r in res:
                        core.save_history("batch", r["name"], r["country"], kind, thr.get(), lists, r["matches"])
                    state["batch_results"] = {"rows": res, "threshold": thr.get(), "lists": lists, "at": core.now(), "file": os.path.basename(fname)}
                    root.after(0, lambda: (set_status(f"{t('screenedRows', L())}: {len(res)}  ·  {t('historySaved', L())}"), state.update(scroll="results"), build()))
                threading.Thread(target=job, daemon=True).start()
            ttk.Button(fi, text="⌕  " + t("runBatch", L()) + "  →", style="Primary.TButton", command=run).pack(side="right")
            ttk.Button(fi, text=t("clearFile", L()), style="Ghost.TButton", command=lambda: (state.update(batch_file=None, batch_rows=[], batch_header=[], batch_results=None), build())).pack(side="right", padx=10)

        br = state["batch_results"]
        if br:
            sec = tk.Frame(p, bg=C["bg"]); sec.pack(fill="x", pady=(28, 0)); state["anchor"] = sec
            hd = tk.Frame(sec, bg=C["bg"]); hd.pack(fill="x"); lbl(hd, t("batchResults", L()), 14, "bold").pack(side="left")
            hitrows = sum(1 for r in br["rows"] if r["matches"] or r.get("country_flag"))
            lbl(hd, f"   {len(br['rows'])} {t('screenedRows', L()).lower()}  ·  {hitrows} {t('matches', L()).lower()}", 10, "bold", C["danger"] if hitrows else C["ok"]).pack(side="left")
            cols = ("row", "name", "country", "count", "top", "score", "source", "cflag")
            heads = {"row": "#", "name": t("searchFor", L()), "country": re.sub(r"\s*[(（].*", "", t("country", L())), "count": t("matchCount", L()), "top": t("topMatch", L()),
                     "score": "Score", "source": "List", "cflag": "Sanctioned country"}
            def flag_text(f): return f"{f['country']} ({'/'.join(f['authorities'])})" if f else "—"
            # Show the exact name or alias that matched, so the score is understandable.
            rows = [[r["row"], r["name"], r["country"] or "—", len(r["matches"]), r["matches"][0].get("matched") or r["matches"][0]["name"] if r["matches"] else "—",
                     f"{r['matches'][0]['score']}%" if r["matches"] else "—", core.SRC[r["matches"][0]["source"]]["name"] if r["matches"] else "—",
                     flag_text(r.get("country_flag"))] for r in br["rows"]]
            def exp(kind):
                export(kind, t("batchResults", L()), [("File", br["file"]), (t("screenedRows", L()), len(rows)), (t("threshold", L()), f"{br['threshold']}%"),
                       (t("lists", L()), ", ".join(core.SRC[x]["name"] for x in br["lists"])), (t("screenedAt", L()), core.fmt_date(br["at"], L()))],
                       [heads[c] for c in cols], rows, [], "scannix_batch", danger_col=(3, 7))
            export_bar(hd, exp).pack(side="right")
            box = card(sec); box.pack(fill="x", pady=(14, 0))
            tw, tr = make_tree(box, cols, heads, {"row": 36, "name": 160, "country": 100, "count": 85, "top": 210, "score": 105, "source": 105, "cflag": 220}, min(16, max(5, len(rows))))
            tw.pack(fill="x", padx=16, pady=16)
            for i, (r, v) in enumerate(zip(br["rows"], rows)):
                tr.insert("", "end", iid=str(i), values=v, tags=(("odd",) if i % 2 else ()) + (("high",) if r["matches"] or r.get("country_flag") else ("clear",)))
            def open_row(e):
                if not tr.selection(): return
                r = br["rows"][int(tr.selection()[0])]
                if r["matches"]: review(r["matches"][0])
                elif r.get("country_flag"):
                    f = r["country_flag"]
                    messagebox.showwarning("ScanniX", f"{f['country']} is subject to sanctions programmes:\n\n" + "\n".join("• " + n for n in f["programmes"]) +
                                           f"\n\nOfficial source: {f['url']}\n\nWhether a specific transaction is prohibited has not been assessed. Use Country / trade review and confirm with a compliance professional.")
            tr.bind("<Double-1>", open_row)
            lbl(box, "Double-click a red row to review the top sanctions record or the country programme details.", 9, fg=C["mutedfg"]).pack(anchor="w", padx=16, pady=(0, 14))

    # ---------------------------------------------------------- page: history
    def page_history(p):
        heading(p, t("history", L()), t("historySubtitle", L()), "AML INTELLIGENCE")
        f = card(p, top=C["primary"]); f.pack(fill="x"); fi = tk.Frame(f, bg=C["card"]); fi.pack(fill="x", padx=22, pady=18)
        hv = state.setdefault("hist_filters", {"name": "", "country": "", "from": "", "to": ""})
        vars_ = {k: tk.StringVar(value=v) for k, v in hv.items()}
        for i, (k, key, hint) in enumerate((("name", "filterName", ""), ("country", "filterCountry", ""), ("from", "from", "YYYY-MM-DD"), ("to", "to", "YYYY-MM-DD"))):
            c = tk.Frame(fi, bg=C["card"]); c.grid(row=0, column=i, sticky="ew", padx=(0, 14))
            lbl(c, t(key, L()), 10, "bold").pack(anchor="w", pady=(0, 6))
            e = ttk.Entry(c, textvariable=vars_[k], font=(F, 10)); e.pack(fill="x"); e.bind("<Return>", lambda ev: apply())
            if hint: lbl(c, hint, 8, fg=C["mutedfg"]).pack(anchor="w", pady=(3, 0))
            fi.columnconfigure(i, weight=1)
        def apply():
            state["hist_filters"] = {k: v.get().strip() for k, v in vars_.items()}; build()
        def reset():
            state["hist_filters"] = {"name": "", "country": "", "from": "", "to": ""}; build()
        bt = tk.Frame(fi, bg=C["card"]); bt.grid(row=0, column=4, sticky="se")
        ttk.Button(bt, text=t("reset", L()), style="Ghost.TButton", command=reset).pack(side="left")
        ttk.Button(bt, text="⌕  Apply", style="Primary.TButton", command=apply).pack(side="left", padx=(6, 0))
        hv = state["hist_filters"]
        items = core.history(hv["name"], hv["country"], hv["from"], hv["to"])
        sec = tk.Frame(p, bg=C["bg"]); sec.pack(fill="x", pady=(24, 0))
        hd = tk.Frame(sec, bg=C["bg"]); hd.pack(fill="x")
        lbl(hd, f"{len(items):,} " + t("history", L()).lower(), 13, "bold").pack(side="left")
        cols = ("ts", "mode", "query", "country", "type", "count", "top", "cflag", "lists")
        heads = {"ts": t("screenedOn", L()), "mode": t("mode", L()), "query": t("searchFor", L()), "country": re.sub(r"\s*[(（].*", "", t("country", L())), "type": t("entryType", L()),
                 "count": t("matchCount", L()), "top": "Score", "cflag": "Sanctioned country", "lists": t("lists", L())}
        def flag_text(f): return f"{f['country']} ({'/'.join(f['authorities'])})" if f else "—"
        modes = {"single": t("single", L()), "batch": t("batchMode", L()), "country": t("countryScreen", L())}
        rows = [[core.fmt_date(h["ts"], L()), modes.get(h["mode"], h["mode"]), h["query"], h["country"] or "—",
                 t("countryScreen", L()) if h["type"] == "country" else t(h["type"] or "all", L()), h["match_count"],
                 f"{h['top_score']:.0f}%" if h["match_count"] else "—", flag_text(h["country_flag"]), ", ".join(core.SRC[x]["name"] if x in core.SRC else x for x in (h["lists"] or "").split(",") if x)] for h in items]
        def exp(kind):
            export(kind, t("history", L()), [(t("filterName", L()), hv["name"] or "—"), (t("filterCountry", L()), hv["country"] or "—"),
                   (t("from", L()), hv["from"] or "—"), (t("to", L()), hv["to"] or "—"), ("Total", len(rows))], [heads[c] for c in cols], rows, [], "scannix_history", danger_col=(5, 7))
        export_bar(hd, exp).pack(side="right")
        box = card(sec); box.pack(fill="x", pady=(14, 0))
        if not items:
            lbl(box, t("noHistory", L()), 11, fg=C["mutedfg"]).pack(pady=40); return
        tw, tr = make_tree(box, cols, heads, {"ts": 165, "mode": 65, "query": 150, "country": 90, "type": 135, "count": 85, "top": 70, "cflag": 220, "lists": 200}, min(18, max(6, len(rows))))
        tw.pack(fill="x", padx=16, pady=16)
        for i, (h, v) in enumerate(zip(items, rows)):
            tr.insert("", "end", iid=str(h["id"]), values=v, tags=(("odd",) if i % 2 else ()) + (("high",) if h["match_count"] or h["country_flag"] else ("clear",)))
        def details(e):
            if not tr.selection(): return
            h = next(x for x in items if str(x["id"]) == tr.selection()[0])
            msg = "\n".join(f"• {m['name']} — {m.get('score')}% · {core.SRC.get(m.get('source'), {}).get('name', '')}" for m in h["results"][:20]) or t("noMatches", L())
            f = h["country_flag"]
            if f and h["mode"] != "country":
                msg += (f"\n\nSanctioned country: {f['country']}\n" + "\n".join("• " + n for n in f["programmes"]) +
                        f"\nOfficial source: {f['url']}\nWhether a specific transaction is prohibited has not been assessed.")
            messagebox.showinfo("ScanniX", f"{h['query']}\n{core.fmt_date(h['ts'], L())}\n\n{msg}")
        tr.bind("<Double-1>", details)
        def delete():
            if tr.selection() and messagebox.askyesno("ScanniX", t("deleteEntry", L()) + "?"):
                for iid in tr.selection(): core.delete_history(int(iid))
                build()
        bb = tk.Frame(box, bg=C["card"]); bb.pack(fill="x", padx=16, pady=(0, 14))
        lbl(bb, "Double-click a row to see its matches.", 9, fg=C["mutedfg"]).pack(side="left")
        ttk.Button(bb, text="🗑  " + t("deleteEntry", L()), style="Secondary.TButton", command=delete).pack(side="right")

    # ---------------------------------------------------------- page: manage lists
    def page_lists(p):
        heading(p, t("manage", L()), t("manageSubtitle", L()), "AML INTELLIGENCE")
        g = tk.Frame(p, bg=C["bg"]); g.pack(fill="x"); g.columnconfigure(0, weight=1); g.columnconfigure(1, weight=1)
        a = card(g, top=C["primary"]); a.grid(row=0, column=0, sticky="nsew", padx=(0, 20)); ai = tk.Frame(a, bg=C["card"]); ai.pack(fill="both", expand=True, padx=22, pady=18)
        lbl(ai, "☰+  " + t("addEntries", L()), 13, "bold").pack(anchor="w")
        lbl(ai, t("addHelp", L()), 9, fg=C["mutedfg"], wraplength=480, justify="left").pack(anchor="w", pady=(4, 12))
        r = tk.Frame(ai, bg=C["card"]); r.pack(fill="x"); r.columnconfigure(0, weight=1); r.columnconfigure(1, weight=1)
        src_names = [s["name"] for s in core.SOURCES]; tvar = tk.StringVar(value=core.SRC["uae"]["name"])
        type_names = [t(k, L()) for k in core.ENTITY_TYPES[1:]]; evar = tk.StringVar(value=type_names[0])
        for col, (label, var, vals) in enumerate(((t("targetList", L()), tvar, src_names), (t("entryType", L()), evar, type_names))):
            f = tk.Frame(r, bg=C["card"]); f.grid(row=0, column=col, sticky="ew", padx=(0, 12) if col == 0 else 0)
            lbl(f, label, 10, "bold").pack(anchor="w", pady=(0, 6)); ttk.Combobox(f, textvariable=var, values=vals, state="readonly").pack(fill="x")
        txt = tk.Text(ai, height=7, font=(F, 10), relief="solid", bd=1, highlightthickness=0, wrap="word"); txt.pack(fill="x", pady=(14, 0))
        def target():
            return core.SOURCES[src_names.index(tvar.get())]["id"], core.ENTITY_TYPES[1:][type_names.index(evar.get())]
        def add():
            entries = core.parse_name_lines(txt.get("1.0", "end"))
            if not entries: messagebox.showinfo("ScanniX", t("noRows", L())); return
            sid, kind = target(); n = core.add_entries(sid, entries, kind)
            set_status(f"{n} {t('added', L())} → {core.SRC[sid]['name']}"); build()
        def upload():
            pth = filedialog.askopenfilename(filetypes=[("Spreadsheets", "*.csv *.xlsx *.txt")])
            if not pth: return
            try: header, rows = core.read_sheet(pth)
            except Exception as e: messagebox.showerror("ScanniX", f"{t('fileError', L())}\n\n{e}"); return
            ni = core.guess_column(header, ["name", "entity", "company"]) or 0; ci = core.guess_column(header, ["country", "nationality"])
            entries = [{"name": r[ni], "country": r[ci] if ci is not None and ci < len(r) else ""} for r in rows if ni < len(r) and r[ni].strip()]
            sid, kind = target(); n = core.add_entries(sid, entries, kind)
            set_status(f"{n} {t('added', L())} → {core.SRC[sid]['name']}"); messagebox.showinfo("ScanniX", f"{n} {t('added', L())} → {core.SRC[sid]['name']}"); build()
        bt = tk.Frame(ai, bg=C["card"]); bt.pack(fill="x", pady=(12, 0))
        ttk.Button(bt, text="+  " + t("addNames", L()), style="Primary.TButton", command=add).pack(side="left")
        ttk.Button(bt, text="⭱  " + t("uploadList", L()), style="Secondary.TButton", command=upload).pack(side="left", padx=10)
        lbl(ai, t("uploadLimits", L()), 8, fg=C["mutedfg"]).pack(anchor="w", pady=(8, 0))
        lbl(ai, "Names you add are stored on this computer only and never change the official lists.", 8, fg=C["mutedfg"]).pack(anchor="w")

        d = card(g, top=C["highlight"]); d.grid(row=0, column=1, sticky="nsew"); di = tk.Frame(d, bg=C["card"]); di.pack(fill="both", expand=True, padx=22, pady=18)
        lbl(di, "⭳  " + t("downloadList", L()), 13, "bold").pack(anchor="w")
        lbl(di, t("downloadListHelp", L()), 9, fg=C["mutedfg"], wraplength=300, justify="left").pack(anchor="w", pady=(4, 12))
        dv = state.setdefault("dl", {"list": t("filterAll", L()), "name": "", "country": "—"})
        lvar = tk.StringVar(value=dv["list"]); nvar = tk.StringVar(value=dv["name"]); cvar = tk.StringVar(value=dv["country"])
        lbl(di, t("lists", L()), 10, "bold").pack(anchor="w", pady=(0, 6))
        ttk.Combobox(di, textvariable=lvar, values=[t("filterAll", L())] + src_names, state="readonly").pack(fill="x")
        rr = tk.Frame(di, bg=C["card"]); rr.pack(fill="x", pady=(12, 0)); rr.columnconfigure(0, weight=1); rr.columnconfigure(1, weight=1)
        f1 = tk.Frame(rr, bg=C["card"]); f1.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        lbl(f1, t("filterName", L()), 10, "bold").pack(anchor="w", pady=(0, 6)); ttk.Entry(f1, textvariable=nvar).pack(fill="x")
        f2 = tk.Frame(rr, bg=C["card"]); f2.grid(row=0, column=1, sticky="ew")
        lbl(f2, t("filterCountry", L()), 10, "bold").pack(anchor="w", pady=(0, 6)); country_box(f2, cvar, 20).pack(fill="x")
        found = state.get("dl_found")
        def find():
            state["dl"] = {"list": lvar.get(), "name": nvar.get().strip(), "country": cvar.get()}
            sid = "all" if lvar.get() == t("filterAll", L()) else core.SOURCES[src_names.index(lvar.get())]["id"]
            state["dl_found"] = core.list_records(sid, nvar.get().strip(), country_value(cvar)); build()
        bt2 = tk.Frame(di, bg=C["card"]); bt2.pack(fill="x", pady=(14, 0))
        ttk.Button(bt2, text="⌕  " + t("prepare", L()), style="Primary.TButton", command=find).pack(side="left")
        if found is not None:
            lbl(bt2, f"{len(found):,} {t('recordsFound', L())}", 10, "bold", C["primary"]).pack(side="left", padx=12)
            def exp(kind):
                rows = [[r["name"], "; ".join(r.get("aliases") or [])[:300], core.SRC[r["source"]]["name"], core.type_label(r["type"], L()),
                         "; ".join(r.get("countries") or []), "; ".join(r.get("programs") or []), "; ".join(r.get("dates") or []), r["id"]] for r in found]
                export(kind, t("downloadList", L()), [(t("lists", L()), state["dl"]["list"]), (t("filterName", L()), state["dl"]["name"] or "—"),
                       (t("filterCountry", L()), country_value(tk.StringVar(value=state["dl"]["country"])) or "—"), (t("recordsFound", L()), f"{len(found):,}")],
                       [t("listName", L()), t("aliases", L()), "List", t("entryType", L()), "Countries", t("programs", L()), t("dates", L()), t("reference", L())], rows, [], "scannix_sanctions_list")
            export_bar(di, exp, C["card"]).pack(anchor="w", pady=(10, 0))

        sec = tk.Frame(p, bg=C["bg"]); sec.pack(fill="x", pady=(26, 0))
        entries = core.custom_entries()
        lbl(sec, f"{t('addedEntries', L())}  ({len(entries)})", 13, "bold").pack(anchor="w")
        box = card(sec); box.pack(fill="x", pady=(12, 0))
        if not entries: lbl(box, t("empty", L()), 10, fg=C["mutedfg"]).pack(pady=30); return
        cols = ("name", "source", "type", "country", "added")
        heads = {"name": t("listName", L()), "source": t("targetList", L()), "type": t("entryType", L()), "country": t("country", L()), "added": t("addedOn", L())}
        tw, tr = make_tree(box, cols, heads, {"name": 300, "source": 200, "type": 120, "country": 140, "added": 160}, min(12, max(4, len(entries))))
        tw.pack(fill="x", padx=16, pady=16)
        for i, e in enumerate(entries):
            tr.insert("", "end", iid=str(e["id"]), values=[e["name"], core.SRC[e["source"]]["name"], core.type_label(e["type"], L()), "; ".join(e["countries"]) or "—", core.fmt_date(e["created_at"], L())], tags=("odd",) if i % 2 else ())
        def remove():
            if tr.selection() and messagebox.askyesno("ScanniX", t("remove", L()) + "?"):
                for iid in tr.selection(): core.delete_entry(int(iid))
                build()
        bb = tk.Frame(box, bg=C["card"]); bb.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bb, text="🗑  " + t("remove", L()), style="Secondary.TButton", command=remove).pack(side="right")

    # ---------------------------------------------------------- page: data sources
    def page_sources(p):
        heading(p, t("sourceTitle", L()), t("sourceSubtitle", L()), "AML INTELLIGENCE", lambda h: update_button(h).pack(side="right", anchor="n"))
        stats_row(p)
        g = tk.Frame(p, bg=C["bg"]); g.pack(fill="x"); snaps = core.snapshots()
        for i, s in enumerate(core.SOURCES):
            sn = snaps.get(s["id"], {}); count = sn.get("count") or 0; custom = sn.get("custom") or 0
            c = card(g, top=C["primary"] if i % 2 == 0 else C["highlight"]); c.grid(row=i // 3, column=i % 3, sticky="nsew", padx=(0, 16), pady=(0, 16))
            ci = tk.Frame(c, bg=C["card"]); ci.pack(fill="both", expand=True, padx=20, pady=16)
            tp = tk.Frame(ci, bg=C["card"]); tp.pack(fill="x")
            tk.Label(tp, text=s["code"], font=(F, 9, "bold"), fg=C["primary"], bg=C["secondary"], width=4, pady=5).pack(side="left")
            nm = tk.Frame(tp, bg=C["card"]); nm.pack(side="left", padx=10)
            lbl(nm, s["name"], 12, "bold").pack(anchor="w"); lbl(nm, s["country"], 9, fg=C["mutedfg"]).pack(anchor="w")
            lbl(ci, s["authority"], 9, fg=C["mutedfg"], wraplength=300, justify="left").pack(anchor="w", pady=(10, 8))
            lbl(ci, f"{count + custom:,}", 24, "bold", C["primary"]).pack(anchor="w")
            lbl(ci, t("count", L()) + (f"  ·  {custom} added on this computer" if custom else ""), 9, fg=C["mutedfg"]).pack(anchor="w")
            info = tk.Frame(ci, bg=C["card"]); info.pack(fill="x", pady=(10, 0))
            if s["url"]:
                for k, v in ((t("updated", L()), core.fmt_date(sn.get("updated_at"), L())), (t("sourceDate", L()), (sn.get("source_date") or t("unavailable", L()))[:10])):
                    rr = tk.Frame(info, bg=C["card"]); rr.pack(fill="x", pady=1)
                    lbl(rr, k, 9, fg=C["mutedfg"]).pack(side="left"); lbl(rr, v, 9, "bold").pack(side="right")
                u = sn.get("updated_at")
                fresh = u and (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(u)).total_seconds() < 86400
                if sn.get("error"): b = badge(ci, "⚠ " + t("partial", L()), C["danger"], C["dangerbg"])
                elif fresh: b = badge(ci, "✓ " + t("current", L()), C["ok"], C["secondary"])
                elif u: b = badge(ci, "⚠ " + t("stale", L()), C["warning"], C["warnbg"])
                else: b = badge(ci, t("never", L()), C["mutedfg"], C["muted"])
                b.pack(anchor="w", pady=(10, 0))
                if sn.get("error"): lbl(ci, sn["error"][:180], 8, fg=C["danger"], wraplength=300, justify="left").pack(anchor="w", pady=(4, 0))
            else:
                badge(ci, t("manualList", L()), C["highlight"], C["hlbg"]).pack(anchor="w", pady=(10, 0))
                lbl(ci, "Add names in Manage lists. No official machine-readable download is published.", 8, fg=C["mutedfg"], wraplength=300, justify="left").pack(anchor="w", pady=(4, 0))
            bt = tk.Frame(ci, bg=C["card"]); bt.pack(fill="x", pady=(12, 0))
            link(bt, t("officialReference", L()) + " ↗", s["website"]).pack(side="left")
            if s["url"]: update_button(bt, s["id"]).pack(side="right")
            else: ttk.Button(bt, text=t("manage", L()), style="Secondary.TButton", command=lambda: go("lists")).pack(side="right")
        for c in range(3): g.columnconfigure(c, weight=1, uniform="s")
        lbl(p, "ⓘ  " + t("sourcePolicy", L()).replace("scheduled for 02:00 UTC", "made while ScanniX is open"), 9, fg=C["mutedfg"]).pack(anchor="w", pady=(8, 0))
        lbl(p, f"Data folder: {core.APP_DIR}", 8, fg=C["mutedfg"]).pack(anchor="w", pady=(4, 0))

    # ---------------------------------------------------------- settings
    def settings():
        w = tk.Toplevel(root); w.title("ScanniX — Settings"); w.configure(bg=C["card"])
        tk.Frame(w, bg=C["primary"], height=4).pack(fill="x")
        inner = tk.Frame(w, bg=C["card"]); inner.pack(padx=24, pady=18)
        lbl(inner, "Settings", 14, "bold").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        auto = tk.BooleanVar(value=cfg.get("auto_update_daily", True))
        ttk.Checkbutton(inner, text="Update the official lists automatically every day", variable=auto, style="Card.TCheckbutton").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        lbl(inner, "Optional live API checks (results open in a window)", 10, "bold").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 4))
        keys = [("yente_url", "OpenSanctions / yente URL"), ("opensanctions_api_key", "OpenSanctions API key"), ("sanctions_io_key", "sanctions.io API key"),
                ("apify_token", "Apify token"), ("apify_actor", "Apify actor")]
        fields = {}
        for i, (k, label) in enumerate(keys):
            lbl(inner, label, 9, fg=C["mutedfg"]).grid(row=3 + i, column=0, sticky="w", padx=(0, 10), pady=3)
            e = ttk.Entry(inner, width=48, show="*" if "key" in k or "token" in k else ""); e.insert(0, str(cfg.get(k, ""))); e.grid(row=3 + i, column=1, pady=3); fields[k] = e
        lbl(inner, "Live check name", 9, fg=C["mutedfg"]).grid(row=9, column=0, sticky="w", pady=(10, 3))
        lv = ttk.Entry(inner, width=48); lv.grid(row=9, column=1, pady=(10, 3))
        def save():
            for k, e in fields.items(): cfg[k] = e.get().strip()
            cfg["auto_update_daily"] = auto.get(); core.save_cfg(cfg); w.destroy(); set_status("Settings saved.")
        def live():
            for k, e in fields.items(): cfg[k] = e.get().strip()
            nm = lv.get().strip()
            if not nm: return
            def job():
                res = live_check(nm, cfg)
                def show():
                    tw = tk.Toplevel(root); tw.title("ScanniX — Live API"); tx = tk.Text(tw, width=100, height=34, font=("Consolas", 10)); tx.pack(fill="both", expand=True)
                    tx.insert("1.0", json.dumps(res, indent=2, ensure_ascii=False))
                root.after(0, show)
            threading.Thread(target=job, daemon=True).start()
        bt = tk.Frame(inner, bg=C["card"]); bt.grid(row=20, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(bt, text="Run live check", style="Secondary.TButton", command=live).pack(side="left", padx=8)
        ttk.Button(bt, text="Save", style="Primary.TButton", command=save).pack(side="left")

    # ---------------------------------------------------------- background updates
    def ui_progress(msg): root.after(0, lambda: set_status(msg))

    def ui_done(errors):
        def after():
            if state["page"] in ("screen", "sources"): build()
            if errors:
                messagebox.showwarning("ScanniX — update problem", "Some lists could not be downloaded:\n\n" + "\n\n".join(e[:300] for e in errors) +
                                       "\n\nThe previous successful list is kept. If you are on a company network, the firewall may block these sites.\n"
                                       f"Details: {core.LOG_PATH}")
        root.after(0, after)

    build()
    set_status("Ready" if core.total_records() else "First start: downloading the official sanctions lists (about 1–2 minutes) ...")
    threading.Thread(target=core.scheduler, args=(lambda: cfg, ui_progress, ui_done), daemon=True).start()
    threading.Thread(target=core.INDEX.get, daemon=True).start()  # warm up matching
    root.mainloop()


if __name__ == "__main__":
    if "--server" in sys.argv: run_server()
    elif "--update" in sys.argv:
        errs = core.update_all()
        print("Done." if not errs else "Problems:\n" + "\n".join(errs))
    else: run_gui()
