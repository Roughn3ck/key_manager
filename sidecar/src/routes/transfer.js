import { Router } from "express";
import {
    gasEstimateForShield,
    populateShield,
    gasEstimateForUnprovenUnshield,
    generateUnshieldProof,
    populateProvedUnshield,
    gasEstimateForUnprovenTransfer,
    generateTransferProof,
    populateProvedTransfer,
} from "@railgun-community/wallet";
import { sidecarState } from "../state.js";
import { COLDSTACK_TO_RAILGUN } from "../networks.js";
import {
    serializeERC20Transfer,
    createSigningWallet,
    getShieldSignature,
    getOriginalGasDetailsForTransaction,
    getGasDetailsForTransaction,
    ensureTokenApproval,
    TXIDVersion,
    calculateGasPrice,
    NETWORK_CONFIG,
} from "../utils.js";

const router = Router();

// ─── Shared validation ───

function validateTransferParams(req) {
    const { chain, fromWalletId, tokenAddress, amount, toAddress } = req.body;

    if (!sidecarState.engineInitialized) {
        return { error: { status: 400, message: 'Railgun engine not initialized.' } };
    }
    if (!chain) {
        return { error: { status: 400, message: 'chain is required' } };
    }
    const networkName = COLDSTACK_TO_RAILGUN[chain];
    if (!networkName) {
        return { error: { status: 400, message: `Chain "${chain}" is not supported by Railgun.` } };
    }
    if (!fromWalletId) {
        return { error: { status: 400, message: 'fromWalletId is required' } };
    }
    if (!sidecarState.wallets.has(fromWalletId)) {
        return { error: { status: 400, message: `Wallet ${fromWalletId} is not loaded.` } };
    }
    if (!tokenAddress) {
        return { error: { status: 400, message: 'tokenAddress is required' } };
    }
    if (!amount) {
        return { error: { status: 400, message: 'amount is required' } };
    }
    if (!toAddress) {
        return { error: { status: 400, message: 'toAddress is required' } };
    }
    return { networkName };
}

// ─── POST /transfer/shield ───
router.post('/shield', async (req, res) => {
    try {
        const validation = validateTransferParams(req);
        if (validation.error) {
            return res.status(validation.error.status).json({ status: 'error', error: validation.error.message });
        }
        const { networkName } = validation;
        const { fromWalletId, tokenAddress, amount, toAddress, signingMnemonic } = req.body;

        if (!signingMnemonic) {
            return res.status(400).json({ status: 'error', error: 'signingMnemonic is required for shield transactions (public wallet that holds the tokens)' });
        }

        const wallet = createSigningWallet(signingMnemonic, networkName);
        const fromWalletAddress = wallet.address;
        console.log(`[shield] From public wallet: ${fromWalletAddress}`);

        const erc20AmountRecipients = [
            serializeERC20Transfer(tokenAddress, BigInt(amount), toAddress),
        ];

        console.log('[shield] Step 1: Getting shield signature...');
        const shieldPrivateKey = await getShieldSignature(wallet);

        console.log('[shield] Step 2: Estimating gas...');
        const { gasEstimate } = await gasEstimateForShield(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            shieldPrivateKey,
            erc20AmountRecipients,
            [],
            fromWalletAddress
        );
        console.log(`[shield] Gas estimate: ${gasEstimate}`);

        const spender = NETWORK_CONFIG[networkName].proxyContract;
        console.log(`[shield] Step 3: Ensuring token approval for ${tokenAddress} → ${spender}...`);
        await ensureTokenApproval(wallet, tokenAddress, BigInt(amount), spender);

        console.log('[shield] Step 4: Getting gas details and populating transaction...');
        const gasDetails = await getGasDetailsForTransaction(networkName, gasEstimate);

        const { transaction, nullifiers } = await populateShield(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            shieldPrivateKey,
            erc20AmountRecipients,
            [],
            gasDetails
        );
        console.log(`[shield] Transaction populated. Nullifiers: ${nullifiers?.length || 0}`);

        console.log('[shield] Step 5: Submitting transaction...');
        const tx = await wallet.sendTransaction(transaction);
        console.log(`[shield] TX submitted: ${tx.hash}`);
        await tx.wait();
        console.log(`[shield] TX confirmed: ${tx.hash}`);

        res.json({
            status: 'ok',
            txHash: tx.hash,
            chain: req.body.chain,
            fromWalletId,
            tokenAddress,
            amount,
            toAddress,
            gasEstimate: gasEstimate.toString(),
            nullifiers: nullifiers || [],
        });

    } catch (err) {
        console.error('[shield] Failed:', err.message);
        console.error(err.stack);
        res.status(500).json({ status: 'error', error: err.message });
    }
});

