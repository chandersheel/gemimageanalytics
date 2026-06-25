from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1ducMmMf1m7EsXvKJreRPOl2ScGJm1UzRUY_c2afp1x8"

creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

# Get sheet ID
res = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
sheet_id = None
for s in res["sheets"]:
    if s["properties"]["title"] == "df_spec_10420":
        sheet_id = s["properties"]["sheetId"]
        break

if sheet_id is not None:
    body = {
        "requests": [
            {
                "appendDimension": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "length": 5
                }
            }
        ]
    }
    svc.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
    print("Added 5 columns to the sheet to make room for Human_Review and Human_Reason.")
