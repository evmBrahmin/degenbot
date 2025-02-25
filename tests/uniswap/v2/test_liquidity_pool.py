import pickle
from fractions import Fraction
from typing import Dict

import degenbot
import pytest
import web3
from degenbot import Erc20Token
from degenbot.exceptions import NoPoolStateAvailable, ZeroSwapError
from degenbot.uniswap import LiquidityPool, UniswapV2PoolSimulationResult, UniswapV2PoolState
from eth_utils import to_checksum_address

degenbot.set_web3(web3.Web3(web3.HTTPProvider(("http://localhost:8545"))))


class MockErc20Token(Erc20Token):
    def __init__(self):
        pass


# Tests are based on the WBTC-WETH Uniswap V2 pool on Ethereum mainnet,
# evaluated against the results from the Uniswap V2 Router 2 contract
# functions `getAmountsOut` and `getAmountsIn`
#
# Pool address: 0xBb2b8038a1640196FbE3e38816F3e67Cba72D940
# Router address: 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D

UNISWAP_V2_WBTC_WETH_POOL_ADDRESS = to_checksum_address(
    "0xBb2b8038a1640196FbE3e38816F3e67Cba72D940"
)
UNISWAPV2_FACTORY_ADDRESS = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
UNISWAPV2_FACTORY_POOL_INIT_HASH = (
    "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f"
)


@pytest.fixture
def wbtc_weth_liquiditypool() -> LiquidityPool:
    token0 = MockErc20Token()
    token0.address = to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599")
    token0.decimals = 8
    token0.name = "Wrapped BTC"
    token0.symbol = "WBTC"

    token1 = MockErc20Token()
    token1.address = to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    token1.decimals = 18
    token1.name = "Wrapped Ether"
    token1.symbol = "WETH"

    try:
        del degenbot.AllPools(chain_id=1)[UNISWAP_V2_WBTC_WETH_POOL_ADDRESS]
    except KeyError:
        pass

    lp = LiquidityPool(
        address=UNISWAP_V2_WBTC_WETH_POOL_ADDRESS,
        update_method="external",
        tokens=[token0, token1],
        name="WBTC-WETH (V2, 0.30%)",
        factory_address=UNISWAPV2_FACTORY_ADDRESS,
        factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
        empty=True,
    )
    lp.update_reserves(
        external_token0_reserves=16231137593,
        external_token1_reserves=2571336301536722443178,
        update_block=1,
    )

    return lp


def test_create_pool() -> None:
    token0 = MockErc20Token()
    token0.address = to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599")
    token0.decimals = 8
    token0.name = "Wrapped BTC"
    token0.symbol = "WBTC"

    token1 = MockErc20Token()
    token1.address = to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    token1.decimals = 18
    token1.name = "Wrapped Ether"
    token1.symbol = "WETH"

    LiquidityPool(
        address=UNISWAP_V2_WBTC_WETH_POOL_ADDRESS,
        tokens=[token0, token1],
        name="WBTC-WETH (V2, 0.30%)",
        factory_address=UNISWAPV2_FACTORY_ADDRESS,
        factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
        empty=True,
    )


def test_create_empty_pool(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    _pool: LiquidityPool = wbtc_weth_liquiditypool

    LiquidityPool(
        address=UNISWAP_V2_WBTC_WETH_POOL_ADDRESS,
        tokens=[_pool.token0, _pool.token1],
        name="WBTC-WETH (V2, 0.30%)",
        factory_address=UNISWAPV2_FACTORY_ADDRESS,
        factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
        empty=True,
    )

    with pytest.raises(ValueError):
        LiquidityPool(
            address=UNISWAP_V2_WBTC_WETH_POOL_ADDRESS,
            # tokens=[_pool.token0, _pool.token1],
            name="WBTC-WETH (V2, 0.30%)",
            factory_address=UNISWAPV2_FACTORY_ADDRESS,
            factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
            empty=True,
        )

    with pytest.raises(ValueError):
        LiquidityPool(
            address=UNISWAP_V2_WBTC_WETH_POOL_ADDRESS,
            tokens=[_pool.token0, _pool.token1],
            name="WBTC-WETH (V2, 0.30%)",
            # factory_address=UNISWAPV2_FACTORY_ADDRESS,
            factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
            empty=True,
        )


def test_pickle_pool(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    pickle.dumps(wbtc_weth_liquiditypool)


def test_calculate_tokens_out_from_tokens_in(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    # Reserve values for this test are taken at block height 17,600,000

    assert (
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            wbtc_weth_liquiditypool.token0,
            8000000000,
        )
        == 847228560678214929944
    )
    assert (
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            wbtc_weth_liquiditypool.token1,
            1200000000000000000000,
        )
        == 5154005339
    )


def test_calculate_tokens_out_from_tokens_in_with_override(
    wbtc_weth_liquiditypool: LiquidityPool
) -> None:
    # Overridden reserve values for this test are taken at block height 17,650,000
    # token0 reserves: 16027096956
    # token1 reserves: 2602647332090181827846

    pool_state_override = UniswapV2PoolState(
        pool=wbtc_weth_liquiditypool,
        reserves_token0=16027096956,
        reserves_token1=2602647332090181827846,
    )

    assert (
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            token_in=wbtc_weth_liquiditypool.token0,
            token_in_quantity=8000000000,
            override_state=pool_state_override,
        )
        == 864834865217768537471
    )

    with pytest.raises(
        ValueError,
        match="Must provide reserve override values for both tokens",
    ):
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            token_in=wbtc_weth_liquiditypool.token0,
            token_in_quantity=8000000000,
            override_reserves_token0=0,
            override_reserves_token1=10,
        )


