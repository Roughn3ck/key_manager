import { Router } from "express";
import { refreshBalances } from "@railgun-community/wallet";
import { sidecarState } from "../state.js";
import { COLDSTACK_TO_RAILGUN } from "../networks.js";

const router = Router();

// POST /balances/refresh
// Body: { chain, walletIds: string[] }
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
                error: `Chain "${chain}" is not supported by Railgun.`,
            });
        }

        let ids = walletIds;
        if (!ids || !Array.isArray(ids) || ids.length === 0) {
            ids = Array.from(sidecarState.wallets.keys());
        }

        if (ids.length === 0) {
            return res.json({
                status: 'ok',
                message: 'No wallets loaded.',
                scannedWallets: 0,
            });
        }

        console.log(`[balances] Refreshing for chain=${chain} wallets=${ids.length}`);
        await refreshBalances(networkName, ids);

        res.json({
            status: 'ok',
            message: 'Balance scan triggered. Results will populate via callbacks.',
            scannedWallets: ids.length,
            chain,
        });

    } catch (err) {
        console.error("[balances] Refresh failed:", err.message);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// GET /balances/status
// Returns all balance data collected by the onBalanceUpdateCallback
router.get('/status', async (req, res) => {
    const buckets = {};

    for (const [key, data] of sidecarState.balances.entries()) {
        const [walletId, chain, bucket] = key.split(':');

        if (!buckets[walletId]) {
            buckets[walletId] = {};
        }
        if (!buckets[walletId][chain]) {
            buckets[walletId][chain] = {};
        }

        buckets[walletId][chain][bucket] = {
            erc20Amounts: data.erc20Amounts,
            nftAmounts: data.nftAmounts,
            timestamp: data.timestamp,
        };
    }

    const scanStatus = {};
    for (const [chain, status] of sidecarState.scanStatus.entries()) {
        scanStatus[chain] = status;
    }

    res.json({
        status: 'ok',
        buckets,
        scanStatus,
    });
});

export default router;
