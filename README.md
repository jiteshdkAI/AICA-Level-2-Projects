# ScanniX Desktop (v6)

The desktop version of the ScanniX web app. It has the same screens:
Sanctions screening, Batch screening, Screening history, Manage lists and Data sources,
plus the 7 languages, the disclaimer, and Print / HTML / CSV reports.

> ScanniX is a scanning support tool only. Results are not legal advice or a final
> sanctions determination. Every result needs review by a qualified compliance professional.

## Windows quick start
1. **Extract the whole zip first**: right-click the zip, choose **Extract All...**, pick a folder.
   Use a **new folder**. Do not reuse the folder from an older version.
2. Open the extracted folder and double-click `run_aml_screener.bat`.
3. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
4. The first run installs its components (1–2 minutes), then downloads the official lists
   (OFAC, UN, UK, EU, about 80 MB). Progress shows at the bottom of the window.
5. Keep the black console window open while you use the app.

You need Python from https://www.python.org/downloads/. Tick **Add Python to PATH** when you install it.

## What is on each screen
- **Sanctions screening**: screen one name in any language or script. Choose All / Individual /
  Organisation / Vessel / Aircraft-airline, or **Country / trade** to check a destination
  country against the US, UK, EU, UN, UAE and India programmes. If a country programme is confirmed,
  it shows in red with the official source. The specific transaction is always marked "not assessed".
- **Batch screening**: upload a CSV or Excel file (max 5 MB, 5,000 rows) and screen every row.
- **Screening history**: filter by name, country and date, then print or download as HTML or CSV.
  The HTML file opens in any browser, so you can send it to people who do not have ScanniX.
- **Manage lists**: add names to the UAE or India lists (typed in or uploaded from a file),
  and download any list filtered by name or country.
- **Data sources**: record counts, last-updated date and publication date for each list,
  plus Update buttons. Lists update automatically once a day while the app is open.

## Other ways to run it
- Update the lists only: `run_aml_screener.bat update` (you can schedule this daily in Task Scheduler)
- Local web service: `run_aml_screener.bat server`, then open http://127.0.0.1:8765/status

## Where data is kept
Everything stays on this computer, in the `.scannix` folder in your user folder.
UAE / India names you add are never mixed into the official lists.

## Optional: a single .exe
```
pip install pyinstaller
pyinstaller --onefile --windowed --name ScanniX --add-data "scannix_text.json;." aml_screener.py
```
