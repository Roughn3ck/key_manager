"""ColdTrack CLI entry point.

Subcommands:
  export   - Materialize merged strategy_view.json (delegates to sentinel_export).
  compound - Record a HyperEVM fee-compound in coldtrack.db.

Example:
  python -m coldtrack export --db ./coldtrack.db --out view.json
  python -m coldtrack compound --db ./coldtrack.db --token-id 545983 \
      --tx-hash 0xabc... --amount-a 1.5 --amount-b 0.0001 --value-usd 42.0
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def _cmd_export(argv: Optional[Sequence[str]] = None) -> int:
    """Delegate to sentinel_export's main()."""
    from coldtrack.sentinel_export import main as sentinel_main
    return sentinel_main(argv)


def _cmd_compound(argv: Optional[Sequence[str]] = None) -> int:
    """Record a fee compound in coldtrack.db.

    This is the sole-writer CLI path for HyperEVM compounding. All network data
    (token amounts, tx hash, prices) is captured first; the CLI only performs the
    atomic DB write.
    """
    parser = argparse.ArgumentParser(
        prog="coldtrack compound",
        description="Record a HyperEVM LP fee compound in coldtrack.db.",
    )
    parser.add_argument("--db", required=True, metavar="PATH",
                        help="Path to coldtrack.db")
    parser.add_argument("--token-id", required=True, metavar="ID",
                        help="Position NFT token id (LP_POSITIONS.TOKEN_ID match key)")
    parser.add_argument("--tx-hash", required=True, metavar="HASH",
                        help="Compound (increaseLiquidity) transaction hash")
    parser.add_argument("--collect-hash", default=None, metavar="HASH",
                        help="Optional collect transaction hash (source of fees)")
    parser.add_argument("--amount-a", type=float, default=0.0, metavar="AMT",
                        help="Reinvested token A amount (human units)")
    parser.add_argument("--amount-b", type=float, default=0.0, metavar="AMT",
                        help="Reinvested token B amount (human units)")
    parser.add_argument("--value-usd", type=float, default=0.0, metavar="USD",
                        help="Total USD value of reinvested fees")
    parser.add_argument("--date", default=None, metavar="ISO",
                        help="ISO-8601 UTC timestamp (default: now)")
    parser.add_argument("--token-a", default="WHYPE", metavar="SYMBOL",
                        help="Token A symbol (default: WHYPE)")
    parser.add_argument("--token-b", default="UBTC", metavar="SYMBOL",
                        help="Token B symbol (default: UBTC)")
    parser.add_argument("--price-a", type=float, default=None, metavar="USD",
                        help="Token A spot USD price (optional)")
    parser.add_argument("--price-b", type=float, default=None, metavar="USD",
                        help="Token B spot USD price (optional)")
    parser.add_argument("--owner", default=None, metavar="ADDR",
                        help="Wallet address that owns/signed the position (optional)")
    args = parser.parse_args(argv)

    from coldtrack.compound_recorder import CompoundResult, CompoundLeg, record_compound_and_export

    date_iso = args.date
    if not date_iso:
        from datetime import datetime, timezone
        date_iso = datetime.now(timezone.utc).isoformat()

    legs: List[CompoundLeg] = []
    if args.amount_a > 0:
        value_a = round(args.amount_a * (args.price_a or 0.0), 6) if args.price_a else None
        if args.value_usd and value_a is None and args.amount_b <= 0:
            value_a = round(args.value_usd, 6)
        legs.append(CompoundLeg(
            asset=args.token_a, amount=args.amount_a, value_usd=value_a, sig=args.tx_hash,
        ))
    if args.amount_b > 0:
        value_b = round(args.amount_b * (args.price_b or 0.0), 6) if args.price_b else None
        if args.value_usd and value_b is None and args.amount_a <= 0:
            value_b = round(args.value_usd, 6)
        legs.append(CompoundLeg(
            asset=args.token_b, amount=args.amount_b, value_usd=value_b, sig=args.tx_hash,
        ))
    if args.value_usd and not any(l.value_usd for l in legs):
        # Even if amounts are zero, record the income value on token A.
        legs.append(CompoundLeg(
            asset=args.token_a, amount=0.0, value_usd=round(args.value_usd, 6), sig=args.tx_hash,
        ))

    token_prices: Dict[str, float] = {}
    if args.price_a:
        token_prices[args.token_a] = args.price_a
    if args.price_b:
        token_prices[args.token_b] = args.price_b

    result = CompoundResult(
        position_mint=str(args.token_id),
        platform="HyperEVM",
        chain="HyperEVM",
        tx_hash=args.tx_hash,
        collect_sig=args.collect_hash,
        legs=legs,
        block_time_iso=date_iso,
        token_price_usd=token_prices,
        final_amounts={args.token_a: args.amount_a, args.token_b: args.amount_b},
        owner=args.owner,
    )

    out = record_compound_and_export(result, Path(args.db))
    if out.get("error"):
        print(f"Ledger pending: {out['error']}", file=sys.stderr)
        return 1
    print(f"Compound recorded: position_id={out['position_id']} "
          f"fee_events={out.get('fee_events', 0)} snapshots={out.get('snapshots', 0)}")
    return 0


