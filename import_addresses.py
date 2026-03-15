#!/usr/bin/env python3
"""
Import script for addresses from Excel/CSV spreadsheets.
This creates a JSON structure that can be loaded into the key manager.
"""
import json
import csv
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def parse_excel_to_database(excel_path: str) -> Dict[str, Any]:
    """
    Parse Excel file and convert to key manager database structure.
    
    Args:
        excel_path: Path to Excel file
        
    Returns:
        Dictionary with key manager database structure
    """
    if not HAS_PANDAS:
        print("Error: pandas is required for Excel file parsing. Install with: pip install pandas")
        sys.exit(1)
    
    # Read Excel file
    df = pd.read_excel(excel_path)
    
    # Convert to list of dictionaries
    rows = df.to_dict('records')
    
    return parse_rows_to_database(rows)


def parse_csv_to_database(csv_path: str) -> Dict[str, Any]:
    """
    Parse CSV file and convert to key manager database structure.
    
    Args:
        csv_path: Path to CSV file
        
    Returns:
        Dictionary with key manager database structure
    """
    # Read CSV file
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    return parse_rows_to_database(rows)


def parse_rows_to_database(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Parse rows from any source and convert to key manager database structure.
    
    Args:
        rows: List of dictionaries with Account, Coin, Chain, Address keys
        
    Returns:
        Dictionary with key manager database structure
    """
    database = {
        "version": "1.0",
        "pools": {
            "Genesis": {
                "description": "Genesis pool - 6 accounts",
                "accounts": ["G SS", "Expenses", "G1", "G2", "G3", "G4", "G5", "G6"]
            },
            "SafetyNet": {
                "description": "SafetyNet pool - 5 accounts",
                "accounts": ["N1", "N2", "N3", "N4", "N5"]
            },
            "Foundation": {
                "description": "Foundation pool - 5 accounts",
                "accounts": ["F1", "F2", "F3", "F4", "F5"]
            },
            "Seed": {
                "description": "Seed pool - 5 accounts",
                "accounts": ["S1", "S2", "S3", "S4", "S5"]
            }
        },
        "accounts": {},
        "mnemonics": {}
    }
    
    # Group by account
    current_account = None
    for row in rows:
        # Handle different column naming
        account = None
        coin = None
        chain = None
        address = None
        
        # Try different possible column names
        for key in row:
            key_lower = key.lower()
            if 'account' in key_lower:
                account = str(row[key]).strip() if pd.notna(row[key]) else ''
            elif 'coin' in key_lower:
                coin = str(row[key]).strip() if pd.notna(row[key]) else ''
            elif 'chain' in key_lower:
                chain = str(row[key]).strip() if pd.notna(row[key]) else ''
            elif 'address' in key_lower:
                address = str(row[key]).strip() if pd.notna(row[key]) else ''
        
        # If we didn't find standard columns, use first 4 columns
        if account is None:
            values = list(row.values())
            if len(values) >= 4:
                account = str(values[0]).strip() if pd.notna(values[0]) else ''
                coin = str(values[1]).strip() if pd.notna(values[1]) else ''
                chain = str(values[2]).strip() if pd.notna(values[2]) else ''
                address = str(values[3]).strip() if pd.notna(values[3]) else ''
        
        # Skip empty rows
        if not account and not coin and not address:
            continue
        
        # Update current account if we have a new one
        if account:
            current_account = account
        
        # Skip if no current account or no address
        if not current_account or not address:
            continue
        
        # Initialize account if not exists
        if current_account not in database["accounts"]:
            database["accounts"][current_account] = {
                "addresses": [],
                "notes": f"Account imported from spreadsheet",
                "pool": determine_pool(current_account, database["pools"])
            }
        
        # Add address
        database["accounts"][current_account]["addresses"].append({
            "coin": coin if coin else "Unknown",
            "chain": chain if chain else "Unknown",
            "address": address
        })
    
    return database


def determine_pool(account_name: str, pools: Dict[str, Any]) -> Optional[str]:
    """
    Determine which pool an account belongs to.
    
    Args:
        account_name: Name of the account
        pools: Dictionary of pools
        
    Returns:
        Pool name or None if not found
    """
    for pool_name, pool_data in pools.items():
        if account_name in pool_data["accounts"]:
            return pool_name
    return None


def create_sample_csv() -> None:
    """Create a sample CSV file with addresses from the spreadsheet."""
    sample_data = [
        ["Account", "Coin", "Chain", "Address"],
        ["Main", "ETH", "ERC-20", "0xfd5e77bf38843FC65Dcc56a955b26DAC90DB70cA"],
        ["Main", "DASH", "DASH", "Xip8jyAJeN3PtHg9Yq94eznMFc4PMr3Lgh"],
        ["Main", "DASH", "Asgardex", "Xibe9hLZsAmLHbnXeCHfmharCVZ4bNEKDH"],
        ["Main", "DASH", "Safepal wallet", "XfNH3WFzL4cCsu4MwKPJ3EnZUkseXM5nPd"],
        ["Monero", "XMR", "", "43hHG641AW6H1rPGM9U6gvYXjtpRrFe3aVwAzReuYsSzNgRSCYr7LRKM3bw4L9da5P6DfzZo21VdAcYphyD9YEwg88Yhm2i"],
        ["G SS", "ETH", "ERC-20", "0xdF96c97d31044FE47160CCA52EF20819DD8675b9"],
        ["G SS", "EVM", "Railgun", "0zk1qyg0sz8whuypapwnf69ul5hk94es6slnfg8yz7auchw2p5lkwsgnlrv7j6fe3z53l7jp9xl26flpm82w2xluvdp3u4238ncjz026csjhu53xltwwjplq5k62uyt"],
        ["G SS", "BTC", "P2WPKH (segwit)", "bc1q4zhsv905e22lj80n377c5nqnvej2w8w37hq6a0"],
        ["G SS", "BTC", "segwit (Safepal)", "39f4NAKgx6BdfTqPZFkrozi4ksPKN5BuwM"],
        ["G SS", "ZEC", "Transparent", "t1cDntSrwvXBd34RfBTz8AjFHF8vkqDJEX1"],
        ["G SS", "ZEC", "Orchard", "u1vn7ep73xsjs62jza5aayrc35umlgg2sr5czxf0rhfsp7t5udcgqeeqlyfzafln3qw6jjdzcsshrfm9rhw4rwrvw3r0cpxhjtjya5dkpl"],
        ["G SS", "DASH", "DASH", "XjE9FbMRPVEAJ3qnaoLSAyEYwuxitjUXbG"],
        ["G SS", "Rune", "Thorchain", "thor1q6e6r9lgk8mgmkjshputrpmj25x66qspnzxfec"],
        ["G SS", "Atom", "Cosmoshub", "cosmos1et40s2stdwutsna24upzywtspfdfdwwkjer78h"],
        ["Expenses", "BTC", "P2TR (taproot)", "bc1prkrkmsy46zq69sjcdy62a2d9gsas4dw0ndx8ra3k4klruma4zdyq8f2nw3"],
        ["Expenses", "BTC", "P2WPKH (segwit)", "bc1qnx36t6ngtamjzs0nmusz3dey9x2u2eaq2kphsm"],
        ["Expenses", "BTC", "Asgardex", "bc1qdpak8yv8raqnhxj5yk2myxpefkphxdda6xhkjc"],
        ["Expenses", "ETH", "ERC-20", "0x6bf18775dcd95cabc3bdf2c0a4f0375e0a6e51b04dadd67d2f480f6c9baaa579"],
        ["Expenses", "ETH", "ERC-20 (Stackwallet & Infiniti)", "0xF30CB32c98e5CeF62302D19fB6ea510223847E4c"],
        ["Expenses", "XMR", "Monero", "43hHG641AW6H1rPGM9U6gvYXjtpRrFe3aVwAzReuYsSzNgRSCYr7LRKM3bw4L9da5P6DfzZo21VdAcYphyD9YEwg88Yhm2i"],
        ["Expenses", "DASH", "DASH", "Xv3ApHQmq28tejuVri9ymBfTnBB3B5zV5P"],
        ["Expenses", "ZEC", "Transparent", "t1Mzpbm8tgnAAgkPv9Pp3mnsGKBP5VoKwgc"],
        ["Expenses", "ZEC", "Orchard", "u1ttl70l5s4xmrhmael4hx3xdhwd29pf8snfc9t23r8eq7rgn4zsaal2ffhtnmkw5dkw9g8mczvgnq2hm2ytf6t7g7vgpktutvauhdvmvm"],
        ["G1", "BTC", "P2TR (taproot)", "bc1pjkd9am2m6jlnuga6yq2vs8tsa84nwjtf9gq794u2jvhe0v2r7yvqzz0qzn"],
        ["G1", "BTC", "P2WPKH (segwit)", "bc1qqzxc9d36z5uj0j23qgwd9sdjkea2dn59h32z4a"],
        ["G1", "ETH", "ERC-20", "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae"],
        ["G1", "Chainflip Acct", "", "cFHsUq1uK5opJudRDd1X1Duj4s6LNTv7qcyUSbyFf6CHkE89N"],
        ["G1", "DASH", "DASH", "XeGoTiKLawUYNW9w77AKtDh4Q55b8aUWE6"],
        ["G1", "SOL", "Solana", "2cM48EmQLW3yACXNTX7vb7v1a8crCcXXd2CmwrdnveWe"],
    ]
    
    with open('sample_addresses.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(sample_data)
    
    print("Created sample_addresses.csv")


def main():
    """Main function to import addresses."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Import addresses into key manager database')
    parser.add_argument('--csv', type=str, help='Path to CSV file with addresses')
    parser.add_argument('--output', type=str, default='address_database.json',
                       help='Output JSON file (default: address_database.json)')
    parser.add_argument('--create-sample', action='store_true',
                       help='Create a sample CSV file')
    
    args = parser.parse_args()
    
    if args.create_sample:
        create_sample_csv()
        return
    
    if not args.csv:
        print("Error: Please provide a CSV file with --csv or use --create-sample to create one")
        parser.print_help()
        return
    
    # Parse CSV and create database
    database = parse_csv_to_database(args.csv)
    
    # Save to JSON file
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(database, f, indent=2)
    
    print(f"Successfully created {args.output}")
    print(f"Accounts imported: {len(database['accounts'])}")
    
    # Show summary
    for pool_name, pool_data in database['pools'].items():
        pool_accounts = pool_data['accounts']
        imported_count = sum(1 for acc in pool_accounts if acc in database['accounts'])
        print(f"{pool_name}: {imported_count}/{len(pool_accounts)} accounts have addresses")


if __name__ == '__main__':
    main()