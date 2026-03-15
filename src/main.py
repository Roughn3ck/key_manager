"""
Key Manager - Secure Crypto Key Storage
CLI interface for managing encrypted cryptocurrency keys and addresses.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from crypto_engine import CryptoEngine, generate_secure_password


console = Console()
crypto = CryptoEngine()

# Constants
DATA_FILE = "key_vault.encrypted"
CONFIG_DIR = Path.home() / ".key_manager"


class KeyManager:
    """Main application for managing encrypted keys."""
    
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        """
        Initialize the key manager.
        
        Args:
            data_dir: Directory to store encrypted data (defaults to CONFIG_DIR)
        """
        self.data_dir = data_dir or CONFIG_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_file = self.data_dir / DATA_FILE
        
        # Sample address database structure (will be loaded from encrypted storage)
        self.address_db = {
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
            "mnemonics": {}  # Encrypted 24-word mnemonics
        }
    
    def load_encrypted_data(self, password: str) -> bool:
        """
        Load encrypted data from file.
        
        Args:
            password: Master password
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.data_file.exists():
            return False
        
        try:
            with open(self.data_file, 'r') as f:
                encrypted_json = f.read()
            
            # Decrypt the data
            decrypted_data = crypto.decrypt_json(encrypted_json, password)
            
            # Update address database with loaded data
            self.address_db.update(decrypted_data)
            return True
            
        except Exception as e:
            console.print(f"[red]Error loading data: {e}[/red]")
            return False
    
    def save_encrypted_data(self, password: str) -> bool:
        """
        Save encrypted data to file.
        
        Args:
            password: Master password
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Encrypt the data
            encrypted_json = crypto.encrypt_json(self.address_db, password)
            
            # Save to file
            with open(self.data_file, 'w') as f:
                f.write(encrypted_json)
            
            return True
            
        except Exception as e:
            console.print(f"[red]Error saving data: {e}[/red]")
            return False
    
    def initialize_vault(self, password: str) -> bool:
        """
        Initialize a new encrypted vault.
        
        Args:
            password: Master password
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.data_file.exists():
            console.print("[yellow]Vault already exists. Use 'unlock' instead.[/yellow]")
            return False
        
        # Initialize with sample data structure
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task(description="Initializing secure vault...", total=None)
            
            # Save initial empty vault
            success = self.save_encrypted_data(password)
        
        if success:
            console.print("[green]✓ Secure vault initialized successfully[/green]")
            return True
        else:
            console.print("[red]Failed to initialize vault[/red]")
            return False
    
    def add_mnemonic(self, account_name: str, mnemonic: str, password: str) -> bool:
        """
        Add or update a mnemonic for an account.
        
        Args:
            account_name: Name of the account
            mnemonic: 24-word mnemonic phrase
            password: Master password for encryption
            
        Returns:
            bool: True if successful, False otherwise
        """
        # Store encrypted mnemonic
        self.address_db["mnemonics"][account_name] = mnemonic
        
        # Save changes
        return self.save_encrypted_data(password)
    
    def add_address(self, account: str, coin: str, chain: str, address: str, password: str) -> bool:
        """
        Add an address to an account.
        
        Args:
            account: Account name
            coin: Cryptocurrency (e.g., BTC, ETH)
            chain: Blockchain/network (e.g., ERC-20, P2TR)
            address: Wallet address
            password: Master password for saving
            
        Returns:
            bool: True if successful, False otherwise
        """
        # Initialize account if not exists
        if account not in self.address_db["accounts"]:
            self.address_db["accounts"][account] = {
                "addresses": [],
                "notes": ""
            }
        
        # Add address
        self.address_db["accounts"][account]["addresses"].append({
            "coin": coin,
            "chain": chain,
            "address": address
        })
        
        # Save changes
        return self.save_encrypted_data(password)
    
    def list_accounts(self) -> None:
        """Display all accounts in a formatted table."""
        table = Table(title="Accounts by Pool")
        
        table.add_column("Pool", style="cyan")
        table.add_column("Description", style="yellow")
        table.add_column("Accounts", style="green")
        
        for pool_name, pool_data in self.address_db["pools"].items():
            accounts = ", ".join(pool_data["accounts"])
            table.add_row(pool_name, pool_data["description"], accounts)
        
        console.print(table)
    
    def show_addresses(self, account: Optional[str] = None) -> None:
        """
        Display addresses for all accounts or a specific account.
        
        Args:
            account: Optional account name to filter by
        """
        if account:
            if account not in self.address_db["accounts"]:
                console.print(f"[red]Account '{account}' not found[/red]")
                return
            
            self._show_account_addresses(account)
        else:
            # Show all accounts
            for acc_name in self.address_db["accounts"]:
                self._show_account_addresses(acc_name)
    
    def _show_account_addresses(self, account: str) -> None:
        """Display addresses for a specific account."""
        acc_data = self.address_db["accounts"][account]
        
        panel = Panel.fit(
            f"[bold cyan]{account}[/bold cyan]\n"
            f"[yellow]{acc_data.get('notes', 'No notes')}[/yellow]",
            title=f"Account: {account}"
        )
        console.print(panel)
        
        if not acc_data["addresses"]:
            console.print("[dim]No addresses stored[/dim]")
            return
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Coin")
        table.add_column("Chain")
        table.add_column("Address")
        
        for addr in acc_data["addresses"]:
            table.add_row(
                addr["coin"],
                addr["chain"],
                addr["address"]
            )
        
        console.print(table)


