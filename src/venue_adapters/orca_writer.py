"""Orca Whirlpool Venue Writer - ColdStack LP Engine v5.2.5

Write operations for Orca Whirlpool LP positions on Solana.

Architecture:
  - Writer builds instructions (Anchor-layout account lists + data blobs)
  - Compiles Solana messages with correct per-account signer/writable metadata
  - Delegates Ed25519 signing to the key_manager_agent (never touches private keys)
  - Single-signer flows: agent builds + broadcasts fully (broadcast_solana_tx)
  - Multi-signer flows (openPosition needs wallet + ephemeral NFT mint keypair):
    the writer generates a fresh random position-mint keypair locally, signs
    with it in pure Python (no agent needed — the key is freshly generated and
    thrown away), asks the agent for the wallet signature, assembles the full
    signed transaction, and POSTs it to broadcast_raw_solana_tx.

All Solana account layouts and instruction discriminators verified against
orca-so/whirlpools source. Stdlib urllib.request only.

Version: v5.2.5 (August 2026) - collect_fees + close_position + rebalance
"""
import base64
import hashlib
import json
import os
import struct
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from venue_adapters.venue_writer import (
    VenueWriter, CollectFeesParams, CompoundFeesParams,
    DecreaseLiquidityParams, IncreaseLiquidityParams,
    OpenPositionParams, RebalanceParams, SwapParams,
)
from venue_adapters.orca_adapter import (
    SOLANA_RPC_URL,
    WHIRLPOOL_PROGRAM_ID,
    SPL_TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    POSITION_ACCOUNT_LEN,
    POOL_ACCOUNT_LEN,
    _b58decode,
    _b58encode,
    _is_solana_address,
    _solana_rpc_call,
    _get_account_data,
    _decode_position_data,
    _decode_pool_data,
    _derive_position_address,
    _get_sol_token_symbol,
    _get_sol_token_decimals,
    _find_program_address,
    _detect_token_program,
)


# ---------------------------------------------------------------------------
# Solana system / SPL constants
# ---------------------------------------------------------------------------

SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
# Token program is now detected per-position via _get_token_program()
# (see the OrcaWriter class below). SPL_TOKEN_PROGRAM_ID kept as the default.
ATA_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
RENT_SYSVAR = "SysvarRent111111111111111111111111111111111"
CLOCK_SYSVAR = "SysvarC1ock11111111111111111111111111111111"
WHIRLPOOL_PROGRAM = WHIRLPOOL_PROGRAM_ID

# Whirlpool tick array parameters (from orca-so/whirlpools state/tick_array.rs)
TICK_ARRAY_SIZE = 88


# ---------------------------------------------------------------------------
# Anchor instruction discriminators
# Anchor convention: sha256(b"global:" + instruction_name_snake_case)[:8]
# ---------------------------------------------------------------------------

def _anchor_disc(name: str) -> bytes:
    """Compute the Anchor instruction discriminator for an instruction name (snake_case)."""
    return hashlib.sha256(b"global:" + name.encode()).digest()[:8]


DISC_COLLECT_FEES = _anchor_disc("collect_fees")
DISC_DECREASE_LIQUIDITY = _anchor_disc("decrease_liquidity")
DISC_INCREASE_LIQUIDITY = _anchor_disc("increase_liquidity")
DISC_OPEN_POSITION = _anchor_disc("open_position")
DISC_CLOSE_POSITION = _anchor_disc("close_position")
DISC_SWAP = _anchor_disc("swap")
DISC_COLLECT_REWARD = _anchor_disc("collect_reward")


# ---------------------------------------------------------------------------
# Pure-Python Ed25519 (for ephemeral NFT mint keypair signing — local only,
# never persisted, never sent to the agent)
# ---------------------------------------------------------------------------

_ED_P = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_BY = 46316835694926478169428394003475163141307993866256225615783033603165251855960
_ED_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_ED_G = (_ED_BX, _ED_BY)


def _ed_add(P, Q):
    x1, y1 = P
    x2, y2 = Q
    d = (-121665 * pow(121666, _ED_P - 2, _ED_P)) % _ED_P
    x1x2 = x1 * x2 % _ED_P
    y1y2 = y1 * y2 % _ED_P
    dx1x2y1y2 = d * x1x2 * y1y2 % _ED_P
    x3 = (x1 * y2 + x2 * y1) * pow(1 + dx1x2y1y2, _ED_P - 2, _ED_P) % _ED_P
    y3 = (y1y2 + x1x2) * pow(1 - dx1x2y1y2, _ED_P - 2, _ED_P) % _ED_P
    return (x3, y3)


def _ed_scalarmult(P, e):
    Q = (0, 1)
    while e > 0:
        if e & 1:
            Q = _ed_add(Q, P)
        P = _ed_add(P, P)
        e >>= 1
    return Q


def _ed_compress(P):
    x, y = P
    yb = y.to_bytes(32, "little")
    if x & 1:
        yb = yb[:31] + bytes([yb[31] | 0x80])
    return yb


def _ed_clamp(s):
    s = bytearray(s)
    s[0] &= 248
    s[31] &= 127
    s[31] |= 64
    return bytes(s)


