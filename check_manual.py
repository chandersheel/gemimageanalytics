from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1ducMmMf1m7EsXvKJreRPOl2ScGJm1UzRUY_c2afp1x8"
SHEET_NAME = "df_spec_10420"

creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

res = svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}").execute()
values = res.get("values", [])

headers = values[0]
print("Headers:", headers)

count_manual = 0
for row in values[1:]:
    padded = list(row) + [""] * (len(headers) - len(row))
    bench = padded[headers.index("Benchmark Link")].strip() if "Benchmark Link" in headers else ""
    reason = padded[headers.index("Reason")].strip() if "Reason" in headers else ""
    if bench or reason:
        count_manual += 1

print(f"Rows with Benchmark Link or Reason filled: {count_manual}")
