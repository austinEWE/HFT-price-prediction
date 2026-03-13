import pandas as pd
import numpy as np
#

def get_binary_labeling(t_events, close, num_days=0, num_hours=0, num_minutes=0, num_seconds=0,
                        max_gap_tolerance_pct=0.10, threshold=0.0):
    """
    Generates Binary labels (0, 1) for supervised learning (LogLoss/BCE).

    Args:
        t_events (pd.Index): Timestamps of events (entry times).
        close (pd.Series): Close prices.
        num_days, num_hours...: Fixed time horizon.
        max_gap_tolerance_pct (float): Max gap allowed. Drops sample if data is missing for too long.
        threshold (float): Minimum return to consider it a "1" (Up).
                           Useful to filter out noise/fees.
                           e.g., 0.001 means price must rise 0.1% to be labeled 1.

    Returns:
        pd.DataFrame: Columns ['bin', 'ret', 't1'] indexed by event timestamps.
                      bin: Binary label (0 or 1).
                      ret: Simple percentage return over the horizon.
                      t1: Exit timestamp (nearest close timestamp at horizon end).
    """
    # 1. Define Horizon
    timedelta = pd.Timedelta(days=num_days, hours=num_hours, minutes=num_minutes, seconds=num_seconds)
    target_times = t_events + timedelta

    # 2. Searchsorted (Find closest future index)
    nearest_index = close.index.searchsorted(target_times, side='left')

    # 3. Filter Out-of-Bounds
    valid_mask = nearest_index < close.shape[0]
    nearest_index = nearest_index[valid_mask]
    filtered_events = t_events[valid_mask]
    nearest_timestamps = close.index[nearest_index]

    # 4. GAP FILTER (Critical for clean data)
    actual_duration = nearest_timestamps - filtered_events
    max_duration = timedelta * (1 + max_gap_tolerance_pct)
    gap_mask = actual_duration <= max_duration

    # Apply Gap Filter
    final_events = filtered_events[gap_mask]
    final_exit_times = nearest_timestamps[gap_mask]

    # 5. Calculate Returns
    price_entry = close.loc[final_events].values
    price_exit = close.loc[final_exit_times].values

    # We use Raw Return or Log Return. Here, simple Percentage Return is standard.
    # (Price_Exit / Price_Entry) - 1
    returns = (price_exit / price_entry) - 1

    # 6. Generate Binary Labels (0 vs 1)
    # Label 1 if Return > Threshold (e.g., > 0.0 or > fees)
    # Label 0 if Return <= Threshold
    binary_labels = (returns > threshold).astype(int)

    return pd.DataFrame({
        'bin': binary_labels,
        'ret': returns,
        't1': final_exit_times.values,
    }, index=final_events)


def get_binary_labeling_bars(bars, n_bars=5, threshold=0.0):
    """
    Generates Binary labels (0, 1) using bar-count horizon.

    For each bar, looks n_bars ahead and computes the return from
    entry bar's close price to horizon bar's close price.

    Args:
        bars (pd.DataFrame): VIB bars with 'close' column and DatetimeIndex.
        n_bars (int): Number of bars ahead for the horizon.
        threshold (float): Minimum return to label as 1 (Up).
                           e.g., 0.001 means price must rise 0.1% to be labeled 1.

    Returns:
        pd.DataFrame: Columns ['bin', 'ret', 't1'] indexed by event bar timestamps.
                      bin: Binary label (0 or 1).
                      ret: Simple percentage return over the bar horizon.
                      t1: Exit bar's DatetimeIndex value.
    """
    n_total = len(bars)
    positions = np.arange(n_total)
    horizon_pos = positions + n_bars

    # Filter: only events where horizon bar exists
    valid = horizon_pos < n_total

    entry_idx = bars.index[positions[valid]]
    exit_idx = bars.index[horizon_pos[valid]]

    price_entry = bars.loc[entry_idx, 'close'].values
    price_exit = bars.loc[exit_idx, 'close'].values

    returns = (price_exit / price_entry) - 1
    binary_labels = (returns > threshold).astype(int)

    out = pd.DataFrame({
        'bin': binary_labels,
        'ret': returns,
        't1': exit_idx.values,
    }, index=entry_idx)

    # Summary
    print(f"\n=== Binary Labeling (bar horizon) ===")
    print(f"  Bars: {n_total}, Events: {len(out)}, Horizon: {n_bars} bars")
    print(f"  Label 1 (up): {(out['bin'] == 1).sum()} ({(out['bin'] == 1).mean()*100:.1f}%)")
    print(f"  Label 0 (down/flat): {(out['bin'] == 0).sum()} ({(out['bin'] == 0).mean()*100:.1f}%)")
    print(f"  Avg return: {out['ret'].mean():.6f}")
    print(f"  Avg |return|: {out['ret'].abs().mean():.6f}")

    return out