def _ed_pubkey(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(_ed_clamp(h[:32]), "little")
    return _ed_compress(_ed_scalarmult(_ED_G, a))


def _ed_sign(seed: bytes, message: bytes) -> bytes:
    """Sign a message with a 32-byte Ed25519 seed. Returns 64-byte signature."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(_ed_clamp(h[:32]), "little")
    A = _ed_compress(_ed_scalarmult(_ED_G, a))
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), "little") % _ED_L
    R = _ed_compress(_ed_scalarmult(_ED_G, r))
    k = int.from_bytes(hashlib.sha512(R + A + message).digest(), "little") % _ED_L
    S = (r + k * a) % _ED_L
    return R + S.to_bytes(32, "little")


def _generate_ephemeral_keypair() -> Tuple[bytes, bytes]:
    """Generate a fresh Ed25519 keypair. Returns (seed_32_bytes, pubkey_32_bytes).

    Used for the Whirlpool openPosition NFT mint, which must be a fresh keypair
    that co-signs the transaction. The seed is NEVER persisted.
    """
    seed = os.urandom(32)
    return seed, _ed_pubkey(seed)


# ---------------------------------------------------------------------------
# PDA derivations
# ---------------------------------------------------------------------------

def _derive_ata_address(wallet: str, mint: str,
                        token_program: str = SPL_TOKEN_PROGRAM_ID) -> Optional[str]:
    """Derive the Associated Token Account address for (wallet, mint).

    ATA = findProgramAddress([wallet_bytes, token_program_bytes, mint_bytes], ATA_PROGRAM)
    """
    try:
        seeds = [
            _b58decode(wallet),
            _b58decode(token_program),
            _b58decode(mint),
        ]
        pda, _ = _find_program_address(seeds, _b58decode(ATA_PROGRAM_ID))
        return _b58encode(pda) if pda else None
    except (KeyError, ValueError):
        return None


def _derive_tick_array_address(whirlpool: str, start_tick_index: int) -> Optional[str]:
    """Derive the FixedTickArray PDA for a given start_tick_index.

    seeds = [b"tick_array", whirlpool_bytes, str(start_tick_index).encode()]
    """
    try:
        seeds = [
            b"tick_array",
            _b58decode(whirlpool),
            str(start_tick_index).encode("utf-8"),
        ]
        pda, _ = _find_program_address(seeds, _b58decode(WHIRLPOOL_PROGRAM))
        return _b58encode(pda) if pda else None
    except (KeyError, ValueError):
        return None


def _tick_array_start_index(tick: int, tick_spacing: int) -> int:
    """Compute the start_tick_index of the TickArray containing `tick`.

    Each TickArray covers TICK_ARRAY_SIZE * tick_spacing ticks.
    """
    span = TICK_ARRAY_SIZE * tick_spacing
    # Python's // is floor division — handles negatives correctly
    return (tick // span) * span


# ---------------------------------------------------------------------------
# Solana message compilation
# ---------------------------------------------------------------------------

def _compact_u16(n: int) -> bytes:
    """Encode as Solana compact-u16 (LEB128)."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(b)
            break
        out.append(b | 0x80)
    return bytes(out)


class _AccountMeta:
    """One entry in a Solana message's account_keys list."""

    __slots__ = ("pubkey", "is_signer", "is_writable")

    def __init__(self, pubkey: bytes, is_signer: bool, is_writable: bool):
        self.pubkey = pubkey
        self.is_signer = is_signer
        self.is_writable = is_writable


def _compile_message(
    fee_payer: bytes,
    instructions: List[Tuple[bytes, List[_AccountMeta], bytes]],
    recent_blockhash: bytes,
) -> Tuple[bytes, List[bytes]]:
    """Compile a Solana transaction message.

    Args:
        fee_payer: 32-byte pubkey of the fee payer (always first signer, writable).
        instructions: list of (program_id_bytes, account_metas, data_bytes)
            where account_metas are _AccountMeta entries for the instruction.
        recent_blockhash: 32-byte blockhash.

    Returns:
        (message_bytes, ordered_account_pubkeys)

    Message layout (legacy format):
        header:             3 bytes (num_sigs, num_readonly_signed, num_readonly_unsigned)
        account_keys:       compact-u16 count + concat(pubkeys)
        recent_blockhash:   32 bytes
        instructions:       compact-u16 count + serialized instructions

    Account key ORDER required by Solana:
        [signers & writable..., signers & readonly..., non-signers & writable..., non-signers & readonly...]
    """
    # Collect unique accounts preserving (signer,writable) from first occurrence
    # that maximizes privileges (an account referenced writable in one ix is writable everywhere)
    accounts: Dict[bytes, _AccountMeta] = {}

    def _add(pubkey: bytes, is_signer: bool, is_writable: bool):
        if pubkey in accounts:
            existing = accounts[pubkey]
            # Upgrade privileges if needed (OR the flags — a key used as writable
            # in ANY instruction must be writable in the message)
            if is_signer and not existing.is_signer:
                existing.is_signer = True
            if is_writable and not existing.is_writable:
                existing.is_writable = True
        else:
            accounts[pubkey] = _AccountMeta(pubkey, is_signer, is_writable)

    # Fee payer first — always writable signer
    _add(fee_payer, True, True)

    # Add all instruction accounts
    program_ids: List[bytes] = []
    for pid, metas, _data in instructions:
        if pid not in program_ids:
            program_ids.append(pid)
        for m in metas:
            _add(m.pubkey, m.is_signer, m.is_writable)

    # Add program IDs as readonly non-signers
    for pid in program_ids:
        _add(pid, False, False)

    # Order: signer+writable, signer+readonly, nonsigner+writable, nonsigner+readonly
    signer_writable = [m for m in accounts.values() if m.is_signer and m.is_writable]
    signer_readonly = [m for m in accounts.values() if m.is_signer and not m.is_writable]
    nonsigner_writable = [m for m in accounts.values() if not m.is_signer and m.is_writable]
    nonsigner_readonly = [m for m in accounts.values() if not m.is_signer and not m.is_writable]

    # Preserve insertion order within each bucket (accounts dict preserves this in py3.7+)
    bucket_order: Dict[bytes, int] = {m.pubkey: i for i, m in enumerate([
        *signer_writable, *signer_readonly, *nonsigner_writable, *nonsigner_readonly
    ])}
    # Re-sort so fee_payer is FIRST within signer_writable
    signer_writable.sort(key=lambda m: (m.pubkey != fee_payer))

    ordered = signer_writable + signer_readonly + nonsigner_writable + nonsigner_readonly
    account_keys = [m.pubkey for m in ordered]
    account_index = {k: i for i, k in enumerate(account_keys)}

    # Header
    num_sigs = len(signer_writable) + len(signer_readonly)
    num_readonly_signed = len(signer_readonly)
    num_readonly_unsigned = len(nonsigner_readonly)
    header = bytes([num_sigs, num_readonly_signed, num_readonly_unsigned])

    # Account keys section
    keys_section = _compact_u16(len(account_keys)) + b"".join(account_keys)

    # Instructions section
    ix_section = _compact_u16(len(instructions))
    for pid, metas, data in instructions:
        ix_section += bytes([account_index[pid]])
        ix_section += _compact_u16(len(metas))
        for m in metas:
            ix_section += bytes([account_index[m.pubkey]])
        ix_section += _compact_u16(len(data))
        ix_section += data

    message = header + keys_section + recent_blockhash + ix_section
    return message, account_keys


def _assemble_signed_tx(signatures: List[bytes], message: bytes) -> bytes:
    """Assemble a complete signed Solana transaction from ordered signatures.

    signatures[i] corresponds to account_keys[i] (which must all be signers).
    """
    return _compact_u16(len(signatures)) + b"".join(signatures) + message


# ---------------------------------------------------------------------------
# OrcaWriter
# ---------------------------------------------------------------------------

class OrcaWriter(VenueWriter):
    """VenueWriter for Orca Whirlpool LP positions on Solana."""

    VENUE_KEY = "orca"

    def __init__(self, agent_url: str = "http://127.0.0.1:8842"):
        self.agent_url = agent_url.rstrip("/")
        self.rpc_url = SOLANA_RPC_URL
        self._unlocked = False
        self._token_program_cache: Dict[str, str] = {}

    def _get_token_program(self, position_mint: str) -> str:
        """Detect whether the position NFT uses SPL Token or Token-2022.

        Caches the result to avoid repeated RPC calls.
        """
        if position_mint in self._token_program_cache:
            return self._token_program_cache[position_mint]
        prog = _detect_token_program(position_mint)
        self._token_program_cache[position_mint] = prog
        print(f"[orca-writer] position_mint={position_mint[:8]}... uses token program: {prog[:8]}...")
        return prog

    # ------------------------------------------------------------------
    # Agent communication
    # ------------------------------------------------------------------

    def _agent_call(self, cmd: str, **params) -> dict:
        payload = {"cmd": cmd, **params}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.agent_url, data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot reach key_manager_agent at {self.agent_url}. "
                f"Is it running? Error: {e}"
            ) from e
        if result.get("status") != "ok":
            raise RuntimeError(f"Agent error ({cmd}): {result.get('error', 'Unknown')}")
        return result["result"]

    def _get_solana_address(self, account: str) -> str:
        """Get the Solana base58 address for a vault account."""
        result = self._agent_call("get_solana_address", account=account)
        if isinstance(result, dict):
            return result.get("address", "")
        return str(result)

    def is_available(self) -> bool:
        try:
            result = self._agent_call("status")
            unlocked = result.get("unlocked", False) if isinstance(result, dict) else False
            self._unlocked = unlocked
            return unlocked
        except Exception:
            self._unlocked = False
            return False

    def unlock(self, credentials: Dict[str, Any]) -> bool:
        return self.is_available()

    # ------------------------------------------------------------------
    # Solana RPC helpers
    # ------------------------------------------------------------------

    def _rpc(self, method: str, params: list) -> Optional[Any]:
        return _solana_rpc_call(method, params)

    def _get_recent_blockhash(self) -> bytes:
        result = self._rpc("getLatestBlockhash", [{"commitment": "finalized"}])
        if result and isinstance(result, dict):
            bh_b58 = result.get("value", {}).get("blockhash", "")
            if bh_b58:
                return _b58decode(bh_b58)
        raise RuntimeError("Could not get recent blockhash from Solana RPC")

    def _get_position_data(self, position_mint: str) -> Optional[Dict[str, Any]]:
        pos_addr = _derive_position_address(position_mint)
        if not pos_addr:
            return None
        data = _get_account_data(pos_addr)
        if not data or len(data) < POSITION_ACCOUNT_LEN:
            return None
        decoded = _decode_position_data(data)
        if decoded:
            decoded["position_address"] = pos_addr
        return decoded

    def _get_pool_data(self, pool_address: str) -> Optional[Dict[str, Any]]:
        data = _get_account_data(pool_address)
        if not data or len(data) < 269:
            return None
        decoded = _decode_pool_data(data)
        if decoded:
            # Carry the pool's own address + the token vaults (not in the decoded dict)
            decoded["whirlpool"] = pool_address
            decoded["token_vault_a"] = _b58encode(data[133:165])
            decoded["token_vault_b"] = _b58encode(data[213:245])
        return decoded

    def _get_token_balance(self, wallet: str, mint: str) -> int:
        """SPL token balance for a wallet/mint pair (raw integer, 0 if no ATA)."""
        result = self._rpc("getTokenAccountsByOwner", [
            wallet, {"mint": mint}, {"encoding": "jsonParsed"},
        ])
        if result and isinstance(result, dict):
            for entry in result.get("value", []):
                try:
                    amount = entry["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]
                    return int(amount)
                except (KeyError, TypeError, ValueError):
                    continue
        return 0

    def _get_position_token_account(self, wallet: str, position_mint: str) -> Optional[str]:
        """Find the wallet's token account holding the position NFT."""
        result = self._rpc("getTokenAccountsByOwner", [
            wallet, {"mint": position_mint}, {"encoding": "jsonParsed"},
        ])
        if result and isinstance(result, dict):
            for entry in result.get("value", []):
                try:
                    amount = entry["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]
                    if amount == "1":
                        return entry["pubkey"]
                except (KeyError, TypeError):
                    continue
        return None

    def _ensure_ata_ix(self, payer: bytes, wallet: str, mint: str,
                        token_program: str = None) -> Tuple[Optional[Tuple], str]:
        """Return (create_ix_or_None, ata_address) for (wallet, mint).

        If token_program is None, defaults to SPL_TOKEN_PROGRAM_ID.
        If the ATA exists, the instruction is None. Otherwise, an idempotent
        create ATA instruction is returned (modern ATA program: empty data).
        """
        if token_program is None:
            token_program = SPL_TOKEN_PROGRAM_ID
        ata = _derive_ata_address(wallet, mint, token_program)
        if not ata:
            raise RuntimeError(f"Could not derive ATA for wallet={wallet} mint={mint}")
        existing = _get_account_data(ata)
        if existing and len(existing) >= 165:
            return None, ata
        # Idempotent create ATA: program=ATA_PROGRAM, data=empty (no discriminator)
        # accounts: [payer(w,sig), ata(w), owner(r), mint(r), system_program(r), token_program(r)]
        create_data = b""  # createIdempotent
        ix = (
            _b58decode(ATA_PROGRAM_ID),
            [
                _AccountMeta(payer, is_signer=True, is_writable=True),
                _AccountMeta(_b58decode(ata), is_signer=False, is_writable=True),
                _AccountMeta(_b58decode(wallet), is_signer=False, is_writable=False),
                _AccountMeta(_b58decode(mint), is_signer=False, is_writable=False),
                _AccountMeta(_b58decode(SYSTEM_PROGRAM_ID), is_signer=False, is_writable=False),
                _AccountMeta(_b58decode(token_program), is_signer=False, is_writable=False),
            ],
            create_data,
        )
        return ix, ata

    # ------------------------------------------------------------------
    # Instruction builders (account layouts verified against Orca source)
    # ------------------------------------------------------------------

    def _build_collect_fees_ix(self, wallet: str, pos: Dict[str, Any],
                                pool: Dict[str, Any]) -> Tuple[bytes, List[_AccountMeta], bytes]:
        """collectFees instruction.

        Accounts (CollectFees struct, verified order):
          0 whirlpool (mut, no sig)
          1 position_authority (sig, no mut)
          2 position (mut)
          3 position_token_account (no mut)
          4 token_owner_account_a (mut)
          5 token_vault_a (mut)
          6 token_owner_account_b (mut)
          7 token_vault_b (mut)
          8 token_program (no mut, no sig)
        Data: 8-byte discriminator only.
        """
        wallet_b = _b58decode(wallet)
        pos_token_acct = self._get_position_token_account(wallet, pos["position_mint"])
        if not pos_token_acct:
            raise RuntimeError(
                f"Wallet {wallet} does not hold the position NFT for {pos['position_mint']}"
            )
        token_prog_a = self._get_token_program(pool["token_mint_a"])
        token_prog_b = self._get_token_program(pool["token_mint_b"])
        _, ata_a = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
        _, ata_b = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)

        whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
        accounts = [
            _AccountMeta(_b58decode(pos["whirlpool"]), False, True),          # whirlpool
            _AccountMeta(wallet_b, True, False),                              # position_authority
            _AccountMeta(_b58decode(pos["position_address"]), False, True),   # position
            _AccountMeta(_b58decode(pos_token_acct), False, False),           # position_token_account
            _AccountMeta(_b58decode(ata_a), False, True),                     # token_owner_account_a
            _AccountMeta(_b58decode(pool["token_vault_a"]), False, True),     # token_vault_a
            _AccountMeta(_b58decode(ata_b), False, True),                     # token_owner_account_b
            _AccountMeta(_b58decode(pool["token_vault_b"]), False, True),     # token_vault_b
            _AccountMeta(_b58decode(token_prog_a), False, False),             # token_program (pool tokens)
        ]
        return whirlpool_prog, accounts, DISC_COLLECT_FEES

    def _build_modify_liquidity_ix(self, wallet: str, pos: Dict[str, Any],
                                    pool: Dict[str, Any], liquidity_amount: int,
                                    token_limit_a: int, token_limit_b: int,
                                    is_increase: bool) -> Tuple[bytes, List[_AccountMeta], bytes]:
        """Build a ModifyLiquidity instruction (shared by increase + decrease).

        discriminator: increase_liquidity OR decrease_liquidity
        data: disc(8) + liquidity_amount(u128 LE 16) + token_max/min_a(u64 LE 8) + token_max/min_b(u64 LE 8)

        For increase: token_max_a/b are the maximum the user will spend.
        For decrease: token_min_a/b are the minimum the user will accept (slippage floor).

        Accounts (ModifyLiquidity struct, verified order):
          0 whirlpool (mut)
          1 token_program
          2 position_authority (sig)
          3 position (mut)
          4 position_token_account
          5 token_owner_account_a (mut)
          6 token_owner_account_b (mut)
          7 token_vault_a (mut)
          8 token_vault_b (mut)
          9 tick_array_lower (mut)
          10 tick_array_upper (mut)
        """
        wallet_b = _b58decode(wallet)
        pos_token_acct = self._get_position_token_account(wallet, pos["position_mint"])
        if not pos_token_acct:
            raise RuntimeError(
                f"Wallet {wallet} does not hold the position NFT for {pos['position_mint']}"
            )
        token_prog_a = self._get_token_program(pool["token_mint_a"])
        token_prog_b = self._get_token_program(pool["token_mint_b"])
        _, ata_a = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
        _, ata_b = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)

        tick_spacing = pool["tick_spacing"]
        ta_lower_start = _tick_array_start_index(pos["tick_lower"], tick_spacing)
        ta_upper_start = _tick_array_start_index(pos["tick_upper"], tick_spacing)
        ta_lower = _derive_tick_array_address(pos["whirlpool"], ta_lower_start)
        ta_upper = _derive_tick_array_address(pos["whirlpool"], ta_upper_start)
        if not ta_lower or not ta_upper:
            raise RuntimeError("Could not derive tick array addresses")

        disc = DISC_INCREASE_LIQUIDITY if is_increase else DISC_DECREASE_LIQUIDITY
        data = (disc
                + liquidity_amount.to_bytes(16, "little")
                + token_limit_a.to_bytes(8, "little")
                + token_limit_b.to_bytes(8, "little"))

        whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
        accounts = [
            _AccountMeta(_b58decode(pos["whirlpool"]), False, True),
            _AccountMeta(_b58decode(token_prog_a), False, False),             # token_program (pool tokens)
            _AccountMeta(wallet_b, True, False),
            _AccountMeta(_b58decode(pos["position_address"]), False, True),
            _AccountMeta(_b58decode(pos_token_acct), False, False),
            _AccountMeta(_b58decode(ata_a), False, True),
            _AccountMeta(_b58decode(ata_b), False, True),
            _AccountMeta(_b58decode(pool["token_vault_a"]), False, True),
            _AccountMeta(_b58decode(pool["token_vault_b"]), False, True),
            _AccountMeta(_b58decode(ta_lower), False, True),
            _AccountMeta(_b58decode(ta_upper), False, True),
        ]
        return whirlpool_prog, accounts, data

    def _build_close_position_ix(self, wallet: str, pos: Dict[str, Any],
                                  ) -> Tuple[bytes, List[_AccountMeta], bytes]:
        """closePosition instruction.

        Accounts (ClosePosition struct, verified order):
          0 position_authority (sig)
          1 receiver (mut)
          2 position (mut)
          3 position_mint (mut)
          4 position_token_account (mut)
          5 token_program
        Data: 8-byte discriminator only.
        """
        wallet_b = _b58decode(wallet)
        pos_token_acct = self._get_position_token_account(wallet, pos["position_mint"])
        if not pos_token_acct:
            raise RuntimeError(
                f"Wallet {wallet} does not hold the position NFT for {pos['position_mint']}"
            )
        token_prog = self._get_token_program(pos["position_mint"])
        whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
        accounts = [
            _AccountMeta(wallet_b, True, False),
            _AccountMeta(wallet_b, False, True),                              # receiver
            _AccountMeta(_b58decode(pos["position_address"]), False, True),
            _AccountMeta(_b58decode(pos["position_mint"]), False, True),
            _AccountMeta(_b58decode(pos_token_acct), False, True),
            _AccountMeta(_b58decode(token_prog), False, False),
        ]
        return whirlpool_prog, accounts, DISC_CLOSE_POSITION

    def _build_open_position_ix(self, wallet: str, pool: Dict[str, Any],
                                 tick_lower: int, tick_upper: int,
                                 new_mint_pubkey: bytes) -> Tuple[bytes, List[_AccountMeta], bytes]:
        """openPosition instruction. The position_mint is a fresh ephemeral keypair.

        Accounts (OpenPosition struct, verified order):
          0 funder (sig, mut)
          1 owner (no sig, no mut)
          2 position (mut, PDA)
          3 position_mint (mut, sig — the fresh keypair)
          4 position_token_account (mut, PDA)
          5 whirlpool
          6 token_program
          7 system_program
          8 rent sysvar
          9 associated_token_program

        Data: disc(8) + bumps (we send the position bump) + tick_lower(i32 LE) + tick_upper(i32 LE)

        Note: Orca's openPosition takes OpenPositionBumps as the first arg after
        the discriminator. Position bump is the bump from _derive_position_address.
        """
        wallet_b = _b58decode(wallet)
        new_mint_b58 = _b58encode(new_mint_pubkey)

        pos_addr = _derive_position_address(new_mint_b58)
        if not pos_addr:
            raise RuntimeError("Could not derive new position address")
        # derive position PDA bump
        seeds = [b"position", new_mint_pubkey]
        _, bump = _find_program_address(seeds, _b58decode(WHIRLPOOL_PROGRAM))
        if bump is None:
            raise RuntimeError("Could not compute position PDA bump")
        # ATA for the new position NFT (owner = wallet) — new mints are always SPL Token
        pos_ata = _derive_ata_address(wallet, new_mint_b58, SPL_TOKEN_PROGRAM_ID)
        if not pos_ata:
            raise RuntimeError("Could not derive position ATA")

        # openPosition args: OpenPositionBumps { position_bump: u8 }, tick_lower: i32, tick_upper: i32
        data = (DISC_OPEN_POSITION
                + bytes([bump])
                + struct.pack("<i", tick_lower)
                + struct.pack("<i", tick_upper))

        whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
        accounts = [
            _AccountMeta(wallet_b, True, True),                               # funder
            _AccountMeta(wallet_b, False, False),                             # owner
            _AccountMeta(_b58decode(pos_addr), False, True),                  # position
            _AccountMeta(new_mint_pubkey, True, True),                        # position_mint (co-signer)
            _AccountMeta(_b58decode(pos_ata), False, True),                   # position_token_account
            _AccountMeta(_b58decode(pool["whirlpool"]), False, False),        # whirlpool
            _AccountMeta(_b58decode(SPL_TOKEN_PROGRAM_ID), False, False),     # new position mints use SPL Token
            _AccountMeta(_b58decode(SYSTEM_PROGRAM_ID), False, False),
            _AccountMeta(_b58decode(RENT_SYSVAR), False, False),
            _AccountMeta(_b58decode(ATA_PROGRAM_ID), False, False),
        ]
        return whirlpool_prog, accounts, data

    def _build_swap_ix(self, wallet: str, pool: Dict[str, Any], pool_address: str,
                        amount: int, threshold: int, sqrt_price_limit: int,
                        amount_specified_is_input: bool, a_to_b: bool) -> Tuple[bytes, List[_AccountMeta], bytes]:
        """swap instruction. tick_array_0/1/2 are the three sequential tick arrays starting
        from the array containing the current tick (in swap direction).

        Accounts (Swap struct, verified order):
          0 token_program
          1 token_authority (sig, no mut)
          2 whirlpool (mut)
          3 token_owner_account_a (mut)
          4 token_vault_a (mut)
          5 token_owner_account_b (mut)
          6 token_vault_b (mut)
          7 tick_array_0 (mut)
          8 tick_array_1 (mut)
          9 tick_array_2 (mut)
          10 oracle (PDA, no mut)
        Data: disc(8) + amount(u64) + other_amount_threshold(u64) + sqrt_price_limit(u128)
              + amount_specified_is_input(u8) + a_to_b(u8)
        """
        wallet_b = _b58decode(wallet)
        # Detect token program per-mint for pool tokens (some pools use Token-2022)
        token_prog_a = self._get_token_program(pool["token_mint_a"])
        token_prog_b = self._get_token_program(pool["token_mint_b"])
        _, ata_a = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
        _, ata_b = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)

        tick_spacing = pool["tick_spacing"]
        current_tick = pool["tick_current"]
        ta0_start = _tick_array_start_index(current_tick, tick_spacing)
        span = TICK_ARRAY_SIZE * tick_spacing
        if a_to_b:
            # swapping A->B, price moves DOWN: tick arrays in decreasing order
            ta1_start = ta0_start - span
            ta2_start = ta0_start - 2 * span
        else:
            ta1_start = ta0_start + span
            ta2_start = ta0_start + 2 * span

        ta0 = _derive_tick_array_address(pool_address, ta0_start)
        ta1 = _derive_tick_array_address(pool_address, ta1_start)
        ta2 = _derive_tick_array_address(pool_address, ta2_start)

        # Oracle PDA: seeds = [b"oracle", whirlpool]
        oracle_seeds = [b"oracle", _b58decode(pool_address)]
        oracle_pda, _ = _find_program_address(oracle_seeds, _b58decode(WHIRLPOOL_PROGRAM))
        oracle_b58 = _b58encode(oracle_pda) if oracle_pda else pool_address

        data = (DISC_SWAP
                + amount.to_bytes(8, "little")
                + threshold.to_bytes(8, "little")
                + sqrt_price_limit.to_bytes(16, "little")
                + bytes([1 if amount_specified_is_input else 0])
                + bytes([1 if a_to_b else 0]))

        whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
        accounts = [
            _AccountMeta(_b58decode(token_prog_a), False, False),
            _AccountMeta(wallet_b, True, False),
            _AccountMeta(_b58decode(pool_address), False, True),
            _AccountMeta(_b58decode(ata_a), False, True),
            _AccountMeta(_b58decode(pool["token_vault_a"]), False, True),
            _AccountMeta(_b58decode(ata_b), False, True),
            _AccountMeta(_b58decode(pool["token_vault_b"]), False, True),
            _AccountMeta(_b58decode(ta0), False, True),
            _AccountMeta(_b58decode(ta1), False, True),
            _AccountMeta(_b58decode(ta2), False, True),
            _AccountMeta(_b58decode(oracle_b58), False, False),
        ]
        return whirlpool_prog, accounts, data

    # ------------------------------------------------------------------
    # Sign + broadcast
    # ------------------------------------------------------------------

    def _sign_and_broadcast_single(self, account: str,
                                   instructions: List[Tuple[bytes, List[_AccountMeta], bytes]],
                                   extra_instructions: Optional[List] = None) -> str:
        """Single-signer flow: compile message, hand to agent for full sign+broadcast.

        extra_instructions are prepended (e.g., ATA creation).
        """
        wallet = self._get_solana_address(account)
        wallet_b = _b58decode(wallet)
        blockhash = self._get_recent_blockhash()

        all_ixs = list(extra_instructions or []) + list(instructions)
        message, _accounts = _compile_message(wallet_b, all_ixs, blockhash)

        message_b64 = base64.b64encode(message).decode()
        result = self._agent_call(
            "broadcast_solana_tx",
            account=account,
            message_b64=message_b64,
            rpc_url=self.rpc_url,
        )
        tx_sig = result.get("tx_signature", "")
        if not tx_sig:
            raise RuntimeError(f"Broadcast failed: {result}")
        return tx_sig

    def _sign_and_broadcast_with_ephemeral(self, account: str,
                                            instructions: List[Tuple[bytes, List[_AccountMeta], bytes]],
                                            ephemeral_seeds: List[bytes]) -> str:
        """Multi-signer flow: wallet + one or more ephemeral keypairs.

        The agent produces the wallet's signature; the writer produces signatures
        for ephemeral keys (local pure-Python, no agent). Writer assembles the
        final signed transaction and posts it to broadcast_raw_solana_tx.
        """
        wallet = self._get_solana_address(account)
        wallet_b = _b58decode(wallet)
        blockhash = self._get_recent_blockhash()

        message, account_keys = _compile_message(wallet_b, instructions, blockhash)
        message_b64 = base64.b64encode(message).decode()

        # Ask the agent for JUST the wallet's signature
        result = self._agent_call(
            "sign_solana_tx_no_broadcast",
            account=account,
            message_b64=message_b64,
        )
        wallet_sig = base64.b64decode(result["signature_b64"])

        # Sign with each ephemeral key
        ephemeral_sigs = {(_ed_pubkey(s)): _ed_sign(s, message) for s in ephemeral_seeds}

        # Order signatures per account_keys: [fee_payer(=wallet), ephemeral_signers...]
        sig_map = {wallet_b: wallet_sig}
        sig_map.update(ephemeral_sigs)
        # account_keys[0:num_signers] are all the signers in order
        num_sigs = message[0]
        ordered_sigs = [sig_map[k] for k in account_keys[:num_sigs]]

        signed_tx = _assemble_signed_tx(ordered_sigs, message)
        signed_b64 = base64.b64encode(signed_tx).decode()

        result = self._agent_call(
            "broadcast_raw_solana_tx",
            signed_tx_b64=signed_b64,
            rpc_url=self.rpc_url,
        )
        tx_sig = result.get("tx_signature", "")
        if not tx_sig:
            raise RuntimeError(f"Broadcast failed: {result}")
        return tx_sig

    def _wait_for_confirmation(self, tx_signature: str, timeout: int = 30,
                               poll_interval: float = 2.0):
        """Block until a Solana transaction confirms or times out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._rpc("getSignatureStatuses", [[tx_signature]])
            if result and isinstance(result, dict):
                statuses = result.get("value", [None])
                if statuses and statuses[0]:
                    status = statuses[0]
                    if status.get("confirmationStatus") in ("confirmed", "finalized"):
                        if status.get("err"):
                            raise RuntimeError(f"Transaction failed: {status['err']}")
                        return
            time.sleep(poll_interval)
        raise RuntimeError(f"Transaction {tx_signature} not confirmed within {timeout}s")

    # ------------------------------------------------------------------
    # VenueWriter interface — single-signer flows
    # ------------------------------------------------------------------

    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued trading fees from an Orca Whirlpool position.

        position_id format: "solana:<position_mint>".
        Returns the transaction signature (base58).
        """
        position_mint = params.position_id.split(":", 1)[1] if ":" in params.position_id else params.position_id
        wallet = self._get_solana_address(params.account)
        wallet_b = _b58decode(wallet)

        pos = self._get_position_data(position_mint)
        if not pos:
            raise RuntimeError(f"Could not read position {position_mint}")
        pool = self._get_pool_data(pos["whirlpool"])
        if not pool:
            raise RuntimeError(f"Could not read pool {pos['whirlpool']}")

        # Ensure user has ATAs for both pool tokens (creates them if missing)
        token_prog_a = self._get_token_program(pool["token_mint_a"])
        token_prog_b = self._get_token_program(pool["token_mint_b"])
        extra_ixs = []
        ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
        if ata_ix_a:
            extra_ixs.append(ata_ix_a)
        ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)
        if ata_ix_b:
            extra_ixs.append(ata_ix_b)

        collect_ix = self._build_collect_fees_ix(wallet, pos, pool)
        tx_sig = self._sign_and_broadcast_single(params.account, [collect_ix],
                                                  extra_instructions=extra_ixs)
        print(f"[orca-writer] collect_fees: position={position_mint[:8]}... tx={tx_sig}")
        return tx_sig

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close an Orca Whirlpool position: decrease liquidity (100%) → collectFees → closePosition.

        Returns a list of transaction signatures, in order.
        """
        position_mint = position_id.split(":", 1)[1] if ":" in position_id else position_id
        wallet = self._get_solana_address(account)
        wallet_b = _b58decode(wallet)

        pos = self._get_position_data(position_mint)
        if not pos:
            raise RuntimeError(f"Could not read position {position_mint}")
        pool = self._get_pool_data(pos["whirlpool"])
        if not pool:
            raise RuntimeError(f"Could not read pool {pos['whirlpool']}")

        tx_hashes: List[str] = []

        # Ensure ATAs exist before any token transfer — detect per-mint for pool tokens
        token_prog_a = self._get_token_program(pool["token_mint_a"])
        token_prog_b = self._get_token_program(pool["token_mint_b"])
        extra_ixs = []
        ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
        if ata_ix_a:
            extra_ixs.append(ata_ix_a)
        ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)
        if ata_ix_b:
            extra_ixs.append(ata_ix_b)

        # Step 1: decreaseLiquidity to zero (skip if already empty)
        if pos["liquidity"] > 0:
            decrease_ix = self._build_modify_liquidity_ix(
                wallet, pos, pool,
                liquidity_amount=pos["liquidity"],
                token_limit_a=0, token_limit_b=0,   # token_min_a/b = 0 (no slippage floor)
                is_increase=False,
            )
            tx1 = self._sign_and_broadcast_single(account, [decrease_ix],
                                                   extra_instructions=extra_ixs)
            tx_hashes.append(tx1)
            self._wait_for_confirmation(tx1, timeout=45)
            # After decrease, the ATA-creation extras are consumed; clear for subsequent TXs
            extra_ixs = []
            print(f"[orca-writer] decrease ok: {tx1}")

        # Step 2: collectFees — claim everything to the wallet
        # Ensure ATAs exist (may have been skipped if liquidity was 0 and step 1 didn't run)
        if not extra_ixs:
            ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
            ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)
            extra_ixs = []
            if ata_ix_a:
                extra_ixs.append(ata_ix_a)
            if ata_ix_b:
                extra_ixs.append(ata_ix_b)

        collect_ix = self._build_collect_fees_ix(wallet, pos, pool)
        tx2 = self._sign_and_broadcast_single(account, [collect_ix],
                                               extra_instructions=extra_ixs)
        tx_hashes.append(tx2)
        self._wait_for_confirmation(tx2, timeout=45)
        extra_ixs = []  # Clear for subsequent transactions
        print(f"[orca-writer] collectFees ok: {tx2}")

        # Step 3: closePosition — burns the NFT, recovers rent
        close_ix = self._build_close_position_ix(wallet, pos)
        tx3 = self._sign_and_broadcast_single(account, [close_ix])
        tx_hashes.append(tx3)
        print(f"[orca-writer] closePosition ok: {tx3}")

        return tx_hashes

    # ------------------------------------------------------------------
    # Rebalance — multi-signer flow
    # ------------------------------------------------------------------

    def rebalance(self, params: RebalanceParams) -> List[str]:
        """Full rebalance on Orca Whirlpool: close → optional swap → openPosition at new range.

        The openPosition step requires a second signer (the fresh NFT mint keypair)
        in addition to the wallet. The writer generates and signs with the fresh
        keypair locally; the wallet's signature comes from the agent.

        Returns a list of TX signatures. The last signature is the openPosition TX
        which creates the new position NFT (the position_id changes after rebalance).
        """
        position_mint = params.position_id.split(":", 1)[1] if ":" in params.position_id else params.position_id
        wallet = self._get_solana_address(params.account)
        wallet_b = _b58decode(wallet)

        pos = self._get_position_data(position_mint)
        if not pos:
            raise RuntimeError(f"Could not read position {position_mint}")
        pool = self._get_pool_data(pos["whirlpool"])
        if not pool:
            raise RuntimeError(f"Could not read pool {pos['whirlpool']}")

        pool_address = pos["whirlpool"]
        mint_a = pool["token_mint_a"]
        mint_b = pool["token_mint_b"]
        decimals_a = _get_sol_token_decimals(mint_a)
        decimals_b = _get_sol_token_decimals(mint_b)

        tx_hashes: List[str] = []

        # Steps 1-2: close the current position
        close_txs = self.close_position(position_mint, params.account)
        tx_hashes.extend(close_txs)

        # Give the RPC a moment to settle
        time.sleep(2.0)

        # Step 3: read post-close balances
        bal_a = self._get_token_balance(wallet, mint_a)
        bal_b = self._get_token_balance(wallet, mint_b)
        print(f"[orca-rebalance] post-close balances: bal_a={bal_a} bal_b={bal_b}")

        if bal_a == 0 and bal_b == 0:
            print("[orca-rebalance] nothing to re-add — aborting before swap/mint")
            return tx_hashes

        # Step 4: compute optimal swap for the new range
        current_tick = pool["tick_current"]
        sqrt_price_x64 = pool["sqrt_price"]
        sqrt_price = sqrt_price_x64 / (2 ** 64)

        swap_ix = self._compute_swap_for_rebalance(
            wallet=wallet,
            pool=pool,
            pool_address=pool_address,
            bal_a=bal_a,
            bal_b=bal_b,
            decimals_a=decimals_a,
            decimals_b=decimals_b,
            sqrt_price=sqrt_price,
            current_tick=current_tick,
            new_tick_lower=params.new_tick_lower,
            new_tick_upper=params.new_tick_upper,
            slippage_pct=params.slippage_pct,
        )
        if swap_ix is not None:
            tx_swap = self._sign_and_broadcast_single(params.account, [swap_ix])
            tx_hashes.append(tx_swap)
            self._wait_for_confirmation(tx_swap, timeout=45)
            time.sleep(2.0)
            # Re-read balances post-swap
            bal_a = self._get_token_balance(wallet, mint_a)
            bal_b = self._get_token_balance(wallet, mint_b)
            print(f"[orca-rebalance] post-swap balances: bal_a={bal_a} bal_b={bal_b}")

        # Step 5: open a new position at the new range
        # The NFT mint is a fresh ephemeral keypair
        mint_seed, mint_pubkey = _generate_ephemeral_keypair()

        open_ix = self._build_open_position_ix(
            wallet, pool, params.new_tick_lower, params.new_tick_upper, mint_pubkey
        )
        # openPosition uses ephemeral mint signer + wallet
        tx_open = self._sign_and_broadcast_with_ephemeral(
            params.account, [open_ix], ephemeral_seeds=[mint_seed]
        )
        tx_hashes.append(tx_open)
        self._wait_for_confirmation(tx_open, timeout=45)
        print(f"[orca-rebalance] openPosition ok: {tx_open} new_mint={_b58encode(mint_pubkey)}")

        # Step 6: add liquidity to the new position via increase_liquidity
        # We need to read the new position address first
        new_mint_b58 = _b58encode(mint_pubkey)
        new_pos_addr = _derive_position_address(new_mint_b58)
        new_pos_data_raw = _get_account_data(new_pos_addr) if new_pos_addr else None
        if not new_pos_data_raw:
            print("[orca-rebalance] could not read new position — skipping increase_liquidity")
            return tx_hashes
        new_pos = _decode_position_data(new_pos_data_raw)
        if not new_pos:
            print("[orca-rebalance] could not decode new position — skipping increase_liquidity")
            return tx_hashes
        new_pos["position_address"] = new_pos_addr

        # Compute liquidity amount for the new range from current balances
        # Using the same V3 math the EVM adapters use.
        tick_lo = params.new_tick_lower
        tick_hi = params.new_tick_upper
        sqrt_lo = 1.0001 ** (tick_lo / 2.0)
        sqrt_hi = 1.0001 ** (tick_hi / 2.0)

        bal_a_h = bal_a / (10 ** decimals_a)
        bal_b_h = bal_b / (10 ** decimals_b)

        if tick_lo <= current_tick < tick_hi:
            liq_a = bal_a_h * sqrt_price * sqrt_hi / (sqrt_hi - sqrt_price)
            liq_b = bal_b_h / (sqrt_price - sqrt_lo)
            liquidity = int(min(liq_a, liq_b))
        elif current_tick < tick_lo:
            liquidity = int(bal_a_h * sqrt_price * sqrt_hi / (sqrt_price * (sqrt_hi - sqrt_lo) / sqrt_lo))
        else:
            liquidity = int(bal_b_h / (sqrt_hi - sqrt_lo))

        if liquidity <= 0:
            print("[orca-rebalance] computed liquidity=0 — position opened but empty")
            return tx_hashes

        # Slippage on increase: token_max_a/b are the amounts we're willing to spend
        if params.slippage_pct > 0:
            max_a = int(bal_a * (1 + params.slippage_pct / 100.0))
            max_b = int(bal_b * (1 + params.slippage_pct / 100.0))
        else:
            max_a = bal_a
            max_b = bal_b

        increase_ix = self._build_modify_liquidity_ix(
            wallet, new_pos, pool,
            liquidity_amount=liquidity,
            token_limit_a=max_a, token_limit_b=max_b,
            is_increase=True,
        )
        tx_inc = self._sign_and_broadcast_single(params.account, [increase_ix])
        tx_hashes.append(tx_inc)
        print(f"[orca-rebalance] increaseLiquidity ok: {tx_inc}")

        return tx_hashes

    def _compute_swap_for_rebalance(
        self,
        wallet: str,
        pool: Dict[str, Any],
        pool_address: str,
        bal_a: int,
        bal_b: int,
        decimals_a: int,
        decimals_b: int,
        sqrt_price: float,
        current_tick: int,
        new_tick_lower: int,
        new_tick_upper: int,
        slippage_pct: float,
    ) -> Optional[Tuple[bytes, List[_AccountMeta], bytes]]:
        """Compute whether a swap is needed and build the swap instruction if so.

        Returns None if balances are already in the right ratio.
        """
        sqrt_lo = 1.0001 ** (new_tick_lower / 2.0)
        sqrt_hi = 1.0001 ** (new_tick_upper / 2.0)

        # Out-of-range extremes
        if current_tick >= new_tick_upper:
            # Need 100% token A
            if bal_b == 0:
                return None
            amount = bal_b
            a_to_b = False  # swap B->A
            threshold = 0  # other_amount_threshold
            sqrt_price_limit = 0  # no limit
            return self._build_swap_ix(wallet, pool, pool_address,
                                       amount=amount, threshold=threshold,
                                       sqrt_price_limit=sqrt_price_limit,
                                       amount_specified_is_input=True,
                                       a_to_b=a_to_b)
        if current_tick < new_tick_lower:
            if bal_a == 0:
                return None
            amount = bal_a
            return self._build_swap_ix(wallet, pool, pool_address,
                                       amount=amount, threshold=0, sqrt_price_limit=0,
                                       amount_specified_is_input=True, a_to_b=True)

        # In-range: figure out which token is excess
        bal_a_h = bal_a / (10 ** decimals_a)
        bal_b_h = bal_b / (10 ** decimals_b)

        if sqrt_price <= sqrt_lo or sqrt_price >= sqrt_hi:
            return None

        liq_a = bal_a_h * sqrt_price * sqrt_hi / (sqrt_hi - sqrt_price)
        liq_b = bal_b_h / (sqrt_price - sqrt_lo)

        if liq_a <= 0 and liq_b <= 0:
            return None
        if abs(liq_a - liq_b) / max(liq_a, liq_b) < 0.01:
            return None

        if liq_a > liq_b:
            # Excess token A — sell some A for B
            numerator = liq_a - liq_b
            coeff_a = sqrt_price * sqrt_hi / (sqrt_hi - sqrt_price)
            coeff_b = (sqrt_price ** 2) / (sqrt_price - sqrt_lo)
            x = numerator / (coeff_a + coeff_b)
            if x <= 0 or x >= bal_a_h:
                return None
            amount = int(x * (10 ** decimals_a))
            if amount <= 0:
                return None
            if slippage_pct > 0:
                est_out = int(amount * sqrt_price * sqrt_price * (10 ** (decimals_b - decimals_a)))
                threshold = int(est_out * (1 - slippage_pct / 100.0))
            else:
                threshold = 0
            return self._build_swap_ix(wallet, pool, pool_address,
                                       amount=amount, threshold=threshold,
                                       sqrt_price_limit=0,
                                       amount_specified_is_input=True, a_to_b=True)
        else:
            # Excess token B — sell some B for A
            numerator = liq_b - liq_a
            coeff_b = 1.0 / (sqrt_price - sqrt_lo)
            coeff_a = sqrt_price * sqrt_hi / (sqrt_hi - sqrt_price) / (sqrt_price ** 2)
            x = numerator / (coeff_a + coeff_b)
            if x <= 0 or x >= bal_b_h:
                return None
            amount = int(x * (10 ** decimals_b))
            if amount <= 0:
                return None
            if slippage_pct > 0:
                est_out = int(amount / (sqrt_price * sqrt_price) * (10 ** (decimals_a - decimals_b)))
                threshold = int(est_out * (1 - slippage_pct / 100.0))
            else:
                threshold = 0
            return self._build_swap_ix(wallet, pool, pool_address,
                                       amount=amount, threshold=threshold,
                                       sqrt_price_limit=0,
                                       amount_specified_is_input=True, a_to_b=False)

    # ------------------------------------------------------------------
    # Stubs for operations not applicable to Orca
    # ------------------------------------------------------------------

    def wrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("Solana native wrapping is via WSOL ATA, not applicable to Orca")

    def unwrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("See wrap_native")

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        raise NotImplementedError("Solana uses account permissions, not ERC-20 approvals")

    def swap(self, params: SwapParams) -> str:
        raise NotImplementedError("Use rebalance() which handles swap internally")

    def open_position(self, params: OpenPositionParams) -> tuple:
        raise NotImplementedError("Use rebalance() which opens via openPosition + increaseLiquidity")

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        raise NotImplementedError("Not yet implemented as a standalone op for Orca")

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        raise NotImplementedError("Use close_position() which decreases to zero")

    def compound_fees(self, params: CompoundFeesParams) -> List[str]:
        raise NotImplementedError("Not yet implemented for Orca — use collect_fees + rebalance")
