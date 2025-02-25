from io import TextIOWrapper
from typing import Dict, List, Optional, TextIO, Tuple, Union

import ujson
from eth_typing import ChecksumAddress
from eth_utils.address import to_checksum_address
from web3 import Web3
from web3._utils.events import get_event_data
from web3._utils.filters import construct_event_filter_params

from ..config import get_web3
from ..logging import logger
from .abi import UNISWAP_V3_POOL_ABI
from .v3_dataclasses import (
    UniswapV3BitmapAtWord,
    UniswapV3LiquidityAtTick,
    UniswapV3LiquidityEvent,
    UniswapV3PoolExternalUpdate,
)
from .v3_liquidity_pool import V3LiquidityPool


class UniswapV3LiquiditySnapshot:
    """
    Retrieve and maintain liquidity positions for Uniswap V3 pools.
    """

    def __init__(
        self,
        file: Union[TextIO, str],
        chain_id: Optional[int] = None,
    ):
        _file: TextIOWrapper
        json_liquidity_snapshot: dict

        try:
            if isinstance(file, TextIOWrapper):
                _file = file
                json_liquidity_snapshot = ujson.load(file)
            elif isinstance(file, str):
                with open(file) as _file:
                    json_liquidity_snapshot = ujson.load(_file)
            else:
                raise ValueError(f"GOT {type(file)}")
        except:
            raise
        finally:
            _file.close()

        _web3 = get_web3()
        if _web3 is not None:
            self._w3 = _web3
        else:
            from brownie import web3 as brownie_web3  # type: ignore[import]

            if brownie_web3.isConnected():
                self._w3 = brownie_web3
            else:
                raise ValueError("No connected web3 object provided.")

        self._chain_id = chain_id or self._w3.eth.chain_id

        self.newest_block = json_liquidity_snapshot.pop("snapshot_block")

        self._liquidity_snapshot: Dict[ChecksumAddress, Dict] = dict()
        for (
            pool_address,
            pool_liquidity_snapshot,
        ) in json_liquidity_snapshot.items():
            self._liquidity_snapshot[to_checksum_address(pool_address)] = {
                "tick_bitmap": {
                    int(k): UniswapV3BitmapAtWord(**v)
                    for k, v in pool_liquidity_snapshot["tick_bitmap"].items()
                },
                "tick_data": {
                    int(k): UniswapV3LiquidityAtTick(**v)
                    for k, v in pool_liquidity_snapshot["tick_data"].items()
                },
            }

        logger.info(
            f"Loaded LP snapshot: {len(json_liquidity_snapshot)} pools @ block {self.newest_block}"
        )

        self._liquidity_events: Dict[ChecksumAddress, List[UniswapV3LiquidityEvent]] = dict()

    def _add_pool_if_missing(self, pool_address: ChecksumAddress) -> None:
        try:
            self._liquidity_events[pool_address]
        except KeyError:
            self._liquidity_events[pool_address] = []

        try:
            self._liquidity_snapshot[pool_address]
        except KeyError:
            self._liquidity_snapshot[pool_address] = {}

    def fetch_new_liquidity_events(
        self,
        to_block: int,
        span: int = 1000,
    ) -> None:
        def _process_log() -> Tuple[ChecksumAddress, UniswapV3LiquidityEvent]:
            decoded_event = get_event_data(self._w3.codec, event_abi, log)

            pool_address = to_checksum_address(decoded_event["address"])
            tx_index = decoded_event["transactionIndex"]
            liquidity_block = decoded_event["blockNumber"]
            liquidity = decoded_event["args"]["amount"] * (
                -1 if decoded_event["event"] == "Burn" else 1
            )
            tick_lower = decoded_event["args"]["tickLower"]
            tick_upper = decoded_event["args"]["tickUpper"]

            return pool_address, UniswapV3LiquidityEvent(
                block_number=liquidity_block,
                liquidity=liquidity,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                tx_index=tx_index,
            )

        logger.info(f"Updating snapshot from block {self.newest_block} to {to_block}")

        v3pool = Web3().eth.contract(abi=UNISWAP_V3_POOL_ABI)

        for event in [v3pool.events.Mint, v3pool.events.Burn]:
            logger.info(f"Processing {event.event_name} events")
            event_abi = event._get_event_abi()
            start_block = self.newest_block + 1

            while True:
                end_block = min(to_block, start_block + span - 1)

                _, event_filter_params = construct_event_filter_params(
                    event_abi=event_abi,
                    abi_codec=self._w3.codec,
                    fromBlock=start_block,
                    toBlock=end_block,
                )

                event_logs = self._w3.eth.get_logs(event_filter_params)

                for log in event_logs:
                    pool_address, liquidity_event = _process_log()

                    # skip zero liquidity events
                    if liquidity_event.liquidity == 0:
                        continue

                    self._add_pool_if_missing(pool_address)
                    self._liquidity_events[pool_address].append(liquidity_event)

                if end_block == to_block:
                    break
                else:
                    start_block = end_block + 1

        logger.info(f"Updated snapshot to block {to_block}")
        self.newest_block = to_block

    def get_pool_updates(self, pool_address) -> List[UniswapV3PoolExternalUpdate]:
        try:
            self._liquidity_events[pool_address]
        except KeyError:
            return []
        else:
            # Sort the liquidity events by block, then transaction index
            # before returning them.
            # @dev the V3LiquidityPool helper will reject liquidity events
            # associated with a past block, so they must be processed in
            # chronological order
            sorted_events = sorted(
                self._liquidity_events[pool_address],
                key=lambda event: (event.block_number, event.tx_index),
            )
            self._liquidity_events[pool_address].clear()

            return [
                UniswapV3PoolExternalUpdate(
                    block_number=event.block_number,
                    liquidity_change=(
                        event.liquidity,
                        event.tick_lower,
                        event.tick_upper,
                    ),
                )
                for event in sorted_events
            ]

    def get_tick_bitmap(
        self, pool: Union[ChecksumAddress, V3LiquidityPool]
    ) -> Dict[int, UniswapV3BitmapAtWord]:
        if isinstance(pool, V3LiquidityPool):
            pool_address = pool.address
        elif isinstance(pool, str):
            pool_address = to_checksum_address(pool)
        else:
            raise ValueError(f"Unexpected input for pool: {type(pool)}")

        try:
            return self._liquidity_snapshot[pool_address]["tick_bitmap"]
        except KeyError:
            return {}

    def get_tick_data(
        self, pool: Union[ChecksumAddress, V3LiquidityPool]
    ) -> Dict[int, UniswapV3LiquidityAtTick]:
        if isinstance(pool, V3LiquidityPool):
            pool_address = pool.address
        elif isinstance(pool, str):
            pool_address = to_checksum_address(pool)
        else:
            raise ValueError(f"Unexpected input for pool: {type(pool)}")

        try:
            return self._liquidity_snapshot[pool_address]["tick_data"]
        except KeyError:
            return {}

    def update_snapshot(
        self,
        pool: Union[V3LiquidityPool, ChecksumAddress],
        tick_data: Dict[int, UniswapV3LiquidityAtTick],
        tick_bitmap: Dict[int, UniswapV3BitmapAtWord],
    ) -> None:
        if isinstance(pool, V3LiquidityPool):
            pool_address = pool.address
        elif isinstance(pool, str):
            pool_address = to_checksum_address(pool)
        else:
            raise ValueError(f"Unexpected input for pool: {type(pool)}")

        self._add_pool_if_missing(pool_address)
        self._liquidity_snapshot[pool_address].update(
            {
                "tick_bitmap": tick_bitmap,
            }
        )
        self._liquidity_snapshot[pool_address].update(
            {
                "tick_data": tick_data,
            }
        )
