import numpy as np
import pandas as pd

import importlib.util as _ilu
import os as _os
_spec = _ilu.spec_from_file_location(
    "mlfinlab_multiprocess",
    _os.path.join(_os.path.dirname(__file__), "mlfinlab", "util", "multiprocess.py"),
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
mp_pandas_obj = _mod.mp_pandas_obj


class Labeling:

    @staticmethod
    def compute_volatility(bars, span=60):
        """EWMA volatility of bar-to-bar log returns using open_mid.

        Args:
            bars: VIB bars DataFrame with open_bid_px_0, open_ask_px_0 columns
            span: EWMA span in number of bars

        Returns:
            pd.Series of volatility estimates, indexed same as bars.
        """
        open_mid = (bars['open_bid_px_0'] + bars['open_ask_px_0']) / 2
        log_ret = np.log(open_mid / open_mid.shift(1))
        vol = log_ret.ewm(span=span).std()
        return vol

    @staticmethod
    def add_vertical_barrier_bars(bars_index, n_bars):
        """Bar-count vertical barrier for information bars.

        Args:
            bars_index: Index of bars DataFrame (e.g., DatetimeIndex)
            n_bars: number of bars ahead for vertical barrier

        Returns:
            pd.Series: index=event bar index value, values=vertical barrier
            bar index value. Excludes events where vertical barrier exceeds
            dataset.
        """
        n_total = len(bars_index)
        positions = np.arange(n_total)
        vb_pos = positions + n_bars
        valid = vb_pos < n_total
        return pd.Series(
            bars_index[vb_pos[valid]].values,
            index=bars_index[positions[valid]]
        )

    @staticmethod
    def apply_pt_sl_on_t1_trades(bars, trades_time, trades_px, trades_gap,
                                  events, pt_sl, molecule):
        """Triple barrier scanning on raw trades.

        For each event in molecule, scans raw trade prices forward from bar open
        to vertical barrier time. Stops at first gap in trade stream.

        Barrier prices (absolute):
            b_up = entry * (1 + pt * trgt)
            b_dn = entry * (1 - sl * trgt)

        Args:
            bars: VIB bars DataFrame
            trades_time: sorted numpy array of trade times (epoch ms, float64)
            trades_px: sorted numpy array of trade prices (float64)
            trades_gap: sorted numpy array of trade gap flags (bool)
            events: DataFrame with t1 (datetime), trgt, entry columns
            pt_sl: [pt_multiple, sl_multiple]
            molecule: subset of event indices to process (for mp_pandas_obj)

        Returns:
            DataFrame with columns [t1, pt, sl] — datetime timestamps.
            NaT where barrier was not touched.
        """
        events_ = events.loc[molecule]
        out = events_[['t1']].copy(deep=True)
        out['pt'] = pd.NaT
        out['sl'] = pd.NaT

        # Pre-compute barrier prices vectorized (like apply_pt_sl_on_t1)
        if pt_sl[0] > 0:
            profit_taking = events_['entry'] * (1 + pt_sl[0] * events_['trgt'])
        else:
            profit_taking = pd.Series(np.inf, index=events_.index)

        if pt_sl[1] > 0:
            stop_loss = events_['entry'] * (1 - pt_sl[1] * events_['trgt'])
        else:
            stop_loss = pd.Series(-np.inf, index=events_.index)

        # Pre-compute epoch ms conversions vectorized
        # Use datetime64[ms] -> int64 to get epoch ms (no division needed)
        t1_ms = events_['t1'].astype('datetime64[ms]').astype('int64').astype(float)
        t_start_ms = bars.loc[events_.index, 'time_open'].astype(float)

        for idx in events_.index:
            # Forward trade window: bar open to vertical barrier
            i_start = np.searchsorted(trades_time, t_start_ms[idx], side='left')
            i_end = np.searchsorted(trades_time, t1_ms[idx], side='right')

            if i_start >= i_end:
                continue

            window_px = trades_px[i_start:i_end]
            window_time = trades_time[i_start:i_end]
            window_gap = trades_gap[i_start:i_end]

            # Truncate at first gap
            gap_indices = np.where(window_gap)[0]
            if len(gap_indices) > 0:
                window_px = window_px[:gap_indices[0]]
                window_time = window_time[:gap_indices[0]]

            if len(window_px) == 0:
                continue

            # Find first upper barrier touch
            if pt_sl[0] > 0:
                pt_touches = np.where(window_px >= profit_taking[idx])[0]
                if len(pt_touches) > 0:
                    out.at[idx, 'pt'] = pd.to_datetime(window_time[pt_touches[0]], unit='ms')

            # Find first lower barrier touch
            if pt_sl[1] > 0:
                sl_touches = np.where(window_px <= stop_loss[idx])[0]
                if len(sl_touches) > 0:
                    out.at[idx, 'sl'] = pd.to_datetime(window_time[sl_touches[0]], unit='ms')

        return out

    @staticmethod
    def get_events_bars(bars, trades_time, trades_px, trades_gap,
                        pt_sl, target, min_ret=0.0, n_bars_vertical=5,
                        num_threads=1, verbose=True):
        """Orchestrator for triple barrier labeling on VIB bars.

        Computes vertical barriers, builds events DataFrame, runs
        apply_pt_sl_on_t1_trades via mp_pandas_obj, and resolves first
        barrier touches.

        Args:
            bars: VIB bars DataFrame
            trades_time: sorted numpy array of trade times (epoch ms, float64)
            trades_px: sorted numpy array of trade prices (float64)
            trades_gap: sorted numpy array of trade gap flags (bool)
            pt_sl: [pt_multiple, sl_multiple]
            target: pd.Series of volatility estimates (from compute_volatility)
            min_ret: minimum volatility threshold to label an event
            n_bars_vertical: bars ahead for vertical barrier
            num_threads: threads for mp_pandas_obj (1=sequential for debugging)
            verbose: progress reporting

        Returns:
            events DataFrame with: t1 (first touch time, datetime), trgt, entry,
            vertical_barrier (datetime), pt (multiple), sl (multiple).
        """
        n_total = len(bars)
        open_mid = (bars['open_bid_px_0'] + bars['open_ask_px_0']) / 2

        # Vertical barriers: n_bars ahead
        vb = Labeling.add_vertical_barrier_bars(bars.index, n_bars_vertical)
        event_indices = vb.index  # DatetimeIndex of event bars

        # Convert vertical barrier bar to datetime (from time_close epoch ms)
        vb_times = pd.to_datetime(bars.loc[vb.values, 'time_close'].values, unit='ms')

        # Build events DataFrame
        events = pd.DataFrame({
            't1': vb_times,
            'trgt': target.reindex(event_indices).values,
            'entry': open_mid.reindex(event_indices).values,
        }, index=event_indices)

        # Filter
        events = events.dropna(subset=['trgt'])
        if min_ret > 0:
            events = events[events['trgt'] > min_ret]

        if len(events) == 0:
            print("=== Triple Barrier: No valid events after filtering ===")
            return events

        pt_sl_ = [pt_sl[0], pt_sl[1]]

        # Apply triple barrier via multiprocessing
        first_touch_dates = mp_pandas_obj(
            func=Labeling.apply_pt_sl_on_t1_trades,
            pd_obj=('molecule', events.index),
            num_threads=num_threads,
            bars=bars,
            trades_time=trades_time,
            trades_px=trades_px,
            trades_gap=trades_gap,
            events=events,
            pt_sl=pt_sl_,
            verbose=verbose,
        )

        # Store original vertical barrier before updating t1
        events['vertical_barrier'] = events['t1'].copy()

        # Update t1 to earliest barrier touch
        for ind in events.index:
            touches = first_touch_dates.loc[ind, :].dropna()
            if len(touches) > 0:
                events.at[ind, 't1'] = touches.min()

        # Store multiples
        events['pt'] = pt_sl[0]
        events['sl'] = pt_sl[1]

        # Summary
        pt_hit = first_touch_dates['pt'].notna()
        sl_hit = first_touch_dates['sl'].notna()
        pt_first = pt_hit & (~sl_hit | (first_touch_dates['pt'] <= first_touch_dates['sl']))
        sl_first = sl_hit & (~pt_hit | (first_touch_dates['sl'] < first_touch_dates['pt']))
        timeout = ~pt_first & ~sl_first

        print(f"\n=== Triple Barrier Labeling ===")
        print(f"  Events: {len(events)} (of {n_total} bars)")
        print(f"  Vertical barrier: {n_bars_vertical} bars ahead")
        print(f"  pt/sl multiples: {pt_sl[0]}/{pt_sl[1]}")
        print(f"  Upper barrier hit first: {pt_first.sum()}")
        print(f"  Lower barrier hit first: {sl_first.sum()}")
        print(f"  Timeout (vertical): {timeout.sum()}")
        if events['trgt'].notna().any():
            print(f"  Avg volatility (trgt): {events['trgt'].mean():.6f}")

        return events

    @staticmethod
    def barrier_touched_bars(out_df, events):
        """Determine triple barrier label.

        Labels: 1=upper barrier (up), 2=lower barrier (down), 0=timeout (vertical).

        Args:
            out_df: DataFrame with 'ret' column (simple returns)
            events: events DataFrame with 't1' and 'vertical_barrier' columns

        Returns:
            out_df with added 'bin' column.
        """
        labels = pd.Series(0, index=out_df.index, dtype=int)

        if 'vertical_barrier' in events.columns:
            ev = events.reindex(out_df.index)
            horizontal = ev['t1'] < ev['vertical_barrier']
            labels.loc[horizontal & (out_df['ret'] > 0)] = 1   # up
            labels.loc[horizontal & (out_df['ret'] <= 0)] = 2  # down

        out_df['bin'] = labels
        return out_df

    @staticmethod
    def get_bins_bars(events, trades_time, trades_px):
        """Compute labels and returns from triple barrier events.

        Args:
            events: output of get_events_bars (with t1, entry, vertical_barrier)
            trades_time: sorted numpy array of trade times (epoch ms, float64)
            trades_px: sorted numpy array of trade prices (float64)

        Returns:
            DataFrame with ret, trgt, bin columns.
            bin: {0: timeout, 1: up, 2: down}
        """
        events_ = events.dropna(subset=['t1'])
        out = pd.DataFrame(index=events_.index)

        entry = events_['entry']

        # Exit price: last trade at or before t1 time
        t1_ms_all = events_['t1'].astype('datetime64[ms]').astype('int64').values.astype(float)
        exit_indices = np.searchsorted(trades_time, t1_ms_all, side='right') - 1
        exit_px = pd.Series(
            np.where(exit_indices >= 0, trades_px[np.clip(exit_indices, 0, len(trades_px) - 1)], np.nan),
            index=events_.index,
        )

        out['ret'] = (exit_px - entry) / entry
        out['trgt'] = events_['trgt']

        # Determine labels
        out = Labeling.barrier_touched_bars(out, events_)

        # Summary
        print(f"\n=== Labels ===")
        counts = out['bin'].value_counts().sort_index()
        label_map = {0: 'timeout', 1: 'up', 2: 'down'}
        for label, count in counts.items():
            print(f"  {label_map.get(label, label)}: {count} ({count/len(out)*100:.1f}%)")
        if out['ret'].notna().any():
            print(f"  Avg return: {out['ret'].mean():.6f}")
            print(f"  Avg |return|: {out['ret'].abs().mean():.6f}")

        return out