def _cmd_record_claim(argv: Optional[Sequence[str]] = None) -> int:
    """Record an Aerodrome AERO emissions claim (v5.3.30).

    One-shot backfill for claims that were executed outside ColdStack (e.g.
    Aerodrome UI). Captured network data is written atomically to
    FEE_EVENTS + TRANSACTIONS and the LP_POSITIONS cumulative fee counters.

    G2 fixture:
      tx 0xa2a5f2f4edb67165bf1595efa964fae8283ffba30ec4fbb23427b34b3d280728
      6.53808479 AERO, gas 0.00000089 ETH
      accrued 0.003748 WETH + 0.00011293 cbBTC
    """
    parser = argparse.ArgumentParser(
        prog="coldtrack record-claim",
        description="Record an Aerodrome AERO emissions claim in coldtrack.db.",
    )
    parser.add_argument("--db", required=True, metavar="PATH",
                        help="Path to coldtrack.db")
    parser.add_argument("--token-id", required=True, metavar="ID",
                        help="Position NFT token id (LP_POSITIONS.TOKEN_ID)")
    parser.add_argument("--tx-hash", required=True, metavar="HASH",
                        help="Claim transaction hash")
    parser.add_argument("--aero", type=float, required=True, metavar="AMT",
                        help="AERO amount claimed (human units)")
    parser.add_argument("--value-usd", type=float, default=0.0, metavar="USD",
                        help="USD value of the AERO claim")
    parser.add_argument("--gas-eth", type=float, default=0.0, metavar="ETH",
                        help="Gas paid in native ETH")
    parser.add_argument("--date", default=None, metavar="ISO",
                        help="ISO-8601 UTC timestamp (default: now)")
    parser.add_argument("--account", default="", metavar="NAME",
                        help="Vault account name (optional, defaults to LP owner)")
    parser.add_argument("--accrued-weth", type=float, default=0.0,
                        help="Accrued WETH trading fees at claim time")
    parser.add_argument("--accrued-cbbtc", type=float, default=0.0,
                        help="Accrued cbBTC trading fees at claim time")
    args = parser.parse_args(argv)

    from datetime import datetime, timezone

    date_iso = args.date or datetime.now(timezone.utc).isoformat()

    from venue_adapters.aerodrome_gauge_writer import (
        AerodromeGaugeWriter, GaugeClaimRecord,
    )

    writer = AerodromeGaugeWriter()
    record = GaugeClaimRecord(
        position_id_str=str(args.token_id),
        account_id=0,  # resolved inside _record_claim from LP_POSITIONS.ACCOUNT_ID
        position_db_id=0,  # resolved by matching TOKEN_ID
        date_iso=date_iso,
        aero_amount=args.aero,
        value_usd=args.value_usd or None,
        tx_hash=args.tx_hash,
        notes=(
            f"gauge emissions; gas {args.gas_eth:g} ETH; "
            f"accrued WETH {args.accrued_weth:g} + cbBTC {args.accrued_cbbtc:g}"
        ),
    )
    out = writer.record_claim_backfill(record, Path(args.db))
    if out.get("error"):
        print(f"Ledger pending: {out['error']}", file=sys.stderr)
        return 1
    print(f"Claim recorded: position_id={out.get('position_id')} "
          f"fee_events={out.get('fee_events', 0)} "
          f"transactions={out.get('transactions', 0)}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coldtrack",
        description="ColdTrack ledger utilities.",
    )
    parser.add_argument(
        "command", choices=["export", "compound", "record-claim"],
        help="Subcommand to run",
    )
    parser.add_argument(
        "rest", nargs=argparse.REMAINDER,
        help="Arguments for the subcommand",
    )
    args = parser.parse_args(argv)

    if args.command == "export":
        return _cmd_export(args.rest)
    if args.command == "compound":
        return _cmd_compound(args.rest)
    if args.command == "record-claim":
        return _cmd_record_claim(args.rest)
    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
