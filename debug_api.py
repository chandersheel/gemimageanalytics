"""Quick test to debug /api/items"""
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1ducMmMf1m7EsXvKJreRPOl2ScGJm1UzRUY_c2afp1x8"
SHEET_NAME = "df_spec_10420"

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

# Read all data
result = svc.spreadsheets().values().get(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'"
).execute()
values = result.get("values", [])
headers = values[0]
rows = values[1:]

print(f"Headers ({len(headers)}): {headers}")
print(f"Total rows: {len(rows)}")

# Check if Human_Review exists
if "Human_Review" in headers:
    print("Human_Review column EXISTS")
else:
    print("Human_Review column MISSING — need to add it")

# Check existing cols that might conflict
for col in ["Benchmark Link", "Reason", "Human_Review", "Human_Reason"]:
    if col in headers:
        print(f"  '{col}' found at index {headers.index(col)}")
    else:
        print(f"  '{col}' NOT found")

# Try ensure_columns logic
needed = ["Human_Review", "Human_Reason"]
missing = [c for c in needed if c not in headers]
print(f"\nMissing columns to add: {missing}")

if missing:
    def col_letter(n):
        r = ""
        while n:
            n, rem = divmod(n - 1, 26)
            r = chr(65 + rem) + r
        return r
    
    start_col = col_letter(len(headers) + 1)
    end_col = col_letter(len(headers) + len(missing))
    range_str = f"'{SHEET_NAME}'!{start_col}1:{end_col}1"
    print(f"Would write to range: {range_str}")
    print(f"Values: {missing}")
    
    # Actually write it
    try:
        svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=range_str,
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()
        print("✅ Columns added successfully!")
    except Exception as e:
        print(f"❌ Failed to add columns: {e}")

# Now test grouping logic
print("\nTesting grouping logic...")
new_headers = headers + missing if missing else headers
link_idx = new_headers.index("app_display_product_link") if "app_display_product_link" in new_headers else None
print(f"  Link column index: {link_idx}")

if link_idx is not None:
    unique_links = set()
    for row in rows[:100]:  # just first 100 rows
        padded = list(row) + [""] * (len(new_headers) - len(row))
        link = (padded[link_idx] or "").strip()
        if link:
            unique_links.add(link)
    print(f"  Unique links in first 100 rows: {len(unique_links)}")
    if unique_links:
        print(f"  Sample link: {list(unique_links)[0][:80]}")
