import sys
from Data import *
from Bars import *
from Label_2 import *

# from mlfinlab.sample_weights import get_weights_by_return
from mlfinlab.sample_weights import get_weights_by_return_capped

##

def TB_Labeling(enriched_trades, vib_bars):
    # === Triple Barrier Labeling (Label_2.py — pandas datetime slicing) ===
    enriched_sorted = enriched_trades.sort_values('time')
    trades_df = pd.DataFrame({
        'px': enriched_sorted['px'].values,
        'gap': enriched_sorted['gap'].values,
    }, index=pd.to_datetime(enriched_sorted['time'].values, unit='ms'))

    vol = Labeling.compute_volatility(vib_bars, span=60)
    vb = Labeling.add_vertical_barrier_bars(vib_bars.index, 5)
    vb_times = pd.Series(
        pd.to_datetime(vib_bars.loc[vb.values, 'time_close'].values, unit='ms'),
        index=vb.index,
    )

    events = Labeling.get_events_bars(
        trades=trades_df,
        t_events=vib_bars.index,
        pt_sl=[1.5, 1.5],
        target=vol,
        min_ret=0.0,
        vertical_barrier_times=vb_times,
        num_threads=1,
    )

    labels = Labeling.get_bins_bars(events, trades_df)

    labels.drop(columns=['trgt'], inplace=True)

    enriched_trades = enriched_trades.join(events)
    enriched_trades = enriched_trades.join(labels)

    print("Length after join: ", len(enriched_trades))

    have_nan = enriched_trades[enriched_trades.isna().any(axis=1)]
    print("Have_nan rows: ", have_nan)

    # sample_weights = get_weights_by_return(events.dropna(), trades_df['px'], num_threads=1)
    sample_weights = get_weights_by_return_capped(events.dropna(), trades_df['px'], labels['ret_clip'], num_threads=1)
    enriched_trades['sample_weight'] = sample_weights

    # Convert StringDtype columns to object for HDF5 compatibility
    str_cols = enriched_trades.select_dtypes(include=['string']).columns
    enriched_trades[str_cols] = enriched_trades[str_cols].astype(object)
    enriched_trades.to_hdf(f'{Symb}_VIB_TB_Label.h5', key='df', mode='w', complevel=9, complib='blosc')

    print("TB Labeling Complete!")


if __name__ == '__main__':

    Symb = 'SOL'

    # ── 1. Labeling ──────────────────────────────────────────────────────────
    labeling = True
    label_mode = 'TB'  # 'TB' for Triple Barrier, 'BIN' for Binary

    if labeling:

        dl = DataLoader()
        l2, trades = dl.load_data()
        dl.inspect_trades(trades)

        book_flags = dl.validate_book(l2)

        # Drop hard-invalid rows (crossed book, non-positive spread)
        hard_invalid = book_flags['crossed_book'] | book_flags['non_positive_spread'] | book_flags['zero_or_neg_qty_l0']
        if hard_invalid.any():
            print(f"  Dropping {hard_invalid.sum()} hard-invalid L2 rows")
            l2 = l2[~hard_invalid]
            book_flags = book_flags[~hard_invalid]

        agg = dl.get_aggressor_trades(trades)

        # VIB path: trades -> temporal join with L2 -> VIB
        enriched_trades = dl.temporal_join(l2, agg)
        vib_bars = Bars.create_de_prado_vib(enriched_trades)

        # L2IB path: L2 -> enrich with trades -> L2IB
        # enriched_l2 = dl.enrich_l2_with_trades(l2, agg, book_flags)
        # l2ib_bars = Bars.create_l2_imbalance_bar(enriched_l2)

        # === Triple Barrier Labeling (Label.py — numpy searchsorted) ===
        # enriched_sorted = enriched_trades.sort_values('time')
        # trades_time = enriched_sorted['time'].values.astype(float)
        # trades_px = enriched_sorted['px'].values.astype(float)
        # trades_gap = enriched_sorted['gap'].values
        # vol = Labeling.compute_volatility(vib_bars, span=60)
        # events = Labeling.get_events_bars(
        #     bars=vib_bars, trades_time=trades_time, trades_px=trades_px,
        #     trades_gap=trades_gap, pt_sl=[1.5, 1.5], target=vol,
        #     min_ret=0.0, n_bars_vertical=5, num_threads=1,
        # )
        # labels = Labeling.get_bins_bars(events, trades_time, trades_px)

        if label_mode == 'TB':
            TB_Labeling(enriched_trades, vib_bars)
        # elif label_mode == 'BIN':
        #     Bin_Labeling()
        sys.exit()
    else:
        print("No Labeling Required!")

        df = pd.read_hdf(f'{Symb}_VIB_TB_Label.h5', key="df", mode="r")




    # label_cols = ['bin']
    # events_cols = ['ret', 'ret_clip', 'price_diff', 'trgt', 't1', 'vertical_barrier', 'sample_weight']
    #
    # feature_cols = list(set(df.columns) - set(label_cols) - set(events_cols))
    # events = df[events_cols].dropna().copy()
    # y = df[label_cols].copy()
    # x = df[feature_cols].copy()


    print("complete")