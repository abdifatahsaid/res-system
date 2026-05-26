import gspread, os, json, tempfile
from google.oauth2.service_account import Credentials

# ─── Spreadsheet ID ───
SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID",
    "128BYmGC0HorwWrN32vpbQ6S9edYG_0kGCtWIpH385F0"
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Credentials ───
# Railway  → GOOGLE_CREDENTIALS environment variable
# Laptop   → credentials.json file
def get_credentials_file():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        # Railway: environment variable ka samee temp file
        creds_dict = json.loads(creds_json)
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        json.dump(creds_dict, tmp)
        tmp.close()
        return tmp.name
    # Laptop: credentials.json file
    return os.path.join(BASE_DIR, "credentials.json")

def get_sheet_client():
    creds = Credentials.from_service_account_file(
        get_credentials_file(), scopes=SCOPES
    )
    return gspread.authorize(creds)

def get_spreadsheet():
    return get_sheet_client().open_by_key(SPREADSHEET_ID)

def get_worksheet(sheet_name):
    try:
        return get_spreadsheet().worksheet(sheet_name)
    except Exception as e:
        print(f"Worksheet error [{sheet_name}]: {e}")
        return None

def rows_to_dicts(ws):
    if not ws: return []
    try:
        return ws.get_all_records()
    except:
        return []

# ─── Sheet Names ───
FOOD_MENU     = "Food_Menu"
SHOPPING_MENU = "Shopping_Menu"
KHUDAAR_MENU  = "Khudaar_Menu"
RAASHIN_MENU  = "Raashin_Menu"
ORDERS        = "Orders"
PAYMENTS      = "Payments"
ABOUT         = "About"
MESSAGES      = "Messages"

# ─── Admin ───
ADMIN_USERNAME = "Admin"
ADMIN_PASSWORD = "12345"
SECRET_KEY     = "res_system_secret_key_2024"
