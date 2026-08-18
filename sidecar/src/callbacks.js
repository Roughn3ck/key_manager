import {
    setOnUTXOMerkletreeScanCallback,
    setOnTXIDMerkletreeScanCallback,
    setOnBalanceUpdateCallback,
} from "@railgun-community/wallet";
import { sidecarState, setBalance, setScanStatus } from "./state.js";

/**
 * Sets up Railgun engine callbacks for merkletree scan progress
 * and balance updates. These callbacks populate sidecarState so
 * that HTTP route handlers can report scan status and balances.
 *
 * Call this after startRailgunEngine() succeeds.
 */
export function setupEngineCallbacks() {
    setOnUTXOMerkletreeScanCallback((eventData) => {
        const { chain, progress, scanStatus } = eventData;
        const chainKey = chain?.toString?.() || chain || "unknown";
        setScanStatus(chainKey, "utxoProgress", progress);
        setScanStatus(chainKey, "status", scanStatus);
        console.log(`[scan-utxo] chain=${chainKey} progress=${progress} status=${scanStatus}`);
    });

    setOnTXIDMerkletreeScanCallback((eventData) => {
        const { chain, progress, scanStatus } = eventData;
        const chainKey = chain?.toString?.() || chain || "unknown";
        setScanStatus(chainKey, "txidProgress", progress);
        setScanStatus(chainKey, "status", scanStatus);
        console.log(`[scan-txid] chain=${chainKey} progress=${progress} status=${scanStatus}`);
    });

    setOnBalanceUpdateCallback((balancesEvent) => {
        const {
            chain,
            railgunWalletID,
            balanceBucket,
            erc20Amounts,
            nftAmounts,
        } = balancesEvent;

        const chainKey = chain?.toString?.() || chain || "unknown";
        const balanceData = {
            walletId: railgunWalletID,
            chain: chainKey,
            bucket: balanceBucket,
            erc20Amounts: erc20Amounts || [],
            nftAmounts: nftAmounts || [],
            timestamp: new Date().toISOString(),
        };

        setBalance(railgunWalletID, chainKey, balanceBucket, balanceData);
        console.log(
            `[balance] wallet=${railgunWalletID} chain=${chainKey} ` +
            `bucket=${balanceBucket} erc20=${erc20Amounts?.length || 0} ` +
            `nft=${nftAmounts?.length || 0}`
        );
    });

    console.log("[callbacks] Engine callbacks registered (UTXO scan, TXID scan, balance updates)");
}
