#!/usr/bin/env python3
"""
Process the Excel file from the user and create the address database.
"""
import pandas as pd
import json
import sys
from pathlib import Path
from import_addresses import parse_excel_to_database


def main():
    # The Excel file is at C:\Users\krisr\Desktop\list_of_addresses.xlsx
    excel_path = r"C:\Users\krisr\Desktop\list_of_addresses.xlsx"
    output_path = "address_database.json"
    
    if not Path(excel_path).exists():
        print(f"Error: Excel file not found at {excel_path}")
        print("Please ensure the file exists or provide a different path.")
        sys.exit(1)
    
    print(f"Reading Excel file: {excel_path}")
    
    try:
        # Parse the Excel file
        database = parse_excel_to_database(excel_path)
        
        # Save to JSON file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(database, f, indent=2)
        
        print(f"Successfully created {output_path}")
        print(f"Accounts imported: {len(database['accounts'])}")
        
        # Show summary
        total_addresses = 0
        for account_name, account_data in database['accounts'].items():
            addresses_count = len(account_data['addresses'])
            total_addresses += addresses_count
            print(f"  {account_name}: {addresses_count} addresses")
        
        print(f"\nTotal addresses: {total_addresses}")
        
        # Show pool summary
        print("\nPool Summary:")
        for pool_name, pool_data in database['pools'].items():
            pool_accounts = pool_data['accounts']
            imported_count = sum(1 for acc in pool_accounts if acc in database['accounts'])
            print(f"  {pool_name}: {imported_count}/{len(pool_accounts)} accounts have addresses")
            
    except Exception as e:
        print(f"Error processing Excel file: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()