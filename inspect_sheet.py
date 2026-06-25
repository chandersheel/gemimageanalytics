"""Inspect the Google Sheet to understand its structure."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1ducMmMf1m7EsXvKJreRPOl2ScGJm1UzRUY_c2afp1x8"

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

# 1. List all sheet tabs
meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
print("=== Sheet Tabs ===")
for s in meta["sheets"]:
    props = s["properties"]
    grid = props.get("gridProperties", {})
    print(f"  Tab: '{props['title']}'  rows={grid.get('rowCount','?')}  cols={grid.get('columnCount','?')}")

# 2. Read headers + first 3 data rows from each tab
for s in meta["sheets"]:
    tab = s["properties"]["title"]
    print(f"\n=== Data from '{tab}' ===")
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab}'!A1:ZZ5"
    ).execute()
    values = result.get("values", [])
    if not values:
        print("  (empty)")
        continue
    headers = values[0]
    print(f"  Headers ({len(headers)} cols): {headers}")
    for i, row in enumerate(values[1:], 1):
        print(f"  Row {i}: {row[:8]}{'...' if len(row)>8 else ''}")

# 3. Count total data rows in the main tab
for s in meta["sheets"]:
    tab = s["properties"]["title"]
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab}'"
    ).execute()
    values = result.get("values", [])
    data_rows = len(values) - 1 if len(values) > 1 else 0
    print(f"\nTotal data rows in '{tab}': {data_rows}")
