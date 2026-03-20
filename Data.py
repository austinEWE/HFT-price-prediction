import pandas as pd
import numpy as np

import seaborn as sns
import matplotlib.pyplot as plt
import math


class DataLoader:

    def __init__(self):
        ""


    def load_data(self):
        l2 = pd.read_hdf('SOL_L2_Demo.h5', key='df')
        l2 = pd.DataFrame(l2)
        trades = pd.read_hdf('SOL_Trades_Demo.h5', key='trades')
        trades = pd.DataFrame(trades)
        return l2, trades

    @staticmethod
    def inspect_trades(trades: pd.DataFrame):
        print(f"=== Trades shape: {trades.shape}")
        print(f"=== Time range: {trades.index.min()} -> {trades.index.max()}")

        # Check every tid has exactly one B and one A side
        tid_sides = trades.groupby('tid')['side'].apply(set)
        unpaired = tid_sides[tid_sides != {'A', 'B'}]
        print(f"=== All trades paired (B+A per tid): {len(unpaired) == 0}")
        if len(unpaired) > 0:
            print(f"    Unpaired tids: {len(unpaired)}")
            print(unpaired.head())

        # B/A split
        side_counts = trades['side'].value_counts()
        print(f"=== Side counts: B={side_counts.get('B', 0)}, A={side_counts.get('A', 0)}")
        print(f"=== Row count = 2 x unique tids: {len(trades) == 2 * trades['tid'].nunique()}")

        # Taker (crossed) identification
        crossed_side = trades[trades['crossed']]['side'].value_counts()
        print(f"=== Taker (crossed=True) side: B={crossed_side.get('B', 0)}, A={crossed_side.get('A', 0)}")
        print(f"=== Taker fee always >= 0: {(trades.loc[trades['crossed'], 'fee'] >= 0).all()}")

        # Null columns
        null_pct = trades.isnull().mean()
        fully_null = null_pct[null_pct == 1.0].index.tolist()
        if fully_null:
            print(f"=== Fully null columns: {fully_null}")

        # Size outliers
        sz = trades['sz']
        print(f"=== sz: min={sz.min()}, median={sz.median():.2f}, p99={sz.quantile(0.99):.2f}, max={sz.max()}")

    @staticmethod
    def validate_book(l2: pd.DataFrame, n_levels: int = 5) -> pd.DataFrame:
        """Book integrity validation per §2 of hft_data_preprocessing.md.
        Returns l2 with added quality flag columns."""
        flags = pd.DataFrame(index=l2.index)

        # 1. Crossed book: bid_px_0 >= ask_px_0
        flags['crossed_book'] = l2['bid_px_0'] >= l2['ask_px_0']

        # 2. Spread
        spread = l2['ask_px_0'] - l2['bid_px_0']
        flags['non_positive_spread'] = spread <= 0

        # 3. Non-monotonic prices
        bid_mono = pd.Series(False, index=l2.index)
        ask_mono = pd.Series(False, index=l2.index)
        for i in range(n_levels - 1):
            bid_mono |= l2[f'bid_px_{i}'] <= l2[f'bid_px_{i+1}']
            ask_mono |= l2[f'ask_px_{i}'] >= l2[f'ask_px_{i+1}']
        flags['bid_non_monotonic'] = bid_mono
        flags['ask_non_monotonic'] = ask_mono

        # 4a. Zero or negative quantity at TOB (level 0) — hard-invalid
        flags['zero_or_neg_qty_l0'] = (l2['bid_sz_0'] <= 0) | (l2['ask_sz_0'] <= 0)

        # 4b. Zero or negative quantity at deeper levels (1+) — soft flag
        zero_qty_deep = pd.Series(False, index=l2.index)
        for side in ['bid', 'ask']:
            for i in range(1, n_levels):
                zero_qty_deep |= l2[f'{side}_sz_{i}'] <= 0
        flags['zero_or_neg_qty_deep'] = zero_qty_deep

        # 5. Abnormal spread: > 10x rolling median (100-snapshot window)
        spread_median = spread.rolling(100, min_periods=1).median()
        flags['wide_spread_anomaly'] = spread > 10 * spread_median

        # 6. Depth collapse: TOB depth drops to < 1% of previous snapshot
        depth_l0 = l2['bid_sz_0'] + l2['ask_sz_0']
        depth_ratio = depth_l0 / depth_l0.shift(1)
        flags['depth_collapse'] = depth_ratio < 0.01

        # 7. Data gap: snapshot interval > 5s
        time_diff = l2['time'].diff()
        flags['gap'] = time_diff > 5000

        # Combined quality flag
        flags['quality'] = 'ok'
        for col in ['crossed_book', 'non_positive_spread', 'zero_or_neg_qty_l0',
                     'bid_non_monotonic', 'ask_non_monotonic', 'zero_or_neg_qty_deep',
                     'wide_spread_anomaly', 'depth_collapse', 'gap']:
            flags.loc[flags[col], 'quality'] = col

        # Print summary
        print(f"=== Book Validation ({len(l2)} snapshots) ===")
        for col in flags.columns:
            if col == 'quality':
                continue
            count = flags[col].sum()
            if count > 0:
                print(f"  {col}: {count} ({count/len(l2)*100:.2f}%)")
        flagged = (flags['quality'] != 'ok').sum()
        print(f"  Total flagged: {flagged} ({flagged/len(l2)*100:.2f}%)")
        print(f"  Clean: {len(l2) - flagged} ({(len(l2) - flagged)/len(l2)*100:.2f}%)")

        return flags

    @staticmethod
    def get_aggressor_trades(trades: pd.DataFrame) -> pd.DataFrame:
        """Filter to taker (aggressor) side only, add signed size and dollar volume.
        Also removes hard-invalid trades (zero qty, negative price) per §8."""
        agg = trades[trades['crossed']].copy()

        # Hard-invalid removal: zero/negative quantity or price
        invalid = (agg['sz'] <= 0) | (agg['px'] <= 0)
        if invalid.any():
            print(f"  Dropping {invalid.sum()} hard-invalid trades (zero/neg qty or price)")
            agg = agg[~invalid]

        # sign: +1 for buy aggressor, -1 for sell aggressor
        agg['sign'] = np.where(agg['side'] == 'B', 1, -1)
        agg['signed_sz'] = agg['sign'] * agg['sz']
        agg['dollar_vol'] = agg['px'] * agg['sz']
        agg['signed_dollar_vol'] = agg['sign'] * agg['dollar_vol']
        return agg

    @staticmethod
    def temporal_join(l2: pd.DataFrame, agg: pd.DataFrame,
                      l2_gap_ms: int = 3000, trade_gap_ms: int = 30000) -> pd.DataFrame:
        """Join aggressor trades with pre-trade L2 snapshots per §6.

        Uses merge_asof with allow_exact_matches=False so that for each trade
        at time T_k, the matched snapshot satisfies t_snapshot < T_k (strictly
        less than — same-block snapshot already includes the trade's impact).

        Also flags gaps in both L2 and trade streams.

        Args:
            l2: L2 order book snapshots with 'time' column (epoch ms)
            agg: Aggressor trades from get_aggressor_trades() with 'time' column
            l2_gap_ms: L2 gap threshold in ms (default 3000 = 3s)
            trade_gap_ms: Trade gap threshold in ms (default 30000 = 30s)

        Returns:
            Enriched aggressor trades with pre-trade book state and gap flags.
        """
        l2_sorted = l2.sort_values('time').reset_index(drop=True)
        agg_sorted = agg.sort_values('time').reset_index()  # preserve local_time as column

        # Rename L2 columns with pre_ prefix before merge (except 'time')
        l2_renamed = l2_sorted.rename(
            columns={c: f'pre_{c}' for c in l2_sorted.columns if c != 'time'}
        )
        l2_renamed = l2_renamed.rename(columns={'time': 'book_snapshot_time'})

        # merge_asof: backward join, strictly less than
        result = pd.merge_asof(
            agg_sorted,
            l2_renamed,
            left_on='time',
            right_on='book_snapshot_time',
            direction='backward',
            allow_exact_matches=False
        )

        # Derived fields from pre-trade book
        result['pre_mid'] = (result['pre_bid_px_0'] + result['pre_ask_px_0']) / 2
        result['pre_spread'] = result['pre_ask_px_0'] - result['pre_bid_px_0']
        result['pre_depth_l0'] = result['pre_bid_sz_0'] + result['pre_ask_sz_0']

        # Staleness: how old is the pre-trade snapshot relative to trade time
        valid = result['book_snapshot_time'].notna()
        result['book_staleness_ms'] = result['time'] - result['book_snapshot_time']

        # Gap detection — L2: trades falling within a gap period
        l2_time_diff = l2_sorted['time'].diff()
        l2_gap_mask = l2_time_diff > l2_gap_ms
        l2_gap_intervals = l2_sorted.loc[l2_gap_mask, 'time']
        l2_gap_prev = l2_sorted['time'].shift(1).loc[l2_gap_mask]
        result['l2_gap'] = False
        for prev_end, gap_end in zip(l2_gap_prev.values, l2_gap_intervals.values):
            in_gap = (result['time'] > prev_end) & (result['time'] <= gap_end)
            result.loc[in_gap, 'l2_gap'] = True

        # Gap detection — trade stream
        result['trade_gap'] = result['time'].diff() > trade_gap_ms

        # Combined gap flag
        result['gap'] = result['l2_gap'] | result['trade_gap'] | ~valid

        # Restore local_time as index
        result = result.set_index('local_time')

        # Print summary
        print(f"\n=== Temporal Join ===")
        print(f"  Trades joined: {valid.sum()} / {len(result)}")
        print(f"  No pre-trade snapshot: {(~valid).sum()}")
        print(f"  L2 gaps (>{l2_gap_ms}ms): {result['l2_gap'].sum()}")
        print(f"  Trade gaps (>{trade_gap_ms}ms): {result['trade_gap'].sum()}")
        print(f"  Total gap-flagged: {result['gap'].sum()}")
        staleness = result.loc[valid.values, 'book_staleness_ms']
        print(f"  Book staleness (ms): min={staleness.min():.0f}, median={staleness.median():.0f}, "
              f"mean={staleness.mean():.0f}, max={staleness.max():.0f}")

        return result

    @staticmethod
    def enrich_l2_with_trades(l2: pd.DataFrame, agg: pd.DataFrame,
                              book_flags: pd.DataFrame,
                              l2_gap_ms: int = 3000, trade_gap_ms: int = 30000) -> pd.DataFrame:
        """Enrich L2 snapshots with aggregated trade stats and gap flags.

        For each L2 snapshot at time t, aggregates all aggressor trades in the
        interval (t_prev, t]. Gap detection aligned with temporal_join thresholds.

        Args:
            l2: L2 order book snapshots with 'time' column (epoch ms)
            agg: Aggressor trades from get_aggressor_trades()
            book_flags: Output of validate_book() for depth_collapse flags
            l2_gap_ms: L2 gap threshold in ms (default 3000 = 3s)
            trade_gap_ms: Trade gap threshold in ms (default 30000 = 30s)

        Returns:
            Enriched L2 snapshots with trade stats and gap flags.
        """
        l2_sorted = l2.sort_values('time').copy()
        agg_sorted = agg.sort_values('time')

        l2_times = l2_sorted['time'].values
        agg_times = agg_sorted['time'].values
        agg_sz = agg_sorted['sz'].values
        agg_signed = agg_sorted['signed_sz'].values
        agg_dollar = agg_sorted['dollar_vol'].values
        agg_px = agg_sorted['px'].values

        # For each L2 snapshot, find trades in (t_prev, t]
        n = len(l2_sorted)
        trade_volume = np.zeros(n)
        buy_volume = np.zeros(n)
        sell_volume = np.zeros(n)
        net_signed_sz = np.zeros(n)
        n_trades = np.zeros(n, dtype=int)
        trade_vwap = np.full(n, np.nan)

        # Use searchsorted to find trade indices for each interval
        # trade_idx[i] = first trade index with time > l2_times[i-1] (or 0 for first)
        for i in range(n):
            t_start = l2_times[i - 1] if i > 0 else -np.inf
            t_end = l2_times[i]

            # Trades in (t_start, t_end]
            idx_start = np.searchsorted(agg_times, t_start, side='right')
            idx_end = np.searchsorted(agg_times, t_end, side='right')

            if idx_start < idx_end:
                slc = slice(idx_start, idx_end)
                trade_volume[i] = agg_sz[slc].sum()
                buy_volume[i] = agg_sz[slc][agg_signed[slc] > 0].sum()
                sell_volume[i] = agg_sz[slc][agg_signed[slc] < 0].sum()
                net_signed_sz[i] = agg_signed[slc].sum()
                n_trades[i] = idx_end - idx_start
                trade_vwap[i] = np.average(agg_px[slc], weights=agg_sz[slc])

        l2_sorted['trade_volume'] = trade_volume
        l2_sorted['buy_volume'] = buy_volume
        l2_sorted['sell_volume'] = sell_volume
        l2_sorted['net_signed_sz'] = net_signed_sz
        l2_sorted['n_trades'] = n_trades
        l2_sorted['trade_vwap'] = trade_vwap

        # Gap detection — L2 gap (aligned with VIB: 3s default)
        l2_time_diff = pd.Series(l2_times).diff().values
        l2_sorted['l2_gap'] = l2_time_diff > l2_gap_ms

        # Gap detection — trade gap: last trade before this snapshot is > threshold ago
        # Find the most recent trade time before each snapshot
        last_trade_idx = np.searchsorted(agg_times, l2_times, side='right') - 1
        last_trade_time = np.where(last_trade_idx >= 0, agg_times[np.clip(last_trade_idx, 0, len(agg_times) - 1)], np.nan)
        trade_staleness = l2_times - last_trade_time
        l2_sorted['trade_gap'] = trade_staleness > trade_gap_ms

        # Depth collapse from book_flags
        depth_collapse = book_flags['depth_collapse'].reindex(l2_sorted.index).fillna(False).values
        l2_sorted['depth_collapse'] = depth_collapse

        # Combined gap flag
        l2_sorted['gap'] = l2_sorted['l2_gap'] | l2_sorted['trade_gap'] | l2_sorted['depth_collapse']

        # Print summary
        print(f"\n=== Enrich L2 with Trades ===")
        print(f"  L2 snapshots: {n}, Trades matched: {int(n_trades.sum())}")
        print(f"  Snapshots with trades: {(n_trades > 0).sum()} ({(n_trades > 0).sum()/n*100:.1f}%)")
        print(f"  L2 gaps (>{l2_gap_ms}ms): {l2_sorted['l2_gap'].sum()}")
        print(f"  Trade gaps (>{trade_gap_ms}ms): {l2_sorted['trade_gap'].sum()}")
        print(f"  Depth collapses: {l2_sorted['depth_collapse'].sum()}")
        print(f"  Total gap-flagged: {l2_sorted['gap'].sum()}")

        return l2_sorted


