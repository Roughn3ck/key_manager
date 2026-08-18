import { Router } from "express";
import { createRailgunWallet } from "@railgun-community/wallet";
import { sidecarState, registerWallet } from "../state.js";
import { buildCreationBlockMap } from "../networks.js";

const router = Router();

// POST /wallet/load
// Body: { mnemonic: string, encryptionKey: string, creationBlockMap?: object }
// If the wallet doesn't exist in the engine DB, it creates it.
// If it already exists (same mnemonic), it loads by ID.
router.post('/load', async (req, res) => {
    try {
        if (!sidecarState.engineInitialized) {
            return res.status(400).json({
                status: 'error',
                error: 'Railgun engine not initialized. Call /engine/init first.',
            });
        }

        const { mnemonic, encryptionKey, creationBlockMap } = req.body;

        if (!mnemonic || typeof mnemonic !== 'string') {
            return res.status(400).json({
                status: 'error',
                error: 'mnemonic is required',
            });
        }

        if (!encryptionKey || typeof encryptionKey !== 'string') {
            return res.status(400).json({
                status: 'error',
                error: 'encryptionKey is required',
            });
        }

        const blockMap = creationBlockMap || buildCreationBlockMap();

        console.log("[wallet] Creating/loading Railgun wallet...");

        let walletInfo;
        let created = false;

        try {
            walletInfo = await createRailgunWallet(
                encryptionKey,
                mnemonic,
                blockMap
            );
            created = true;
            console.log(`[wallet] New wallet created: id=${walletInfo.id}`);
        } catch (createErr) {
            console.log(`[wallet] createRailgunWallet threw: ${createErr.message}`);
            throw createErr;
        }

        registerWallet(walletInfo);

        res.json({
            status: 'ok',
            walletId: walletInfo.id,
            railgunAddress: walletInfo.railgunAddress,
            publicKey: walletInfo.publicKey,
            created: created,
            message: created ? 'New Railgun wallet created' : 'Railgun wallet loaded',
        });

    } catch (err) {
        console.error("[wallet] Load failed:", err.message);
        console.error(err.stack);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// GET /wallet/list
router.get('/list', async (req, res) => {
    const wallets = [];
    for (const [id, info] of sidecarState.wallets.entries()) {
        wallets.push({
            id: info.id,
            railgunAddress: info.railgunAddress,
            publicKey: info.publicKey,
        });
    }

    res.json({
        status: 'ok',
        wallets,
        count: wallets.length,
    });
});

export default router;
