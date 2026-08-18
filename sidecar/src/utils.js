import { Wallet, JsonRpcProvider, Contract } from "ethers";
import { NETWORK_CONFIG, TXIDVersion, calculateGasPrice } from "@railgun-community/shared-models";
import { RAILGUN_TO_COLDSTACK } from "./networks.js";
import { sidecarState } from "./state.js";

/**
 * Serialize an ERC-20 transfer into a RailgunERC20AmountRecipient object.
 * @param {string} tokenAddress - ERC-20 contract address
 * @param {bigint} amount - Amount in base units (wei)
 * @param {string} recipientAddress - 0zk address (transfers) or public address (unshield)
 * @returns {object} RailgunERC20AmountRecipient
 */
export function serializeERC20Transfer(tokenAddress, amount, recipientAddress) {
    return {
        tokenAddress,
        amount: BigInt(amount),
        recipientAddress,
    };
}

/**
 * Get the RPC URL for a given Railgun network from the sidecar's loaded RPC config.
 * @param {NetworkName} network
 * @returns {string|null} RPC URL
 */
export function getRpcUrlForNetwork(network) {
    if (!sidecarState.rpcConfig) return null;
    const chainName = RAILGUN_TO_COLDSTACK[network];
    if (!chainName) return null;
    const config = sidecarState.rpcConfig[chainName];
    if (config && config.url) return config.url;
    return null;
}

/**
 * Create an ethers Wallet instance for signing transactions.
 * @param {string} mnemonic - BIP39 mnemonic for the signing wallet
 * @param {NetworkName} network - Network to sign on
 * @returns {Wallet} ethers Wallet instance connected to a provider
 */
export function createSigningWallet(mnemonic, network) {
    const rpcUrl = getRpcUrlForNetwork(network);
    if (!rpcUrl) {
        throw new Error(`No RPC URL configured for network ${network}`);
    }
    const provider = new JsonRpcProvider(rpcUrl);
    return Wallet.fromPhrase(mnemonic, provider);
}

/**
 * Get the shield private key signature from a wallet.
 * @param {Wallet} wallet
 * @returns {Promise<string>} Shield signature
 */
export async function getShieldSignature(wallet) {
    return await wallet.signMessage("RAILGUN_SHIELD_SIGNATURE");
}

/**
 * Get original gas details from the network.
 * @param {NetworkName} network
 * @returns {Promise<object>} TransactionGasDetails (without gasEstimate)
 */
export async function getOriginalGasDetailsForTransaction(network) {
    const rpcUrl = getRpcUrlForNetwork(network);
    if (!rpcUrl) {
        throw new Error(`No RPC URL configured for network ${network}`);
    }
    const provider = new JsonRpcProvider(rpcUrl);
    const feeData = await provider.getFeeData();

    const maxFeePerGas = feeData.maxFeePerGas;
    const maxPriorityFeePerGas = feeData.maxPriorityFeePerGas;

    if (maxFeePerGas && maxPriorityFeePerGas) {
        return {
            gasPrice: 0n,
            maxFeePerGas,
            maxPriorityFeePerGas,
        };
    } else {
        return {
            gasPrice: feeData.gasPrice || 0n,
            maxFeePerGas: 0n,
            maxPriorityFeePerGas: 0n,
        };
    }
}

/**
 * Get full gas details including the gas estimate.
 * @param {NetworkName} network
 * @param {bigint} gasEstimate
 * @returns {Promise<object>} TransactionGasDetails with gasEstimate
 */
export async function getGasDetailsForTransaction(network, gasEstimate) {
    const original = await getOriginalGasDetailsForTransaction(network);
    return {
        ...original,
        gasEstimate,
    };
}

/**
 * Check and approve ERC-20 token spending if allowance is insufficient.
 * @param {Wallet} wallet
 * @param {string} tokenAddress
 * @param {bigint} requiredAmount
 * @param {string} spender
 * @returns {Promise<boolean>}
 */
export async function ensureTokenApproval(wallet, tokenAddress, requiredAmount, spender) {
    const erc20Abi = [
        "function allowance(address owner, address spender) view returns (uint256)",
        "function approve(address spender, uint256 amount) external returns (bool)",
    ];
    const contract = new Contract(tokenAddress, erc20Abi, wallet);
    const currentAllowance = await contract.allowance(wallet.address, spender);

    if (currentAllowance >= requiredAmount) {
        console.log(`[approval] Sufficient allowance: ${currentAllowance} >= ${requiredAmount}`);
        return false;
    }

    console.log(`[approval] Approving ${tokenAddress} for spender ${spender}: amount=${requiredAmount}`);
    const tx = await contract.approve(spender, requiredAmount);
    await tx.wait();
    console.log(`[approval] Approval tx confirmed: ${tx.hash}`);
    return true;
}

// Re-export TXIDVersion and calculateGasPrice for route modules
export { TXIDVersion, calculateGasPrice, NETWORK_CONFIG };
