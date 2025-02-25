from fractions import Fraction
from typing import List

from eth_utils.address import to_checksum_address
from scipy import optimize  # type: ignore[import]

from ..baseclasses import ArbitrageHelper
from ..erc20_token import Erc20Token
from ..config import get_web3
from ..uniswap.v2_liquidity_pool import LiquidityPool
from ..uniswap.v2_functions import get_v2_pools_from_token_path
from ..uniswap.managers import UniswapV2LiquidityPoolManager
from ..logging import logger


class FlashBorrowToRouterSwap(ArbitrageHelper):
    def __init__(
        self,
        borrow_pool: LiquidityPool,
        borrow_token: Erc20Token,
        swap_factory_address: str,
        swap_router_address: str,
        swap_token_addresses: List[str],
        swap_router_fee=Fraction(3, 1000),
        name: str = "",
        update_method="polling",
    ):
        _web3 = get_web3()
        if _web3 is not None:
            self._w3 = _web3
        else:
            from brownie import web3 as brownie_web3  # type: ignore[import]

            if brownie_web3.isConnected():
                self._w3 = brownie_web3
            else:
                raise ValueError("No connected web3 object provided.")

        if borrow_token.address != swap_token_addresses[0]:
            raise ValueError(
                "Token addresses must begin with the borrowed token"
            )

        if borrow_pool.token0 == borrow_token:
            if borrow_pool.token1.address != swap_token_addresses[-1]:
                raise ValueError(
                    "Token addresses must end with the repaid token"
                )
        else:
            if borrow_pool.token0.address != swap_token_addresses[-1]:
                raise ValueError(
                    "Token addresses must end with the repaid token"
                )

        self.swap_router_address = swap_router_address

        # build a list of all tokens involved in this swapping path
        self.tokens: List[Erc20Token] = []
        for address in swap_token_addresses:
            self.tokens.append(Erc20Token(address=address))

        if name:
            self.name = name
        else:
            self.name = "-".join([token.symbol for token in self.tokens])

        self.token_path = [token.address for token in self.tokens]

        # build the list of intermediate pool pairs for the given multi-token path.
        # Pool list length will be 1 less than the token path length, e.g. a token1->token2->token3
        # path will result in a pool list consisting of token1/token2 and token2/token3
        self.swap_pools = []
        self.swap_pool_addresses = []

        swap_factory_address = to_checksum_address(swap_factory_address)

        self.swap_pools = get_v2_pools_from_token_path(
            tx_path=self.token_path,
            pool_manager=UniswapV2LiquidityPoolManager(
                factory_address=swap_factory_address,
                chain_id=self._w3.eth.chain_id,
            ),
        )
        for pool in self.swap_pools:
            logger.info(f"Loaded LP: {pool}")

        self.swap_pool_addresses = [pool.address for pool in self.swap_pools]

        self.borrow_pool = borrow_pool
        self.borrow_token = borrow_token

        if self.borrow_token == self.borrow_pool.token0:
            self.repay_token = self.borrow_pool.token1
        elif self.borrow_token == self.borrow_pool.token1:
            self.repay_token = self.borrow_pool.token0

        self.best = {
            "init": True,
            "borrow": 0,
            "borrow_token": self.borrow_token,
            "profit": 0,
            "profit_token": self.repay_token,
        }

    def __str__(self):
        return self.name

    def update_reserves(
        self,
        silent: bool = False,
        print_reserves: bool = True,
        print_ratios: bool = True,
    ) -> bool:
        """
        Checks each liquidity pool for updates by passing a call to .update_reserves(), which returns False if there are no updates.
        Will calculate arbitrage amounts only after checking all pools and finding an update, or on startup (via the 'init' dictionary key)
        """
        recalculate = False

        # calculate initial arbitrage after the object is instantiated, otherwise proceed with normal checks
        if self.best["init"] is True:
            self.best["init"] = False
            recalculate = True

        # flag for recalculation if the borrowing pool has been updated
        if self.borrow_pool.update_reserves(
            silent=silent,
            print_reserves=print_reserves,
            print_ratios=print_ratios,
        ):
            recalculate = True

        # flag for recalculation if any of the pools along the swap path have been updated
        for pool in self.swap_pools:
            if pool.update_reserves(
                silent=silent,
                print_reserves=print_reserves,
                print_ratios=print_ratios,
            ):
                recalculate = True

        if recalculate:
            self._calculate_arbitrage()
            return True
        else:
            return False

    def _calculate_arbitrage(self):
        # set up the boundaries for the Brent optimizer based on which token is being borrowed
        if self.borrow_token.address == self.borrow_pool.token0.address:
            bounds = (
                1,
                float(self.borrow_pool.reserves_token0),
            )
            bracket = (
                0.01 * self.borrow_pool.reserves_token0,
                0.05 * self.borrow_pool.reserves_token0,
            )
        else:
            bounds = (
                1,
                float(self.borrow_pool.reserves_token1),
            )
            bracket = (
                0.01 * self.borrow_pool.reserves_token1,
                0.05 * self.borrow_pool.reserves_token1,
            )

        opt = optimize.minimize_scalar(
            lambda x: -float(
                self.calculate_multipool_tokens_out_from_tokens_in(
                    token_in=self.borrow_token,
                    token_in_quantity=x,
                )
                - self.borrow_pool.calculate_tokens_in_from_tokens_out(
                    token_in=self.repay_token,
                    token_out_quantity=x,
                )
            ),
            method="bounded",
            bounds=bounds,
            bracket=bracket,
        )

        best_borrow = opt.x
        best_profit = -opt.fun
        swap_out = self.calculate_multipool_tokens_out_from_tokens_in(
            token_in=self.borrow_token,
            token_in_quantity=best_borrow,
        )

        # only save opportunities with rational, positive values
        if best_borrow > 0 and best_profit > 0:
            self.best.update(
                {
                    "borrow": best_borrow,
                    "profit": best_profit,
                    "swap_out": swap_out,
                }
            )
        else:
            self.best.update(
                {
                    "borrow": 0,
                    "profit": 0,
                    "swap_out": 0,
                }
            )

    def calculate_multipool_tokens_out_from_tokens_in(
        self,
        token_in: Erc20Token,
        token_in_quantity: int,
    ) -> int:
        """
        Calculates the expected token OUTPUT from the last pool for a given token INPUT to the first pool at current pool reserves.
        Uses the self.token0 and self.token1 pointers to determine which token is being swapped in
        and uses the appropriate formula
        """

        number_of_pools = len(self.swap_pools)

        for i in range(number_of_pools):
            # determine the output token for pool0
            if token_in.address == self.swap_pools[i].token0.address:
                token_out = self.swap_pools[i].token1
            elif token_in.address == self.swap_pools[i].token1.address:
                token_out = self.swap_pools[i].token0
            else:
                print("wtf?")
                raise Exception

            # calculate the swap output through pool[i]
            token_out_quantity = self.swap_pools[
                i
            ].calculate_tokens_out_from_tokens_in(
                token_in=token_in,
                token_in_quantity=token_in_quantity,
            )

            if i == number_of_pools - 1:
                break
            else:
                # otherwise, use the output as input on the next loop
                token_in = token_out
                token_in_quantity = token_out_quantity

        return token_out_quantity