def test_calculate_tokens_in_from_tokens_out(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    # Reserve values for this test are taken at block height 17,600,000
    assert (
        wbtc_weth_liquiditypool.calculate_tokens_in_from_tokens_out(
            8000000000,
            wbtc_weth_liquiditypool.token1,
        )
        == 2506650866141614297072
    )

    assert (
        wbtc_weth_liquiditypool.calculate_tokens_in_from_tokens_out(
            1200000000000000000000,
            wbtc_weth_liquiditypool.token0,
        )
        == 14245938804
    )


def test_calculate_tokens_in_from_tokens_out_with_override(
    wbtc_weth_liquiditypool: LiquidityPool
) -> None:
    # Overridden reserve values for this test are taken at block height 17,650,000
    # token0 reserves: 16027096956
    # token1 reserves: 2602647332090181827846

    pool_state_override = UniswapV2PoolState(
        pool=wbtc_weth_liquiditypool,
        reserves_token0=16027096956,
        reserves_token1=2602647332090181827846,
    )

    assert (
        wbtc_weth_liquiditypool.calculate_tokens_in_from_tokens_out(
            token_in=wbtc_weth_liquiditypool.token0,
            token_out_quantity=1200000000000000000000,
            override_state=pool_state_override,
        )
        == 13752842264
    )

    with pytest.raises(
        ValueError,
        match="Must provide reserve override values for both tokens",
    ):
        wbtc_weth_liquiditypool.calculate_tokens_in_from_tokens_out(
            token_in=wbtc_weth_liquiditypool.token0,
            token_out_quantity=1200000000000000000000,
            override_reserves_token0=0,
            override_reserves_token1=10,
        )


def test_comparisons(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    assert wbtc_weth_liquiditypool == "0xBb2b8038a1640196FbE3e38816F3e67Cba72D940"
    assert wbtc_weth_liquiditypool == "0xBb2b8038a1640196FbE3e38816F3e67Cba72D940".lower()

    del degenbot.AllPools(chain_id=1)[wbtc_weth_liquiditypool]

    other_lp = LiquidityPool(
        address="0xBb2b8038a1640196FbE3e38816F3e67Cba72D940",
        update_method="external",
        tokens=[wbtc_weth_liquiditypool.token0, wbtc_weth_liquiditypool.token1],
        name="WBTC-WETH (V2, 0.30%)",
        factory_address=UNISWAPV2_FACTORY_ADDRESS,
        factory_init_hash=UNISWAPV2_FACTORY_POOL_INIT_HASH,
        fee=Fraction(3, 1000),
        empty=True,
    )

    assert wbtc_weth_liquiditypool == other_lp
    assert wbtc_weth_liquiditypool is not other_lp

    with pytest.raises(NotImplementedError):
        assert wbtc_weth_liquiditypool == 420

    # sets depend on __hash__ dunder method
    set([wbtc_weth_liquiditypool, other_lp])


def test_reorg(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    _START_BLOCK = 2
    _END_BLOCK = 10

    # Provide some dummy updates, then simulate a reorg back to the starting state
    starting_state = wbtc_weth_liquiditypool.state
    starting_token0_reserves = starting_state.reserves_token0
    starting_token1_reserves = starting_state.reserves_token1

    block_states: Dict[int, UniswapV2PoolState] = {1: wbtc_weth_liquiditypool.state}

    for block_number in range(_START_BLOCK, _END_BLOCK + 1, 1):
        wbtc_weth_liquiditypool.update_reserves(
            external_token0_reserves=starting_token0_reserves + 10_000 * block_number,
            external_token1_reserves=starting_token1_reserves + 10_000 * block_number,
            print_ratios=False,
            print_reserves=False,
            update_block=block_number,
        )
        # lp.external_update(
        #     update=UniswapV2PoolExternalUpdate(
        #         block_number=block_number,
        #         liquidity=starting_liquidity + 10_000 * block_number,
        #     ),
        # )
        block_states[block_number] = wbtc_weth_liquiditypool.state

    last_block_state = wbtc_weth_liquiditypool.state

    # Cannot restore to a pool state before the first
    with pytest.raises(NoPoolStateAvailable):
        wbtc_weth_liquiditypool.restore_state_before_block(0)

    # Last state is at block 10, so this will succedd but have no effect on the current state
    wbtc_weth_liquiditypool.restore_state_before_block(11)
    assert wbtc_weth_liquiditypool.state == last_block_state

    # Unwind the updates and compare to the stored states at previous blocks
    for block_number in range(_END_BLOCK + 1, 1, -1):
        wbtc_weth_liquiditypool.restore_state_before_block(block_number)
        assert wbtc_weth_liquiditypool.state == block_states[block_number - 1]

    # Verify the pool has been returned to the starting state
    assert wbtc_weth_liquiditypool.state == starting_state

    # Unwind all states
    wbtc_weth_liquiditypool.restore_state_before_block(1)
    assert wbtc_weth_liquiditypool.state == UniswapV2PoolState(wbtc_weth_liquiditypool, 0, 0)


def test_simulations(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    sim_result = UniswapV2PoolSimulationResult(
        amount0_delta=8000000000,
        amount1_delta=-847228560678214929944,
        current_state=wbtc_weth_liquiditypool.state,
        future_state=UniswapV2PoolState(
            pool=wbtc_weth_liquiditypool,
            reserves_token0=wbtc_weth_liquiditypool.reserves_token0 + 8000000000,
            reserves_token1=wbtc_weth_liquiditypool.reserves_token1 - 847228560678214929944,
        ),
    )

    # token_in = lp.token0 should have same result as token_out = lp.token1
    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_in=wbtc_weth_liquiditypool.token0,
            token_in_quantity=8000000000,
        )
        == sim_result
    )
    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_out=wbtc_weth_liquiditypool.token1,
            token_in_quantity=8000000000,
        )
        == sim_result
    )

    sim_result = UniswapV2PoolSimulationResult(
        amount0_delta=-5154005339,
        amount1_delta=1200000000000000000000,
        current_state=wbtc_weth_liquiditypool.state,
        future_state=UniswapV2PoolState(
            pool=wbtc_weth_liquiditypool,
            reserves_token0=wbtc_weth_liquiditypool.reserves_token0 - 5154005339,
            reserves_token1=wbtc_weth_liquiditypool.reserves_token1 + 1200000000000000000000,
        ),
    )

    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_in=wbtc_weth_liquiditypool.token1,
            token_in_quantity=1200000000000000000000,
        )
        == sim_result
    )

    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_out=wbtc_weth_liquiditypool.token0,
            token_in_quantity=1200000000000000000000,
        )
        == sim_result
    )


