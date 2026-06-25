"""
Cryptographic engine for secure key storage.
Provides AES-256-GCM encryption with Argon2id key derivation.
"""
import base64
import json
import os
import secrets
from typing import Dict, Optional, Tuple, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


class CryptoEngine:
    """Secure cryptographic engine for encrypting/decrypting sensitive data."""
    
    # Constants
    SALT_SIZE = 16  # bytes for Argon2id salt
    NONCE_SIZE = 12  # bytes for AES-GCM nonce (recommended size)
    KEY_SIZE = 32  # bytes for AES-256 key
    TAG_SIZE = 16  # bytes for AES-GCM authentication tag
    
    # Argon2 parameters (conservative settings for security)
    ARGON2_ITERATIONS = 3  # Number of iterations
    ARGON2_MEMORY_COST = 65536  # 64MB memory usage
    ARGON2_LANES = 4  # Number of parallel lanes
    
    def __init__(self) -> None:
        """Initialize the crypto engine."""
        pass
    
    def derive_key(self, password: str, salt: bytes) -> bytes:
        """
        Derive an encryption key from a password using Argon2id.
        
        Args:
            password: The master password as a string
            salt: Random salt bytes
            
        Returns:
            bytes: 32-byte encryption key
        """
        kdf = Argon2id(
            salt=salt,
            length=self.KEY_SIZE,
            iterations=self.ARGON2_ITERATIONS,
            memory_cost=self.ARGON2_MEMORY_COST,
            lanes=self.ARGON2_LANES,
        )
        return kdf.derive(password.encode('utf-8'))
    
    def encrypt_data(self, plaintext: bytes, key: bytes) -> Tuple[bytes, bytes, bytes]:
        """
        Encrypt data using AES-256-GCM.
        
        Args:
            plaintext: Data to encrypt
            key: 32-byte encryption key
            
        Returns:
            Tuple of (ciphertext, nonce, tag)
        """
        # Generate a fresh random nonce
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        
        # Create AES-GCM cipher
        aesgcm = AESGCM(key)
        
        # Encrypt and get ciphertext with authentication tag
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        
        # Split ciphertext and tag (last TAG_SIZE bytes are the tag)
        ciphertext_only = ciphertext[:-self.TAG_SIZE]
        tag = ciphertext[-self.TAG_SIZE:]
        
        return ciphertext_only, nonce, tag
    
    def decrypt_data(self, ciphertext: bytes, nonce: bytes, tag: bytes, key: bytes) -> bytes:
        """
        Decrypt data using AES-256-GCM.
        
        Args:
            ciphertext: Encrypted data (without tag)
            nonce: Nonce used during encryption
            tag: Authentication tag
            key: 32-byte encryption key
            
        Returns:
            bytes: Decrypted plaintext
            
        Raises:
            InvalidTag: If authentication fails (tampered data)
        """
        # Combine ciphertext and tag
        ciphertext_with_tag = ciphertext + tag
        
        # Create AES-GCM cipher
        aesgcm = AESGCM(key)
        
        # Decrypt and verify authentication tag
        return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    
    def encrypt_json(self, data: Dict[str, Any], password: str) -> str:
        """
        Encrypt a JSON-serializable dictionary.
        
        Args:
            data: Dictionary to encrypt
            password: Master password
            
        Returns:
            str: JSON string containing encrypted data, salt, nonce, and tag
        """
        # Generate random salt
        salt = secrets.token_bytes(self.SALT_SIZE)
        
        # Derive key from password
        key = self.derive_key(password, salt)
        
        # Convert data to JSON bytes
        plaintext = json.dumps(data).encode('utf-8')
        
        # Encrypt the data
        ciphertext, nonce, tag = self.encrypt_data(plaintext, key)
        
        # Create result dictionary
        result = {
            "version": "1.0",
            "salt": base64.b64encode(salt).decode('utf-8'),
            "nonce": base64.b64encode(nonce).decode('utf-8'),
            "tag": base64.b64encode(tag).decode('utf-8'),
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
            "argon2_params": {
                "iterations": self.ARGON2_ITERATIONS,
                "memory_cost": self.ARGON2_MEMORY_COST,
                "lanes": self.ARGON2_LANES,
            }
        }
        
        return json.dumps(result, indent=2)
    
    def decrypt_json(self, encrypted_json: str, password: str) -> Dict[str, Any]:
        """
        Decrypt a JSON string back to a dictionary.
        
        Args:
            encrypted_json: JSON string from encrypt_json
            password: Master password
            
        Returns:
            Dict[str, Any]: Decrypted dictionary
            
        Raises:
            ValueError: If JSON is malformed or authentication fails
        """
        try:
            # Parse the encrypted JSON
            encrypted_data = json.loads(encrypted_json)
            
            # Extract and decode components
            salt = base64.b64decode(encrypted_data["salt"])
            nonce = base64.b64decode(encrypted_data["nonce"])
            tag = base64.b64decode(encrypted_data["tag"])
            ciphertext = base64.b64decode(encrypted_data["ciphertext"])
            
            # Derive key from password
            key = self.derive_key(password, salt)
            
            # Decrypt the data
            plaintext = self.decrypt_data(ciphertext, nonce, tag, key)
            
            # Parse back to dictionary
            return json.loads(plaintext.decode('utf-8'))
            
        except (KeyError, json.JSONDecodeError, InvalidTag) as e:
            raise ValueError(f"Decryption failed: {str(e)}")
    
    def verify_password(self, encrypted_json: str, password: str) -> bool:
        """
        Verify if a password is correct without decrypting the full data.
        
        Args:
            encrypted_json: JSON string from encrypt_json
            password: Password to verify
            
        Returns:
            bool: True if password is correct, False otherwise
        """
        try:
            # Try to decrypt a small part to verify password
            self.decrypt_json(encrypted_json, password)
            return True
        except ValueError:
            return False


def generate_secure_password(length: int = 32) -> str:
    """
    Generate a cryptographically secure random password.
    
    Args:
        length: Length of the password in characters
        
    Returns:
        str: Secure random password
    """
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()_+-=[]{}|;:,.<>?"
    return ''.join(secrets.choice(alphabet) for _ in range(length))