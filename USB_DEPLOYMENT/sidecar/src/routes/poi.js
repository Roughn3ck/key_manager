import { Router } from "express";
import { refreshBalances } from "@railgun-community/wallet";
import { sidecarState } from "../state.js";
import { COLDSTACK_TO_RAILGUN } from "../networks.js";

const router = Router();

// POST /poi/refresh
// Body: { chain, walletIds?: string[] }
// Triggers a balance scan which also refreshes POI status.
// POI status is derived from the balance buckets (MissingInternalPOI, MissingExternalPOI, etc.)
router.post('/refresh', async (req, res) => {
    try {
        if (!sidecarState.engineInitialized) {
            return res.status(400).json({
                status: 'error',
                error: 'Railgun engine not initialized.',
            });
        }

        const { chain, walletIds } = req.body;

        if (!chain) {
            return res.status(400).json({
                status: 'error',
                error: 'chain is required',
            });
        }

        const networkName = COLDSTACK_TO_RAILGUN[chain];
        if (!networkName) {
            return res.status(400).json({
                status: 'error',
                error: `Chain "${chain}" is not supported by Railgun. Supported: ${Object.keys(COLDSTACK_TO_RAILGUN).join(', ')}`,
            });
        }

        let ids = walletIds;
        if (!ids || !Array.isArray(ids) || ids.length === 0) {
            ids = Array.from(sidecarState.wallets.keys());
        }

        if (ids.length === 0) {
            return res.json({
                status: 'ok',
                message: 'No wallets loaded — nothing to refresh.',
                scannedWallets: 0,
            });
        }

        console.log(`[poi] Refreshing balances for chain=${chain} wallets=${ids.length}`);
        await refreshBalances(networkName, ids);

        res.json({
            status: 'ok',
            message: 'Balance scan triggered. POI status will update via callbacks.',
            scannedWallets: ids.length,
            chain,
        });

    } catch (err) {
        console.error("[poi] Refresh failed:", err.message);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// GET /poi/status
// Returns POI-relevant balance buckets for all wallets
router.get('/status', async (req, res) => {
    const result = {};

    for (const [key, balanceData] of sidecarState.balances.entries()) {
        const [walletId, chain, bucket] = key.split(':');

        if (!result[walletId]) {
            result[walletId] = {};
        }
        if (!result[walletId][chain]) {
            result[walletId][chain] = {};
        }

        const isPOIBucket = [
            'Spendable',
            'ShieldBlocked',
            'ShieldPending',
            'ProofSubmitted',
            'MissingInternalPOI',
            'MissingExternalPOI',
            'Spent',
        ].includes(bucket);

        if (isPOIBucket) {
            result[walletId][chain][bucket] = {
                erc20Count: balanceData.erc20Amounts.length,
                nftCount: balanceData.nftAmounts.length,
                timestamp: balanceData.timestamp,
            };
        }
    }

    res.json({
        status: 'ok',
        wallets: result,
    });
});

export default router;
