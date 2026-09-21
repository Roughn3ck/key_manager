"""Golden-layout tests for Orca closePosition / collectFees builders.

Asserts the exact account list (order + signer/writable flags) and discriminator
bytes produced for (a) a classic-SPL-NFT position and (b) a Token-2022-NFT
position, using the pinned whirlpools IDL structs as the fixture. Catches the
v5.3.13 IllegalOwner regression class (wrong token program / wrong discriminator
on a Token-2022 position NFT) without touching the network.

Run:  python test_orca_close_layout.py
"""
import sys
import tempfile
import hashlib
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters import orca_writer as ow  # noqa: E402
from venue_adapters import orca_adapter as oa  # noqa: E402
from venue_adapters.orca_adapter import (  # noqa: E402
    SPL_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID, _b58encode,
)

CLASSIC = SPL_TOKEN_PROGRAM_ID
TE = TOKEN_2022_PROGRAM_ID
PROG = ow.WHIRLPOOL_PROGRAM

# Pinned from the IDL (cdn.jsdelivr @orca-so/whirlpools-sdk whirlpool.json).
DISC = {
    "close_position": "7b86510031446262",
    "close_position_with_token_extensions": "01b6873b9b1963df",
    "collect_fees": "a498cf631eba13b6",
    "decrease_liquidity": "a026d06f685b2c01",
}

WALLET = "So11111111111111111111111111111111111111112"  # any 32-byte key works for layout
POS_ADDR = "F98SmNgmft21dRAwfXGPtWu95Kb1WcSm58WgaQzUZQQR"
POS_MINT = "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"
POS_ATA = "7bEGyi7MzzK3aWwQm6mT8cJbQv9yYw1mQv1mQv1mQv1m"
# Real, decodable owner-token accounts (ATAs) so base58 round-trips succeed.
ATA_SOL = "DYkz5CCMUshPUso6Eg25f2kw5cnexYAKSrN6ZMSPM2xS"
ATA_CBBTC = "7G26tTFk7VhpqhnRHqvgsWh7NhVeLj7cGNCXs9PWfVR3"


def _mk_writer():
    w = ow.OrcaWriter.__new__(ow.OrcaWriter)  # bypass __init__ (no agent / no RPC)
    w._token_program_cache = {}
    return w


def _pos():
    return {"position_address": POS_ADDR, "position_mint": POS_MINT,
            "whirlpool": "CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN"}


def _pool():
    return {
        "token_mint_a": "So11111111111111111111111111111111111111112",
        "token_mint_b": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
        "token_vault_a": "DYkz5CCMUshPUso6Eg25f2kw5cnexYAKSrN6ZPSMAAAA",
        "token_vault_b": "7G26tTFk7VhpqhnRHqvgsWh7NhVeLj7cGNCXs9PWAAAT",
    }


def _stub(w, token_prog):
    """Deterministically stub RPC-dependent helpers for layout testing."""
    w._get_position_token_account = lambda wallet, mint: POS_ATA
    w._get_token_program = lambda mint: (
        token_prog if mint == POS_MINT else _POOL_TOK_PROGS.get(mint, CLASSIC)
    )
    ata_map = {"So11111111111111111111111111111111111111112": ATA_SOL,
               "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": ATA_CBBTC}
    w._ensure_ata_ix = lambda payer, wallet, mint, token_program=None: (None, ata_map[mint])


_POOL_TOK_PROGS = {
    "So11111111111111111111111111111111111111112": CLASSIC,
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": CLASSIC,
}