@click.group()
@click.pass_context
def cli(ctx):
    """Secure Crypto Key Manager - Store and manage encrypted cryptocurrency keys."""
    ctx.ensure_object(dict)
    ctx.obj['manager'] = KeyManager()


@cli.command()
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True,
              help='Master password for the vault')
def init(password: str):
    """Initialize a new encrypted vault."""
    manager = KeyManager()
    manager.initialize_vault(password)


@cli.command()
@click.option('--password', prompt=True, hide_input=True,
              help='Master password to unlock the vault')
def unlock(password: str):
    """Unlock and load an existing vault."""
    manager = KeyManager()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="Unlocking vault...", total=None)
        
        if manager.load_encrypted_data(password):
            console.print("[green]✓ Vault unlocked successfully[/green]")
            # Store manager in context for subsequent commands
            click.get_current_context().obj['manager'] = manager
        else:
            console.print("[red]Failed to unlock vault. Wrong password or vault doesn't exist.[/red]")
            sys.exit(1)


@cli.command()
@click.pass_context
def accounts(ctx):
    """List all accounts organized by pool."""
    manager = ctx.obj.get('manager')
    if not manager:
        console.print("[red]Please unlock the vault first: key-manager unlock[/red]")
        sys.exit(1)
    
    manager.list_accounts()


@cli.command()
@click.argument('account', required=False)
@click.pass_context
def addresses(ctx, account: Optional[str]):
    """Show addresses for all accounts or a specific account."""
    manager = ctx.obj.get('manager')
    if not manager:
        console.print("[red]Please unlock the vault first: key-manager unlock[/red]")
        sys.exit(1)
    
    manager.show_addresses(account)


@cli.command()
@click.argument('account')
@click.argument('coin')
@click.argument('chain')
@click.argument('address')
@click.option('--password', prompt=True, hide_input=True,
              help='Master password to save changes')
@click.pass_context
def add_address(ctx, account: str, coin: str, chain: str, address: str, password: str):
    """Add an address to an account."""
    manager = ctx.obj.get('manager')
    if not manager:
        console.print("[red]Please unlock the vault first: key-manager unlock[/red]")
        sys.exit(1)
    
    if manager.add_address(account, coin, chain, address, password):
        console.print("[green]✓ Address added successfully[/green]")
    else:
        console.print("[red]Failed to add address[/red]")


@cli.command()
@click.argument('account')
@click.argument('mnemonic', nargs=-1)
@click.option('--password', prompt=True, hide_input=True,
              help='Master password to save changes')
@click.pass_context
def add_mnemonic(ctx, account: str, mnemonic: tuple, password: str):
    """Add or update a mnemonic for an account."""
    manager = ctx.obj.get('manager')
    if not manager:
        console.print("[red]Please unlock the vault first: key-manager unlock[/red]")
        sys.exit(1)
    
    mnemonic_str = ' '.join(mnemonic)
    if len(mnemonic_str.split()) != 24:
        console.print("[red]Mnemonic must be 24 words[/red]")
        sys.exit(1)
    
    if manager.add_mnemonic(account, mnemonic_str, password):
        console.print("[green]✓ Mnemonic added successfully[/green]")
    else:
        console.print("[red]Failed to add mnemonic[/red]")


@cli.command()
def gen_password():
    """Generate a secure random password."""
    password = generate_secure_password()
    console.print(Panel(
        f"[green]{password}[/green]",
        title="Secure Password",
        subtitle="Copy this password and store it safely"
    ))


@cli.command()
@click.pass_context
def status(ctx):
    """Show vault status and statistics."""
    manager = ctx.obj.get('manager')
    if not manager:
        console.print("[red]Please unlock the vault first: key-manager unlock[/red]")
        sys.exit(1)
    
    vault_exists = manager.data_file.exists()
    
    info_table = Table(title="Vault Status", show_header=False)
    info_table.add_column("Property", style="cyan")
    info_table.add_column("Value", style="green")
    
    info_table.add_row("Vault Location", str(manager.data_file))
    info_table.add_row("Vault Exists", "✓ Yes" if vault_exists else "✗ No")
    
    if vault_exists:
        file_size = manager.data_file.stat().st_size
        info_table.add_row("File Size", f"{file_size:,} bytes")
    
    info_table.add_row("Accounts Stored", str(len(manager.address_db["accounts"])))
    info_table.add_row("Mnemonics Stored", str(len(manager.address_db["mnemonics"])))
    
    console.print(info_table)


if __name__ == '__main__':
    cli()