cd "$(dirname "$0")/../.."

rm -f spot.log

python -m spot.main --optimize-ga \
  --symbols BTCUSDT \
  --backtest-start 2022-03-03 \
  --backtest-end 2026-03-03 \
  --kline-interval 15m \
  --decision-timing on_close \
  --backtest-data-source local \
  --backtest-data-file ./spot/history/bt_20220303_20260303.json.gz \
  --ga-pop-size 24 \
  --ga-generations 12 \
  --ga-workers 4 \
  --ga-mutation-rate 0.18 \
  --ga-crossover-rate 0.75 \
  --ga-elitism-k 3 \
  --ga-top-k-log 5 \
  --ga-max-search-dims 14 \
  --walkforward-train 730 \
  --walkforward-test 90 \
  --walkforward-step 90 \
  --ga-final-test-days 120 \
  --seed 42 \
  --fitness-weights ann_return=1,sharpe=0.8,max_drawdown=1.1,stability=0.8 \
  --ga-search-risk \
  --ga-output-dir ./spot/ga_runs \
  --export-best-params ./spot/best_params_ga.json
