import numpy as np
import pandas as pd

# import importlib.util as _ilu
# import os as _os
# _spec = _ilu.spec_from_file_location(
#     "mlfinlab_multiprocess",
#     _os.path.join(_os.path.dirname(__file__), "mlfinlab", "util", "multiprocess.py"),
# )
# _mod = _ilu.module_from_spec(_spec)
# _spec.loader.exec_module(_mod)
# mp_pandas_obj = _mod.mp_pandas_obj

from mlfinlab.util.multiprocess import mp_pandas_obj


class Labeling:

    @staticmethod
    def compute_volatility(bars, span=60):
        """EWMA volatility of bar-to-bar log returns using open_mid."""
        open_mid = (bars['open_bid_px_0'] + bars['open_ask_px_0']) / 2
        log_ret = np.log(open_mid / open_mid.shift(1))
        return log_ret.ewm(span=span).std()

    @staticmethod
    def add_vertical_barrier_bars(bars_index, n_bars):
        """Bar-count vertical barrier for information bars.

        Args:
            bars_index: Index of bars DataFrame (e.g., DatetimeIndex)
            n_bars: number of bars ahead for vertical barrier

        Returns:
            pd.Series: index=event bar index, values=vertical barrier bar index.
            Excludes events where vertical barrier exceeds dataset.
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
    def apply_pt_sl_on_t1_trades(trades, events, pt_sl, molecule):
        """Triple barrier scanning on raw trades via datetime slicing.

        Mirrors apply_pt_sl_on_t1 from mlfinlab. For each event, slices
        trades[event_time : vertical_barrier], truncates at first gap,
        and finds first barrier touch.

        Args:
            trades: datetime-indexed DataFrame with 'px' and 'gap' columns
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

        for idx, vb in events_['t1'].items():
            path = trades.loc[idx: vb]

            # Skip event if gap in trade path (only label complete, continuous data)
            if len(path) == 0 or path['gap'].any():
                continue

            prices = path['px']
            # Entry = trade price at event time (mirrors mlfinlab's close[loc])
            entry = prices.iloc[0]

            # Barrier levels from entry price
            if pt_sl[0] > 0:
                pt_level = entry * (1 + pt_sl[0] * events_.at[idx, 'trgt'])
                out.at[idx, 'pt'] = prices[prices >= pt_level].index.min()
            if pt_sl[1] > 0:
                sl_level = entry * (1 - pt_sl[1] * events_.at[idx, 'trgt'])
                out.at[idx, 'sl'] = prices[prices <= sl_level].index.min()

        return out

    @staticmethod
    def get_events_bars(trades, t_events, pt_sl, target, min_ret=0.0,
                        vertical_barrier_times=False, num_threads=1, verbose=True):
        """Orchestrator for triple barrier labeling on VIB bars.

        Mirrors get_events from mlfinlab. Entry price is derived from
        trades['px'] at event time (same as mlfinlab's close[loc]).

        Args:
            trades: datetime-indexed DataFrame with 'px' and 'gap' columns
            t_events: DatetimeIndex of events (bar open times)
            pt_sl: [pt_multiple, sl_multiple]
            target: pd.Series of volatility estimates (from compute_volatility)
            min_ret: minimum volatility threshold to label an event
            vertical_barrier_times: pd.Series of vertical barrier datetimes,
                or False to disable vertical barriers
            num_threads: threads for mp_pandas_obj (1=sequential for debugging)
            verbose: progress reporting

        Returns:
            events DataFrame with: t1 (datetime), trgt,
            vertical_barrier (datetime), pt (multiple), sl (multiple).
        """
        # 1) Get target
        target = target.reindex(t_events)
        target = target[target > min_ret]

        # 2) Get vertical barrier
        if vertical_barrier_times is False:
            vertical_barrier_times = pd.Series(pd.NaT, index=t_events)

        # 3) Build events
        events = pd.concat({
            't1': vertical_barrier_times,
            'trgt': target,
        }, axis=1)
        events = events.dropna(subset=['trgt', 't1'])

        if len(events) == 0:
            print("=== Triple Barrier: No valid events after filtering ===")
            return events

        # Symmetric barriers when no side prediction (mirrors mlfinlab)
        pt_sl_ = [pt_sl[0], pt_sl[0]]

        # 4) Apply triple barrier
        first_touch_dates = mp_pandas_obj(
            func=Labeling.apply_pt_sl_on_t1_trades,
            pd_obj=('molecule', events.index),
            num_threads=num_threads,
            trades=trades,
            events=events,
            pt_sl=pt_sl_,
            verbose=verbose,
        )

        # Store original vertical barrier
        events['vertical_barrier'] = events['t1'].copy()

        # Update t1 to earliest barrier touch
        for ind in events.index:
            events.at[ind, 't1'] = first_touch_dates.loc[ind, :].dropna().min()

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
        print(f"  Events: {len(events)}")
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

        Labels: 1=up, -1=down, 0=timeout (vertical).
        """
        labels = pd.Series(0, index=out_df.index, dtype=int)

        if 'vertical_barrier' in events.columns:
            ev = events.reindex(out_df.index)
            horizontal = ev['t1'] < ev['vertical_barrier']
            labels.loc[horizontal & (out_df['ret'] > 0)] = 1
            labels.loc[horizontal & (out_df['ret'] <= 0)] = -1

        out_df['bin'] = labels
        return out_df

    @staticmethod
    def get_bins_bars(events, trades):
        """Compute labels and returns from triple barrier events.

        Mirrors get_bins from mlfinlab.

        Args:
            events: output of get_events_bars
            trades: datetime-indexed DataFrame with 'px' column

        Returns:
            DataFrame with ret, trgt, bin columns.
            bin: {-1: down, 0: timeout, 1: up}
        """
        events_ = events.dropna(subset=['t1'])
        out = pd.DataFrame(index=events_.index)

        # Entry = trade price at event time (mirrors mlfinlab's close[loc])
        entry = trades['px'].asof(events_.index)
        exit_px = events_['t1'].apply(trades['px'].asof)

        out['ret'] = (exit_px.values - entry.values) / entry.values
        out['trgt'] = events_['trgt']

        # Capped returns: clip to barrier levels for PT/SL hits, uncapped for timeout
        pt_cap = events_['pt'] * events_['trgt']
        sl_cap = -events_['sl'] * events_['trgt']
        out['ret_clip'] = out['ret'].clip(lower=sl_cap, upper=pt_cap)

        out = Labeling.barrier_touched_bars(out, events_)

        # Summary
        print(f"\n=== Labels ===")
        counts = out['bin'].value_counts().sort_index()
        label_map = {-1: 'down', 0: 'timeout', 1: 'up'}
        for label, count in counts.items():
            print(f"  {label_map.get(label, label)}: {count} ({count/len(out)*100:.1f}%)")
        if out['ret'].notna().any():
            print(f"  Avg return: {out['ret'].mean():.6f}")
            print(f"  Avg |return|: {out['ret'].abs().mean():.6f}")

        return out
