import { NETWORK_CONFIG, NetworkName } from "@railgun-community/shared-models";

/**
 * Mapping from ColdStack chain names to Railgun NetworkName enum values.
 * ColdStack uses lowercase chain names in its rpc_config; Railgun uses
 * a NetworkName enum from shared-models.
 */
export const COLDSTACK_TO_RAILGUN = {
    "ethereum": NetworkName.Ethereum,
    "arbitrum": NetworkName.Arbitrum,
    "bsc": NetworkName.BNBChain,
    "polygon": NetworkName.Polygon,
    "base": NetworkName.Base,
    "optimism": NetworkName.Optimism,
};

/**
 * Railgun NetworkName -> ColdStack chain name (reverse map)
 */
export const RAILGUN_TO_COLDSTACK = Object.fromEntries(
    Object.entries(COLDSTACK_TO_RAILGUN).map(([k, v]) => [v, k])
);

/**
 * Networks supported by Railgun (for status reporting)
 */
export const SUPPORTED_RAILGUN_NETWORKS = Object.values(COLDSTACK_TO_RAILGUN);

/**
 * Builds a FallbackProviderJsonConfig for the Railgun engine from
 * ColdStack's RPC config format.
 *
 * ColdStack RPC config format (from rpc_config.py):
 * {
 *   "ethereum": { "url": "https://...", "auth": null, "fallback": "https://..." },
 *   "base": { "url": "https://...", "auth": null, "fallback": "https://..." },
 *   ...
 * }
 *
 * @param {string} chainName - ColdStack chain name (e.g. "ethereum", "base")
 * @param {object} chainConfig - { url, auth, fallback }
 * @returns {FallbackProviderJsonConfig|null} Provider config or null if chain not supported by Railgun
 */
export function buildProviderConfig(chainName, chainConfig) {
    const networkName = COLDSTACK_TO_RAILGUN[chainName];
    if (!networkName) {
        return null;
    }

    const networkConfig = NETWORK_CONFIG[networkName];
    if (!networkConfig) {
        return null;
    }

    const providers = [];

    if (chainConfig.url) {
        providers.push({
            provider: chainConfig.url,
            priority: 3,
            weight: 2,
            maxLogsPerBatch: 1,
        });
    }

    if (chainConfig.fallback) {
        providers.push({
            provider: chainConfig.fallback,
            priority: 2,
            weight: 2,
            maxLogsPerBatch: 1,
        });
    }

    if (providers.length === 0) {
        return null;
    }

    return {
        chainId: networkConfig.chain.id,
        providers,
    };
}

/**
 * Gets the deployment block for a Railgun network.
 * Used for creationBlockMap when creating wallets.
 *
 * @param {NetworkName} networkName
 * @returns {number|undefined}
 */
export function getDeploymentBlock(networkName) {
    const config = NETWORK_CONFIG[networkName];
    return config?.deploymentBlock;
}

/**
 * Builds a creationBlockMap for all supported Railgun networks.
 * Used when creating a new wallet to optimize initial balance scans.
 *
 * @returns {object} Map of NetworkName -> deploymentBlock
 */
export function buildCreationBlockMap() {
    const map = {};
    for (const networkName of SUPPORTED_RAILGUN_NETWORKS) {
        const block = getDeploymentBlock(networkName);
        if (block !== undefined) {
            map[networkName] = block;
        }
    }
    return map;
}
