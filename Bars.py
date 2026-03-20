import pandas as pd
import numpy as np
#

class Bars:

    def __init__(self):
        pass
    @staticmethod
    def create_de_prado_vib(enriched: pd.DataFrame,
                            expected_ticks_init: int = 40,
                            alpha: float = 0.01) -> pd.DataFrame:
        """Create Volume Imbalance Bars (De Prado) from enriched aggressor trades.

        Uses actual aggressor sign (from crossed field) instead of tick rule.
        θ_T = Σ sign_k * sz_k (cumulative signed volume).
        New bar when |θ_T| >= E[T] * |E[signed_vol per tick]|.

        Trades are aggregated by block time (same timestamp) before threshold
        checks to prevent multiple bars at the same timestamp. OHLCV is computed
        from individual trades within each bar.

        Args:
            enriched: Output of DataLoader.temporal_join() with columns:
                      time, px, sz, sign, signed_sz, dollar_vol, gap
            expected_ticks_init: Initial expected blocks per bar
            alpha: EWMA decay (smaller = slower adaptation)

        Returns:
            DataFrame of VIB bars with OHLCV and metadata.
        """
        df = enriched.sort_values('time')

        # Aggregate to block level for VIB logic only (signed volume + gap)
        blocks = df.groupby('time').agg(
            signed_sz=('signed_sz', 'sum'),
            sz=('sz', 'sum'),
            gap=('gap', 'any'),
        ).reset_index()

        block_times = blocks['time'].values
        block_signed = blocks['signed_sz'].values
        block_sizes = blocks['sz'].values
        block_gaps = blocks['gap'].values

        # Pre-extract trade-level arrays for OHLCV aggregation
        trade_times = df['time'].values
        trade_prices = df['px'].values
        trade_sizes = df['sz'].values
        trade_signed = df['signed_sz'].values
        trade_dollar = df['dollar_vol'].values
        trade_staleness = df['book_staleness_ms'].values

        # Pre-extract book columns for bar-open state
        book_cols = [c for c in df.columns if c.startswith('pre_')]
        book_arrays = {c: df[c].values for c in book_cols}

        bars = []
        theta = 0.0
        exp_ticks = float(expected_ticks_init)
        exp_imbalance_per_tick = 0.0

        # Running threshold floor: EWMA of block size (no lookahead)
        ewma_size = block_sizes[0] if len(block_sizes) > 0 else 1.0

        # Bar accumulation
        bar_start_block = 0
        n_blocks = 0

        for i in range(len(blocks)):
            if block_gaps[i]:
                theta = 0.0
                n_blocks = 0
                bar_start_block = i + 1
                continue

            ewma_size = alpha * block_sizes[i] + (1 - alpha) * ewma_size

            theta += block_signed[i]
            n_blocks += 1

            threshold = exp_ticks * abs(exp_imbalance_per_tick)
            threshold = max(threshold, ewma_size * 10)

            if abs(theta) >= threshold and n_blocks > 0:
                # Get trade-level data for this bar's time range
                t_start = block_times[bar_start_block]
                t_end = block_times[i]
                mask = (trade_times >= t_start) & (trade_times <= t_end)
                bar_prices = trade_prices[mask]
                bar_sizes = trade_sizes[mask]
                bar_signed_sz = trade_signed[mask]
                bar_dollar = trade_dollar[mask]

                # First trade index for bar-open book state
                first_idx = np.argmax(mask)

                bar_record = {
                    'time_open': t_start,
                    'time_close': t_end,
                    'duration_ms': t_end - t_start,
                    'open': bar_prices[0],
                    'high': bar_prices.max(),
                    'low': bar_prices.min(),
                    'close': bar_prices[-1],
                    'vwap': np.average(bar_prices, weights=bar_sizes),
                    'volume': bar_sizes.sum(),
                    'dollar_volume': bar_dollar.sum(),
                    'n_blocks': n_blocks,
                    'n_trades': mask.sum(),
                    'imbalance': theta,
                    'buy_volume': bar_sizes[bar_signed_sz > 0].sum(),
                    'sell_volume': bar_sizes[bar_signed_sz < 0].sum(),
                    'max_book_staleness_ms': trade_staleness[mask].max(),
                }

                # Attach bar-open book state (pre_ columns from first trade, renamed to open_)
                for col in book_cols:
                    bar_record[col.replace('pre_', 'open_')] = book_arrays[col][first_idx]

                bars.append(bar_record)

                exp_ticks = alpha * n_blocks + (1 - alpha) * exp_ticks
                current_avg_imbal = theta / n_blocks
                exp_imbalance_per_tick = alpha * current_avg_imbal + (1 - alpha) * exp_imbalance_per_tick

                theta = 0.0
                n_blocks = 0
                bar_start_block = i + 1

        result = pd.DataFrame(bars)
        if not result.empty:
            result['time_open'] = result['time_open'].astype('int64')
            result['time_close'] = result['time_close'].astype('int64')
            result.index = pd.to_datetime(result['time_open'], unit='ms')
            result.index.name = 'datetime'

        n_gap_blocks = block_gaps.sum()
        print(f"\n=== VIB Construction ===")
        print(f"  Input: {len(df)} trades -> {len(blocks)} blocks (gap blocks: {n_gap_blocks})")
        print(f"  Bars created: {len(result)}")
        if not result.empty:
            print(f"  Avg blocks/bar: {result['n_blocks'].mean():.1f}, avg trades/bar: {result['n_trades'].mean():.1f}")
            print(f"  Avg duration: {result['duration_ms'].mean():.0f}ms")
            print(f"  Avg volume/bar: {result['volume'].mean():.2f}")
            print(f"  Zero-duration bars: {(result['duration_ms'] == 0).sum()}")

        return result

    @staticmethod
    def create_l2_imbalance_bar(l2: pd.DataFrame,
                                expected_updates_init: int = 100,
                                alpha: float = 0.01) -> pd.DataFrame:
        """Create L2 Imbalance Bars from enriched L2 snapshots.

        I_t = bid_sz_0 - ask_sz_0 (level 0 depth imbalance).
        θ_T = Σ I_t (cumulative imbalance).
        New bar when |θ_T| >= E[T] * |E[I_t]|.

        Uses MAD-based threshold floor to resist spoofing/outlier depth.

        Args:
            l2: Enriched L2 snapshots from DataLoader.enrich_l2_with_trades()
                with trade stats and combined 'gap' flag.
            expected_updates_init: Initial expected L2 updates per bar.
            alpha: EWMA decay (smaller = slower adaptation).

        Returns:
            DataFrame of L2IB bars with OHLCV (mid-price), book state, and trade stats.
        """
        df = l2.sort_values('time')

        times = df['time'].values
        bid_sz_0 = df['bid_sz_0'].values
        ask_sz_0 = df['ask_sz_0'].values
        bid_px_0 = df['bid_px_0'].values
        ask_px_0 = df['ask_px_0'].values
        mids = (bid_px_0 + ask_px_0) / 2
        gaps = df['gap'].values

        # I_t = bid depth - ask depth at level 0
        imbalances = bid_sz_0 - ask_sz_0

        # Trade stats arrays
        trade_cols = ['trade_volume', 'buy_volume', 'sell_volume', 'net_signed_sz', 'n_trades']
        trade_arrays = {}
        for col in trade_cols:
            trade_arrays[col] = df[col].values if col in df.columns else np.zeros(len(df))

        # Book columns for bar-open state
        book_cols = [c for c in df.columns if c.startswith(('bid_px_', 'bid_sz_', 'ask_px_', 'ask_sz_'))]
        book_arrays = {c: df[c].values for c in book_cols}

        bars = []
        theta = 0.0
        exp_updates = float(expected_updates_init)
        exp_imbalance_per_update = 0.0

        # MAD-based threshold floor: rolling buffer of recent |I_t|
        mad_buffer = []
        mad_window = 200

        bar_start_idx = 0
        n_updates = 0

        for i in range(len(df)):
            if gaps[i]:
                theta = 0.0
                n_updates = 0
                bar_start_idx = i + 1
                continue

            I_t = imbalances[i]

            # Update MAD buffer
            mad_buffer.append(abs(I_t))
            if len(mad_buffer) > mad_window:
                mad_buffer.pop(0)

            theta += I_t
            n_updates += 1

            threshold = exp_updates * abs(exp_imbalance_per_update)
            # MAD-based floor: median(|I_t|) * expected_updates * 0.5
            if len(mad_buffer) >= 10:
                mad_floor = np.median(mad_buffer) * exp_updates * 0.5
                threshold = max(threshold, mad_floor)
            else:
                threshold = max(threshold, abs(I_t) * 10)

            if abs(theta) >= threshold and n_updates > 0:
                bar_mids = mids[bar_start_idx:i + 1]
                first_idx = bar_start_idx

                bar_slice = slice(bar_start_idx, i + 1)

                bar_record = {
                    'time_open': times[bar_start_idx],
                    'time_close': times[i],
                    'duration_ms': times[i] - times[bar_start_idx],
                    'open_mid': mids[bar_start_idx],
                    'high_mid': bar_mids.max(),
                    'low_mid': bar_mids.min(),
                    'close_mid': mids[i],
                    'open_spread': ask_px_0[bar_start_idx] - bid_px_0[bar_start_idx],
                    'close_spread': ask_px_0[i] - bid_px_0[i],
                    'n_updates': n_updates,
                    'imbalance': theta,
                    'mean_imbalance': theta / n_updates,
                }

                # Aggregated trade stats across bar
                for col in trade_cols:
                    bar_record[f'bar_{col}'] = trade_arrays[col][bar_slice].sum()

                # Bar-open book state (all levels)
                for col in book_cols:
                    bar_record[f'open_{col}'] = book_arrays[col][first_idx]

                bars.append(bar_record)

                exp_updates = alpha * n_updates + (1 - alpha) * exp_updates
                current_avg_imbal = theta / n_updates
                exp_imbalance_per_update = alpha * current_avg_imbal + (1 - alpha) * exp_imbalance_per_update

                theta = 0.0
                n_updates = 0
                bar_start_idx = i + 1

        result = pd.DataFrame(bars)
        if not result.empty:
            result['time_open'] = result['time_open'].astype('int64')
            result['time_close'] = result['time_close'].astype('int64')

        n_resets = gaps.sum()
        print(f"\n=== L2 Imbalance Bar Construction ===")
        print(f"  Input: {len(df)} snapshots (reset events: {n_resets})")
        print(f"  Bars created: {len(result)}")
        if not result.empty:
            print(f"  Avg updates/bar: {result['n_updates'].mean():.1f}")
            print(f"  Avg duration: {result['duration_ms'].mean():.0f}ms")
            print(f"  Zero-duration bars: {(result['duration_ms'] == 0).sum()}")

        return result