def test_simulations_with_override(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    sim_result = UniswapV2PoolSimulationResult(
        amount0_delta=8000000000,
        amount1_delta=-864834865217768537471,
        current_state=wbtc_weth_liquiditypool.state,
        future_state=UniswapV2PoolState(
            pool=wbtc_weth_liquiditypool,
            reserves_token0=wbtc_weth_liquiditypool.reserves_token0 + 8000000000,
            reserves_token1=wbtc_weth_liquiditypool.reserves_token1 - 864834865217768537471,
        ),
    )

    pool_state_override = UniswapV2PoolState(
        pool=wbtc_weth_liquiditypool,
        reserves_token0=16027096956,
        reserves_token1=2602647332090181827846,
    )

    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_in=wbtc_weth_liquiditypool.token0,
            token_in_quantity=8000000000,
            override_state=pool_state_override,
        )
        == sim_result
    )

    sim_result = UniswapV2PoolSimulationResult(
        amount0_delta=13752842264,
        amount1_delta=-1200000000000000000000,
        current_state=wbtc_weth_liquiditypool.state,
        future_state=UniswapV2PoolState(
            pool=wbtc_weth_liquiditypool,
            reserves_token0=wbtc_weth_liquiditypool.reserves_token0 + 13752842264,
            reserves_token1=wbtc_weth_liquiditypool.reserves_token1 - 1200000000000000000000,
        ),
    )

    assert (
        wbtc_weth_liquiditypool.simulate_swap(
            token_out=wbtc_weth_liquiditypool.token1,
            token_out_quantity=1200000000000000000000,
            override_state=pool_state_override,
        )
        == sim_result
    )


def test_swap_for_all(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    # The last token in a pool can never be swapped for
    assert (
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            wbtc_weth_liquiditypool.token1,
            2**256 - 1,
        )
        == wbtc_weth_liquiditypool.reserves_token0 - 1
    )
    assert (
        wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
            wbtc_weth_liquiditypool.token0,
            2**256 - 1,
        )
        == wbtc_weth_liquiditypool.reserves_token1 - 1
    )


def test_zero_swaps(wbtc_weth_liquiditypool: LiquidityPool) -> None:
    with pytest.raises(ZeroSwapError):
        assert (
            wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
                wbtc_weth_liquiditypool.token0,
                0,
            )
            == 0
        )

    with pytest.raises(ZeroSwapError):
        assert (
            wbtc_weth_liquiditypool.calculate_tokens_out_from_tokens_in(
                wbtc_weth_liquiditypool.token1,
                0,
            )
            == 0
        )