// ─── POST /transfer/unshield ───
router.post('/unshield', async (req, res) => {
    try {
        const validation = validateTransferParams(req);
        if (validation.error) {
            return res.status(validation.error.status).json({ status: 'error', error: validation.error.message });
        }
        const { networkName } = validation;
        const { fromWalletId, tokenAddress, amount, toAddress, encryptionKey, signingMnemonic } = req.body;

        if (!encryptionKey) {
            return res.status(400).json({ status: 'error', error: 'encryptionKey is required for unshield transactions' });
        }
        if (!signingMnemonic) {
            return res.status(400).json({ status: 'error', error: 'signingMnemonic is required (public wallet to receive + sign)' });
        }

        const wallet = createSigningWallet(signingMnemonic, networkName);
        const sendWithPublicWallet = true;

        const erc20AmountRecipients = [
            serializeERC20Transfer(tokenAddress, BigInt(amount), toAddress),
        ];

        console.log('[unshield] Step 1: Estimating gas...');
        const originalGasDetails = await getOriginalGasDetailsForTransaction(networkName);
        const feeTokenDetails = undefined;

        const { gasEstimate } = await gasEstimateForUnprovenUnshield(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            encryptionKey,
            erc20AmountRecipients,
            [],
            originalGasDetails,
            feeTokenDetails,
            sendWithPublicWallet
        );
        console.log(`[unshield] Gas estimate: ${gasEstimate}`);

        const transactionGasDetails = await getGasDetailsForTransaction(networkName, gasEstimate);
        const overallBatchMinGasPrice = calculateGasPrice(transactionGasDetails);

        console.log('[unshield] Step 3: Generating proof (this may take 20-30 seconds)...');
        const progressCallback = (progress) => {
            console.log(`[unshield] Proof progress: ${progress}%`);
        };

        await generateUnshieldProof(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            encryptionKey,
            erc20AmountRecipients,
            [],
            undefined,
            sendWithPublicWallet,
            overallBatchMinGasPrice,
            progressCallback
        );
        console.log('[unshield] Proof generated.');

        console.log('[unshield] Step 4: Populating transaction...');
        const populateResponse = await populateProvedUnshield(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            erc20AmountRecipients,
            [],
            undefined,
            sendWithPublicWallet,
            overallBatchMinGasPrice,
            transactionGasDetails
        );

        console.log('[unshield] Step 5: Submitting transaction...');
        const tx = await wallet.sendTransaction(populateResponse.transaction);
        console.log(`[unshield] TX submitted: ${tx.hash}`);
        await tx.wait();
        console.log(`[unshield] TX confirmed: ${tx.hash}`);

        res.json({
            status: 'ok',
            txHash: tx.hash,
            chain: req.body.chain,
            fromWalletId,
            tokenAddress,
            amount,
            toAddress,
            gasEstimate: gasEstimate.toString(),
        });

    } catch (err) {
        console.error('[unshield] Failed:', err.message);
        console.error(err.stack);
        res.status(500).json({ status: 'error', error: err.message });
    }
});

// ─── POST /transfer/shielded ───
router.post('/shielded', async (req, res) => {
    try {
        const validation = validateTransferParams(req);
        if (validation.error) {
            return res.status(validation.error.status).json({ status: 'error', error: validation.error.message });
        }
        const { networkName } = validation;
        const {
            fromWalletId, tokenAddress, amount, toAddress,
            encryptionKey, signingMnemonic,
            memoText, showSenderAddressToRecipient
        } = req.body;

        if (!encryptionKey) {
            return res.status(400).json({ status: 'error', error: 'encryptionKey is required for private transfers' });
        }
        if (!signingMnemonic) {
            return res.status(400).json({ status: 'error', error: 'signingMnemonic is required (public wallet to sign the transaction)' });
        }

        const wallet = createSigningWallet(signingMnemonic, networkName);
        const sendWithPublicWallet = true;
        const showSender = showSenderAddressToRecipient !== undefined ? showSenderAddressToRecipient : true;

        const erc20AmountRecipients = [
            serializeERC20Transfer(tokenAddress, BigInt(amount), toAddress),
        ];

        console.log('[transfer] Step 1: Estimating gas...');
        const originalGasDetails = await getOriginalGasDetailsForTransaction(networkName);
        const feeTokenDetails = undefined;

        const { gasEstimate } = await gasEstimateForUnprovenTransfer(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            encryptionKey,
            memoText,
            erc20AmountRecipients,
            [],
            originalGasDetails,
            feeTokenDetails,
            sendWithPublicWallet
        );
        console.log(`[transfer] Gas estimate: ${gasEstimate}`);

        const transactionGasDetails = await getGasDetailsForTransaction(networkName, gasEstimate);
        const overallBatchMinGasPrice = calculateGasPrice(transactionGasDetails);

        console.log('[transfer] Step 3: Generating proof (this may take 20-30 seconds)...');
        const progressCallback = (progress) => {
            console.log(`[transfer] Proof progress: ${progress}%`);
        };

        await generateTransferProof(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            encryptionKey,
            showSender,
            memoText,
            erc20AmountRecipients,
            [],
            undefined,
            sendWithPublicWallet,
            overallBatchMinGasPrice,
            progressCallback
        );
        console.log('[transfer] Proof generated.');

        console.log('[transfer] Step 4: Populating transaction...');
        const populateResponse = await populateProvedTransfer(
            TXIDVersion.V2_PoseidonMerkle,
            networkName,
            fromWalletId,
            showSender,
            memoText,
            erc20AmountRecipients,
            [],
            undefined,
            sendWithPublicWallet,
            overallBatchMinGasPrice,
            transactionGasDetails
        );

        console.log('[transfer] Step 5: Submitting transaction...');
        const tx = await wallet.sendTransaction(populateResponse.transaction);
        console.log(`[transfer] TX submitted: ${tx.hash}`);
        await tx.wait();
        console.log(`[transfer] TX confirmed: ${tx.hash}`);

        res.json({
            status: 'ok',
            txHash: tx.hash,
            chain: req.body.chain,
            fromWalletId,
            tokenAddress,
            amount,
            toAddress,
            memoText: memoText || null,
            gasEstimate: gasEstimate.toString(),
        });

    } catch (err) {
        console.error('[transfer] Failed:', err.message);
        console.error(err.stack);
        res.status(500).json({ status: 'error', error: err.message });
    }
});

export default router;
