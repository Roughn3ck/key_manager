"""
Backup Engine for Key Manager.
Provides automated timestamped encrypted backups of the vault.
"""
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import json


class BackupEngine:
    """Handles automated backup of encrypted vault files."""
    
    # Maximum number of backups to retain
    MAX_BACKUPS = 10
    BACKUP_FOLDER_NAME = "backups"
    
    def __init__(self, data_dir: Path) -> None:
        """
        Initialize the backup engine.
        
        Args:
            data_dir: Directory where the main vault is stored
        """
        self.data_dir = data_dir
        self.backup_dir = data_dir / self.BACKUP_FOLDER_NAME
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def backup_vault(self, vault_file: Path, reason: str = "manual") -> Optional[Path]:
        """
        Create a timestamped backup of the vault file.
        
        This function generates a separate, timestamped, encrypted copy of the
        current storage file whenever a significant change is made.
        
        Args:
            vault_file: Path to the encrypted vault file
            reason: Description of why backup was created (e.g., "add_address", "add_mnemonic")
            
        Returns:
            Path to the backup file if successful, None otherwise
        """
        if not vault_file.exists():
            return None
        
        try:
            # Generate timestamp-based filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"vault_backup_{timestamp}_{reason}.encrypted"
            backup_path = self.backup_dir / backup_filename
            
            # Copy the encrypted vault file (preserves encryption)
            shutil.copy2(vault_file, backup_path)
            
            # Create backup metadata
            metadata = {
                "original_file": str(vault_file),
                "backup_time": datetime.now().isoformat(),
                "reason": reason,
                "original_size": vault_file.stat().st_size,
                "backup_file": backup_filename
            }
            
            metadata_file = backup_path.with_suffix(".meta.json")
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Rotate old backups
            self._rotate_backups()
            
            return backup_path
            
        except Exception as e:
            print(f"Backup failed: {e}")
            return None
    
    def _rotate_backups(self) -> None:
        """Remove oldest backups if we exceed MAX_BACKUPS."""
        # Get all backup files sorted by modification time
        backup_files = sorted(
            self.backup_dir.glob("vault_backup_*.encrypted"),
            key=lambda f: f.stat().st_mtime
        )
        
        # Remove oldest backups if over limit
        while len(backup_files) > self.MAX_BACKUPS:
            oldest = backup_files.pop(0)
            oldest.unlink()
            
            # Also remove metadata file
            meta_file = oldest.with_suffix(".meta.json")
            if meta_file.exists():
                meta_file.unlink()
    
    def list_backups(self) -> List[dict]:
        """
        List all available backups with metadata.
        
        Returns:
            List of backup metadata dictionaries
        """
        backups = []
        for meta_file in sorted(self.backup_dir.glob("*.meta.json"), reverse=True):
            try:
                with open(meta_file, 'r') as f:
                    metadata = json.load(f)
                    backup_file = self.backup_dir / metadata["backup_file"]
                    if backup_file.exists():
                        metadata["exists"] = True
                        metadata["size"] = backup_file.stat().st_size
                        backups.append(metadata)
            except (json.JSONDecodeError, KeyError):
                continue
        
        return backups
    
    def restore_backup(self, backup_filename: str, vault_file: Path) -> bool:
        """
        Restore a backup to the main vault file.
        
        Args:
            backup_filename: Name of the backup file to restore
            vault_file: Path to the main vault file
            
        Returns:
            bool: True if successful, False otherwise
        """
        backup_path = self.backup_dir / backup_filename
        
        if not backup_path.exists():
            return False
        
        try:
            # Create a safety backup of current vault before restoring
            if vault_file.exists():
                self.backup_vault(vault_file, reason="pre_restore")
            
            # Restore the backup
            shutil.copy2(backup_path, vault_file)
            return True
            
        except Exception as e:
            print(f"Restore failed: {e}")
            return False
    
    def get_backup_count(self) -> int:
        """Get the number of existing backups."""
        return len(list(self.backup_dir.glob("vault_backup_*.encrypted")))
