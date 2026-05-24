"""
Run this script ONCE to fix existing Payments sheet Category column.
Usage: python fix_payments.py
"""
import sys, json
sys.path.insert(0, '.')
from config import *

print("=" * 50)
print("  FIX PAYMENTS CATEGORY — Running...")
print("=" * 50)

# Connect
try:
    ws = get_worksheet(PAYMENTS)
    if not ws:
        print("ERROR: Cannot connect to Payments sheet")
        sys.exit(1)
    print("✅ Connected to Google Sheets")
except Exception as e:
    print(f"ERROR connecting: {e}")
    sys.exit(1)

# Get headers
headers = ws.row_values(1)
print(f"Headers: {headers}")

# Find Category column
try:
    cat_col = headers.index("Category") + 1
    print(f"Category column index: {cat_col}")
except ValueError:
    print("ERROR: 'Category' column not found in Payments sheet!")
    sys.exit(1)

# Get all records
records = ws.get_all_records()
print(f"Total rows: {len(records)}")
print()

fixed   = 0
skipped = 0

for i, row in enumerate(records, start=2):
    cat_val = str(row.get("Category", "")).strip()

    # Skip if already plain text
    if not (cat_val.startswith('[') or cat_val.startswith('{')):
        skipped += 1
        continue

    # Try to parse JSON
    try:
        # Clean escaped quotes
        clean_val = cat_val.replace('\\"', '"').replace('\\\\', '')
        parsed    = json.loads(clean_val)
        arr       = parsed if isinstance(parsed, list) else [parsed]

        # Build clean text
        parts = []
        for item in arr:
            name = item.get('name') or item.get('Name') or '?'
            qty  = item.get('qty')  or item.get('Qty')  or 1
            parts.append(f"{name} x{qty}")

        clean_text = " | ".join(parts)
        ws.update_cell(i, cat_col, clean_text)
        print(f"✅ Row {i}: {cat_val[:40]}...")
        print(f"        → {clean_text}")
        fixed += 1

        # Small delay to avoid rate limiting
        import time
        time.sleep(0.5)

    except Exception as e:
        print(f"❌ Row {i}: Parse error — {e}")
        print(f"   Value: {cat_val[:60]}")

print()
print("=" * 50)
print(f"✅ Fixed:   {fixed} rows")
print(f"⏭️  Skipped: {skipped} rows (already clean)")
print("=" * 50)
print("\nDone! Refresh your Payment History page.")