def main() -> int:
    # --- close_position, classic NFT ---
    w = _mk_writer(); _stub(w, CLASSIC)
    prog, accts, disc = ow.OrcaWriter._build_close_position_ix(w, WALLET, _pos())
    assert prog == ow._b58decode(PROG)
    assert disc.hex() == DISC["close_position"], disc.hex()
    expect = [
        (WALLET, True, False),   # position_authority
        (WALLET, False, True),   # receiver
        (POS_ADDR, False, True), # position
        (POS_MINT, False, True), # position_mint
        (POS_ATA, False, True),  # position_token_account
        (CLASSIC, False, False), # token_program
    ]
    got = [(ow._b58encode(bytes(a.pubkey) if isinstance(a.pubkey, (bytes, bytearray)) else a.pubkey),
            a.is_signer, a.is_writable) for a in accts]
    for i, (e_addr, e_sig, e_wr) in enumerate(expect):
        g_addr, g_sig, g_wr = got[i]
        assert g_sig == e_sig and g_wr == e_wr, f"close classic acct{i} flags"
        assert g_addr == e_addr, f"close classic acct{i} addr {g_addr} != {e_addr}"

    # --- close_position_with_token_extensions, Token-2022 NFT ---
    w = _mk_writer(); _stub(w, TE)
    prog, accts, disc = ow.OrcaWriter._build_close_position_ix(w, WALLET, _pos())
    assert disc.hex() == DISC["close_position_with_token_extensions"], disc.hex()
    got5 = got = [(ow._b58encode(a.pubkey if isinstance(a.pubkey, (bytes, bytearray)) else a.pubkey),
                   a.is_signer, a.is_writable) for a in accts]
    # account 5 must be the Token-2022 program id
    assert got5[5][0] == TE, got5[5]
    # same order/flags as classic for the first five accounts
    assert got5[:5] == [
        (WALLET, True, False), (WALLET, False, True), (POS_ADDR, False, True),
        (POS_MINT, False, True), (POS_ATA, False, True),
    ], got5[:5]

    # --- collect_fees (9 accounts, classic token program, disc a498...) ---
    w = _mk_writer(); _stub(w, TE)
    prog, accts, disc = ow.OrcaWriter._build_collect_fees_ix(w, WALLET, _pos(), _pool())
    assert disc.hex() == DISC["collect_fees"], disc.hex()
    assert len(accts) == 9, len(accts)
    flags = [(a.is_signer, a.is_writable) for a in accts]
    assert flags == [
        (False, True),   # whirlpool
        (True, False),   # position_authority
        (False, True),   # position
        (False, False),  # position_token_account
        (False, True),   # token_owner_account_a
        (False, True),   # token_vault_a
        (False, True),   # token_owner_account_b
        (False, True),   # token_vault_b
        (False, False),  # token_program
    ], flags
    # position_token_account is the TE ATA; program slot is the classic pool program
    addrs = [ow._b58encode(a.pubkey if isinstance(a.pubkey, (bytes, bytearray)) else a.pubkey) for a in accts]
    assert addrs[3] == POS_ATA
    assert addrs[8] == CLASSIC

    # --- collect_reward (8 accounts, classic token program, disc collect_reward) ---
    disc_reward = ow.DISC_COLLECT_REWARD.hex()
    # computed: sha256("global:collect_reward")[:8] — pin the literal
    import hashlib as _h
    assert disc_reward == _h.sha256(b"global:collect_reward").digest()[:8].hex()
    w = _mk_writer(); _stub(w, TE)
    pool = _pool()
    pool["reward_infos"] = [
        {"mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1ZktXjY",
         "vault": "Cqj7caoFayYa26ApYfwg3AB3tjPkhzJa93SncMPcULNq",
         "authority": "6gY7e1vChFTkBPb5xUxEXFenAjAgZuwA44ZrLnKTd1Ec", "initialized": True},
        {"mint": ATA_SOL, "vault": ATA_CBBTC, "authority": WALLET, "initialized": False},
        {"mint": ATA_SOL, "vault": ATA_CBBTC, "authority": WALLET, "initialized": False},
    ]
    w2 = _mk_writer()
    w2._get_position_token_account = lambda a, b: POS_ATA
    w2._get_token_program = lambda mint: CLASSIC  # ORCA reward mint is classic SPL
    w2._ensure_ata_ix = lambda payer, wallet, mint, token_program=None: (None, ATA_SOL)
    prog, accts, data, ataix = ow.OrcaWriter._build_collect_reward_ix(
        w2, WALLET, _pos(), pool, 0)
    assert prog == ow._b58decode(PROG)
    assert data[:8].hex() == disc_reward, data[:8].hex()
    assert data[8] == 0, "reward_index 0"
    assert len(accts) == 7, len(accts)
    flags = [(a.is_signer, a.is_writable) for a in accts]
    assert flags == [
        (False, True),   # whirlpool
        (True, False),   # position_authority
        (False, True),   # position
        (False, False),  # position_token_account
        (False, True),   # reward_owner_account
        (False, True),   # reward_vault
        (False, False),  # token_program
    ], flags
    raddrs = [ow._b58encode(a.pubkey if isinstance(a.pubkey, (bytes, bytearray)) else a.pubkey)
              for a in accts]
    assert raddrs[4] == ATA_SOL  # reward owner ATA (stubbed)
    assert raddrs[5] == "Cqj7caoFayYa26ApYfwg3AB3tjPkhzJa93SncMPcULNq"  # reward vault
    assert raddrs[6] == CLASSIC  # classic token program

    # --- 2.x position-bytes decode fixture (216 bytes) ---
    import struct as _st
    pos_bytes = bytearray(216)
    pos_bytes[0:8] = bytes.fromhex("aabc8fe47a40f7d0")  # Position discriminator
    pos_bytes[8:40] = ow._b58decode("CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN")
    pos_bytes[40:72] = ow._b58decode("FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX")
    # liquidity 0 at 72..88 (already zero)
    _st.pack_into("<i", pos_bytes, 88, -91040)
    _st.pack_into("<i", pos_bytes, 92, -89024)
    # fee checkpoints 96..112, 120..136 zero; fee_owed_a 112..120 = 0; fee_owed_b 128..136 = 0
    # reward[2].amount_owed at 136 + 2*24 + 16 = 200..208 — set nonzero to exercise is_empty=False
    _st.pack_into("<Q", pos_bytes, 200, 0)  # reward[2] amount_owed = 0
    d = oa._decode_position_data(bytes(pos_bytes))
    assert d["liquidity"] == 0 and d["tick_lower"] == -91040 and d["tick_upper"] == -89024
    assert d["fee_owed_a"] == 0 and d["fee_owed_b"] == 0
    assert len(d["reward_infos"]) == 3
    assert d["is_position_empty"] is True
    # now set reward[1].amount_owed (offset 136+24+16=176) and confirm not-empty
    _st.pack_into("<Q", pos_bytes, 176, 15047)
    d2 = oa._decode_position_data(bytes(pos_bytes))
    assert d2["reward_infos"][1]["amount_owed"] == 15047
    assert d2["is_position_empty"] is False, "reward owed must mark position not empty"

    print("✅ ORCA CLOSE/COLLECT/REWARD LAYOUT TESTS PASS (classic + Token-2022 + 2.x position bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
